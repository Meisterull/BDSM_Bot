"""
Coach-Quiz 🧠 – das Quiz für die Domina-Seite.

/quiz (Domina): Der Coach stellt EINE Frage – entweder Fachwissen aus dem
kuratierten Themenkatalog (presets/coach_quiz_themen.py, mit ausführlicher
Auflösung = Lerneffekt) oder "Wie gut kennst du deinen Sklaven?" (belegbar
aus Profil/Dossier). Bewertung wie beim Sklaven-Quiz (temp=0), aber im
Coach-Ton und OHNE Punkte. Fachwissens-Auflösungen landen als Langzeit-Wissen
in der knowledge_base (typ=quiz_wissen); FALSCH beantwortete Themen dürfen
nach >=7 Tagen wiederkommen.

Dazu der Coach-Impuls (scheduler.coach_impuls_job): spontane Quiz-Frage oder
eine fertige Wett-Idee zum Weitergeben – Spiegel des Spiel-Impulses.
"""
import logging
import random
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, kategorie_logik, limits_check
from bot.prompts import coach_persona
from bot.prompts.presets.coach_quiz_themen import THEMEN
from bot.messages import t

logger = logging.getLogger(__name__)

ANTEIL_WISSEN = 0.6          # Rest: Sklaven-Wissen
CHANCE_OFFENES_THEMA = 0.35  # falsch beantwortete Themen bevorzugt wiederholen
ANTI_WDH_EINTRAEGE = 15      # so viele letzte Wissens-Themen gelten als verbraucht


async def quiz_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiz nach Rolle verzweigen: Sub → bestehendes Quiz, Domina → Coach-Quiz."""
    chat_id = str(update.effective_chat.id)
    if chat_id == paare.dom_chat_id():
        await start(update, context)
    else:
        from bot.handlers import quiz  # lazy: zirkulären Import vermeiden
        await quiz.start(update, context)


# ---------------------------------------------------------------------------
# Fachwissen
# ---------------------------------------------------------------------------

async def _thema_waehlen() -> dict | None:
    """Thema aus dem Katalog: Basis immer, Vorlieben-Themen nur bei Andockpunkt,
    nichts aus Limits-Kategorien, Anti-Wiederholung über die knowledge_base."""
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    vorlieben_kats = kategorie_logik.kategorien_in_text(
        " ".join(sklave_profil.get("vorlieben", []) or []))
    limit_kats = kategorie_logik.kategorien_in_text(
        " ".join((sklave_profil.get("hard_limits", []) or [])
                 + (domina_profil.get("grenzen", []) or [])))

    pool = []
    for thema in THEMEN:
        kats = set(thema.get("kategorien", []))
        if kats & limit_kats:
            continue
        if kats and not kats & vorlieben_kats:
            continue
        pool.append(thema)
    if not pool:
        return None

    try:
        juengste = await qdrant.get_recent_quiz_wissen("domina", limit=ANTI_WDH_EINTRAEGE)
        verbraucht = {e.get("thema") for e in juengste}
        offene = set(await qdrant.get_offene_quiz_themen("domina"))
    except Exception:
        logger.exception("Coach-Quiz: Anti-Wiederholung nicht ladbar (weiter ohne)")
        verbraucht, offene = set(), set()

    offene_im_pool = [th for th in pool if th["name"] in offene]
    if offene_im_pool and random.random() < CHANCE_OFFENES_THEMA:
        return random.choice(offene_im_pool)
    frisch = [th for th in pool if th["name"] not in verbraucht]
    # Alles verbraucht → ältestes Wissen darf wiederkommen statt zu verstummen.
    return random.choice(frisch or pool)


async def _generiere_wissensfrage(thema: dict) -> tuple[str, str, str] | None:
    """(frage, musterantwort, aufloesung) NUR aus den kuratierten Fakten."""
    fakten = "\n".join(f"- {f}" for f in thema["fakten"])
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    system = (
        coach_persona.fuer_coach_prompt()
        + "\n\nDu stellst deiner Freundin (der dominanten Seite) EINE Wissensfrage "
        "als kleines Lern-Quiz. STRIKT:\n"
        "- Frage und Auflösung MÜSSEN sich vollständig aus den Fakten unten belegen "
        "lassen – erfinde NICHTS dazu.\n"
        "- Keine Ja/Nein-Frage, keine Fangfrage; die Frage prüft EINEN Kernpunkt – "
        "keine Doppelfrage ('… und wie/warum …?').\n"
        "- Die Frage darf die Antwort nicht enthalten oder nahelegen.\n"
        "- Die Auflösung erklärt in 3–6 Sätzen locker und konkret, was man sich "
        "merken sollte – auch das, was über die reine Antwort hinaus wissenswert ist.\n"
        'Antworte NUR als JSON: {"frage": "...", "musterantwort": "knapp", '
        '"aufloesung": "3-6 Saetze"}\nKein Text außerhalb des JSON.'
    )
    roh = await limits_check.generate_mit_limit_retry(
        f"Thema: {thema['name']}\nFakten:\n{fakten}",
        sklave_hard_limits=sklave_profil.get("hard_limits", []),
        domina_grenzen=domina_profil.get("grenzen", []),
        system=system, temperature=0.7, max_tokens=750,
    )
    if not roh:
        return None
    try:
        daten = grok.parse_json(roh)
        frage = (daten.get("frage") or "").strip()
        muster = (daten.get("musterantwort") or "").strip()
        aufloesung = (daten.get("aufloesung") or "").strip()
        if not frage or not muster or not aufloesung:
            raise ValueError("Frage/Musterantwort/Auflösung leer")
        return frage, muster, aufloesung
    except Exception:
        logger.exception("Coach-Quiz: Wissensfrage nicht parsebar")
        return None


# ---------------------------------------------------------------------------
# Sklaven-Wissen
# ---------------------------------------------------------------------------

async def _sklave_kontext() -> str:
    """Belegbare Fakten über den Sklaven als Prompt-Block (Spiegel des
    Sklave-Quiz). Bewusst inkl. seiner Grenzen: dass die Domina die kennt,
    ist Sicherheits-Wissen – darum hier KEIN limits_check."""
    profil = await qdrant.get_user_profile("sklave") or {}
    teile = []
    for feld, label in (("vorlieben", "Seine Vorlieben"), ("hard_limits", "Seine Grenzen"),
                        ("persoenlichkeit_tags", "Persönlichkeit"),
                        ("offene_faeden", "Offene Fäden")):
        wert = profil.get(feld)
        if isinstance(wert, list):
            # DIV4-Analogon (Live-Befund 06.09., wie bei den Tiny-Tasks): die
            # komplette Vorlieben-Liste ankert die Fragen auf denselben
            # Top-Einträgen – subsampeln variiert den Fokus pro Quiz.
            # Grenzen bleiben IMMER vollständig (Sicherheits-Wissen).
            if feld == "vorlieben" and len(wert) > 8:
                wert = random.sample(wert, 8)
            wert = ", ".join(str(w) for w in wert)
        if wert:
            teile.append(f"- {label}: {wert}")
    dossier = (profil.get("dossier") or "").strip()
    if dossier:
        teile.append(f"- Dossier über ihn:\n{dossier[:1200]}")
    return "\n".join(teile)


async def _generiere_sklavenfrage(chat_id: str, kontext: str) -> tuple[str, str] | None:
    s = state.get(chat_id)
    letzte = s.get("coach_quiz_letzte_fragen", [])
    system = (
        coach_persona.fuer_coach_prompt()
        + "\n\nDu stellst deiner Freundin (der dominanten Seite) EINE Quizfrage: wie "
        "gut kennt sie ihren Sub wirklich? STRIKT:\n"
        "- Die Frage MUSS aus den Daten unten eindeutig beantwortbar sein – erfinde "
        "NICHTS, was dort nicht steht.\n"
        "- Keine Ja/Nein-Frage, keine Fangfrage; GENAU EINE Frage zu EINEM Kernpunkt – "
        "keine Doppelfrage ('… und wie/warum …?').\n"
        "- Die Frage darf die Antwort nicht vorwegnehmen: zitiere in der Frage keine "
        "Formulierung aus den Daten, die selbst die gesuchte Antwort ist.\n"
        + ("- NICHT diese kürzlich gestellten Fragen wiederholen: "
           + " | ".join(letzte) + "\n" if letzte else "")
        + 'Antworte NUR als JSON: {"frage": "...", "antwort": "knappe Musterantwort"}\n'
        "Kein Text außerhalb des JSON."
    )
    try:
        roh = await grok.simple(f"Belegbare Daten:\n{kontext}", system=system,
                                temperature=0.7, max_tokens=500)
        daten = grok.parse_json(roh)
        frage = (daten.get("frage") or "").strip()
        antwort = (daten.get("antwort") or "").strip()
        if not frage or not antwort:
            raise ValueError("Frage/Antwort leer")
    except Exception:
        logger.exception("Coach-Quiz: Sklavenfrage-Generierung fehlgeschlagen")
        return None
    s["coach_quiz_letzte_fragen"] = (letzte + [frage])[-5:]
    return frage, antwort


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

def _scharf_schalten(chat_id: str, typ: str, frage: str, muster: str,
                     aufloesung: str, thema: str) -> None:
    s = state.get(chat_id)
    s["coach_quiz_typ"] = typ
    s["coach_quiz_frage"] = frage
    s["coach_quiz_muster"] = muster
    s["coach_quiz_aufloesung"] = aufloesung
    s["coach_quiz_thema"] = thema
    state.set_mode(chat_id, "coach_quiz_antwort")


async def _frage_erzeugen(chat_id: str, typ: str) -> tuple[str, str] | None:
    """Frage generieren + scharf schalten; Rückgabe (typ, fertige Nachricht)."""
    if typ == "wissen":
        thema = await _thema_waehlen()
        if not thema:
            return None
        ergebnis = await _generiere_wissensfrage(thema)
        if not ergebnis:
            return None
        frage, muster, aufloesung = ergebnis
        _scharf_schalten(chat_id, "wissen", frage, muster, aufloesung, thema["name"])
        text = t("COACH_QUIZ_FRAGE_WISSEN",
                 thema=telegram_helper.md_einbett_sicher(thema["name"]),
                 frage=telegram_helper.md_einbett_sicher(frage))
    else:
        kontext = await _sklave_kontext()
        if len(kontext) < 40:
            return ("leer", t("COACH_QUIZ_ZU_WENIG_DATEN"))
        ergebnis = await _generiere_sklavenfrage(chat_id, kontext)
        if not ergebnis:
            return None
        frage, antwort = ergebnis
        _scharf_schalten(chat_id, "sklave", frage, antwort, "", "")
        text = t("COACH_QUIZ_FRAGE_SKLAVE",
                 frage=telegram_helper.md_einbett_sicher(frage))
    return (typ, text)


def _typ_waehlen(args: list[str]) -> str:
    if args:
        wahl = args[0].strip().lower()
        if wahl in ("wissen", "lernen", "knowledge"):
            return "wissen"
        if wahl in ("sklave", "sub", "slave"):
            return "sklave"
    return "wissen" if random.random() < ANTEIL_WISSEN else "sklave"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiz auf der Coach-Seite."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    typ = _typ_waehlen(context.args or [])
    async with telegram_helper.typing_action(context.bot, chat_id):
        ergebnis = await _frage_erzeugen(chat_id, typ)
        # Wunsch-Typ ohne Ergebnis → einmal den anderen Typ probieren, bevor
        # wir mit einer Fehlermeldung aufgeben (z.B. leerer Themen-Pool).
        if ergebnis is None and not context.args:
            anderer = "sklave" if typ == "wissen" else "wissen"
            ergebnis = await _frage_erzeugen(chat_id, anderer)
    if ergebnis is None:
        await update.message.reply_text(t("COACH_QUIZ_FEHLER"))
        return
    _, text = ergebnis
    await telegram_helper.send_domina(context.bot, text, parse_mode="Markdown")


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Antwort der Domina bewerten (mode coach_quiz_antwort)."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()

    if text.lower() in ("/abbrechen", "abbrechen"):
        for k in ("coach_quiz_typ", "coach_quiz_frage", "coach_quiz_muster",
                  "coach_quiz_aufloesung", "coach_quiz_thema"):
            s.pop(k, None)
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    typ = s.pop("coach_quiz_typ", "")
    frage = s.pop("coach_quiz_frage", "")
    muster = s.pop("coach_quiz_muster", "")
    aufloesung = s.pop("coach_quiz_aufloesung", "")
    thema = s.pop("coach_quiz_thema", "")
    state.set_mode(chat_id, "chat")
    if not frage or not muster:
        return

    urteil = "FALSCH"
    try:
        bewertung = await grok.simple(
            f'Frage: "{frage}"\nMusterantwort: "{muster}"\nIhre Antwort: """{text}"""',
            system=("Bewerte, ob ihre Antwort inhaltlich zur Musterantwort passt. "
                    "Sei fair: andere Formulierung mit gleichem Kern = RICHTIG; "
                    "teilweise getroffen = TEILWEISE; daneben = FALSCH. "
                    "Antworte NUR mit einem Wort: RICHTIG, TEILWEISE oder FALSCH."),
            temperature=0,
        )
        wort = (bewertung or "").strip().upper()
        if wort in ("RICHTIG", "TEILWEISE", "FALSCH"):
            urteil = wort
    except Exception:
        logger.exception("Coach-Quiz: Bewertung fehlgeschlagen – werte als FALSCH")

    muster_sicher = telegram_helper.md_einbett_sicher(muster)
    if urteil == "RICHTIG":
        antwort_text = t("COACH_QUIZ_RICHTIG")
    elif urteil == "TEILWEISE":
        antwort_text = t("COACH_QUIZ_TEILWEISE", antwort=muster_sicher)
    else:
        antwort_text = t("COACH_QUIZ_FALSCH", antwort=muster_sicher)
    if typ == "wissen" and aufloesung:
        antwort_text += "\n\n" + t("COACH_QUIZ_AUFLOESUNG",
                                   aufloesung=telegram_helper.md_einbett_sicher(aufloesung))
    await telegram_helper.send_domina(context.bot, antwort_text, parse_mode="Markdown")

    if typ == "wissen":
        try:
            await qdrant.save_quiz_wissen("domina", {
                "thema": thema, "frage": frage, "inhalt": aufloesung or muster,
                "urteil": urteil,
                "status": "offen" if urteil == "FALSCH" else "gelernt",
            })
        except Exception:
            logger.exception("Coach-Quiz: Wissens-Eintrag nicht gespeichert")


# ---------------------------------------------------------------------------
# Coach-Impuls (Scheduler)
# ---------------------------------------------------------------------------

async def sende_spontane_frage(bot) -> bool:
    """Coach-Impuls: spontane Quiz-Frage an die Domina. True nur bei Versand."""
    chat_id = paare.dom_chat_id()
    typ = "wissen" if random.random() < ANTEIL_WISSEN else "sklave"
    ergebnis = await _frage_erzeugen(chat_id, typ)
    if ergebnis is None or ergebnis[0] == "leer":
        return False
    # TOCTOU-Re-Check nach dem LLM-Await (Muster spiel_impuls): im Fenster kann
    # ein Safeword oder ein UI-Flow gekommen sein.
    if state.is_paused() or state.get_mode(chat_id) != "coach_quiz_antwort":
        logger.info("Coach-Impuls-Quiz nach Generierung verworfen – Pause/Mode geändert.")
        return False
    _, text = ergebnis
    try:
        await telegram_helper.send_domina(
            bot, t("COACH_IMPULS_QUIZ_PREFIX") + "\n\n" + text, parse_mode="Markdown")
    except Exception:
        # Nicht zugestellte Frage nicht scharf lassen (Muster Spiel-Impuls).
        state.set_mode(chat_id, "chat")
        for k in ("coach_quiz_typ", "coach_quiz_frage", "coach_quiz_muster",
                  "coach_quiz_aufloesung", "coach_quiz_thema"):
            state.get(chat_id).pop(k, None)
        raise
    return True


# Nachsatz-Detektor (Live-Befund 07.09.): trotz „kein Vorwort, keine Erklärung"
# hängte das Modell eine Rückfrage auf einen Flow an, den es nicht gibt
# („… so abschicken oder noch was ändern?") – direkt darüber steht schon die
# Template-Zeile „Nur eine Idee – gib sie weiter …". Deterministisch
# abschneiden statt auf die Prompt-Regel hoffen (Lernmuster Detektor > Regel).
_NACHSATZ_RE = re.compile(
    r"(abschick|weitergeb|weiterleit|so\s+(rüber|raus)|ändern|anpass|soll ich|"
    r"willst du (das|es|die|sie) so|passt (das|dir das|dir so)|was meinst du|"
    r"was sagst du|einverstanden)", re.IGNORECASE)
_LETZTER_FRAGESATZ_RE = re.compile(r"(?:^|(?<=[.!?…])\s+)([^.!?…\n]*\?)\s*$")


def _nachsatz_entfernen(text: str) -> str:
    """Schneidet abschließende Rückfrage-Sätze ab (nur wenn davor noch Text steht)."""
    t = (text or "").strip()
    while True:
        m = _LETZTER_FRAGESATZ_RE.search(t)
        if not m or m.start(1) == 0 or not _NACHSATZ_RE.search(m.group(1)):
            return t
        t = t[:m.start()].rstrip()


async def sende_wett_idee(bot) -> bool:
    """Coach-Impuls: fertige Wett-Idee im Coach-Ton – zum Weitergeben an den
    Sub, bewusst OHNE eigenen Bestätigungs-Flow. True nur bei Versand.

    Dieselben Bausteine wie der Tiny-Task (Live-Befund 07.09.: die erste
    Wett-Idee verdrehte die Rollen – Vorlieben in Ich-Perspektive ohne
    Rollen-Rahmen – und garnierte mit den Zutaten des Tipps vom selben Abend):
    Rollen-Frame + Richtungs-Regel (Prompt), Vorlieben-Subsample,
    Zutaten-Sperrliste, Schablonen-Detektor mit einem Retry, Nachsatz-Schnitt."""
    # lazy: der Scheduler importiert coach_quiz selbst lazy (zirkulärer Import)
    from bot.scheduler.followup import _formel_verstoesse, _verbrauchte_zutaten
    from bot.prompts import followup as followup_prompts, rollen

    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    # Subsample wie beim Tiny-Task (DIV4-Analogon): die volle Liste ankert auf
    # denselben Top-Einträgen. Zeilen wörtlich, Original-Reihenfolge erhalten.
    vorlieben = list(sklave_profil.get("vorlieben", []) or [])
    if len(vorlieben) > 8:
        auswahl = set(random.sample(range(len(vorlieben)), 8))
        vorlieben = [v for i, v in enumerate(vorlieben) if i in auswahl]
    interessen = list(domina_profil.get("interessen", []) or [])
    if len(interessen) > 6:
        interessen = random.sample(interessen, 6)
    try:
        _, _, volltexte = await qdrant.get_recent_tiny_tasks(limit=3)
    except Exception:
        logger.exception("Wett-Idee: letzte Vorschläge nicht lesbar – ohne Zutaten-Sperre")
        volltexte = []
    zutaten = _verbrauchte_zutaten(list(volltexte or [])[:3], [], None)

    sk_hl = sklave_profil.get("hard_limits", []) or []
    do_gr = domina_profil.get("grenzen", []) or []
    system, prompt = followup_prompts.wett_idee(
        sklave_vorlieben=vorlieben, sklave_hard_limits=sk_hl,
        domina_interessen=interessen, verbrauchte_zutaten=zutaten,
    )

    async def _generiere(p: str) -> str | None:
        return await limits_check.generate_mit_limit_retry(
            p, sklave_hard_limits=sk_hl, domina_grenzen=do_gr,
            system=system, temperature=0.9, max_tokens=400,
        )

    idee = await _generiere(prompt)
    if not idee:
        return False
    idee = _nachsatz_entfernen(idee)
    funde = _formel_verstoesse(idee)
    if funde:
        logger.info("Wett-Idee nutzt verbotene Schablonen (%s) – generiere einmal neu.",
                    "; ".join(funde))
        s = rollen.sub()
        neu = await _generiere(
            prompt + "\n\nACHTUNG: Dein letzter Entwurf hat diese VERBOTENEN Schablonen "
            "benutzt: " + "; ".join(funde) + ". Formuliere die Wette neu – der Inhalt "
            "darf bleiben, aber ohne Profil-Abgleich ('passt zu …', 'genau "
            f"{s['poss']}e …', '…, den du magst') und ohne Kommentar, wovon du dich absetzt."
        )
        if neu:
            neu = _nachsatz_entfernen(neu)
            if len(_formel_verstoesse(neu)) <= len(funde):
                idee = neu
    chat_id = paare.dom_chat_id()
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("Coach-Impuls-Wette nach Generierung verworfen – Pause/Mode geändert.")
        return False
    await telegram_helper.send_domina(
        bot, t("COACH_IMPULS_WETTE", idee=telegram_helper.md_einbett_sicher(idee)),
        parse_mode="Markdown")
    return True
