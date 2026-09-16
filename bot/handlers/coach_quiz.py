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
eine fertige Wett-Idee zum Weitergeben – Spiegel des Spiel-Impulses – und
/wette (Dom-Seite): Wettvorschlag auf Abruf, optional mit Thema. Annahme,
Ablehnung und Urteil laufen in handlers/waehrung.
"""
import logging
import random
import re
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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


async def wette_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wette nach Rolle verzweigen: Dom-Seite → Wettvorschlag auf Abruf,
    Sub → Punkte-Wette auf die nächste Aufgabe (handlers/wette)."""
    if str(update.effective_chat.id) == paare.dom_chat_id():
        await wette_abruf(update, context)
    else:
        from bot.handlers import wette  # lazy: zirkulären Import vermeiden
        await wette.show(update, context)


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
    r"willst du|möchtest du|magst du|sollen wir|passt (das|dir das|dir so)|was meinst du|"
    r"was sagst du|einverstanden|passt\b|anmacht|gefällt|\boder\s*\?\s*$)", re.IGNORECASE)
_LETZTER_FRAGESATZ_RE = re.compile(r"(?:^|(?<=[.!?…])\s+)([^.!?…\n]*\?)\s*$")


# Coach-Kommentar ÜBER die Wette am Ende („Kurz, klar und in 1–2 Tagen
# entscheidbar.") – Live-Render 16.09.: kein Fragesatz, also vom Nachsatz-Schnitt
# nicht erfasst. Nur ein kurzer letzter Satz/Absatz mit Meta-Vokabular fällt.
_META_SCHLUSS_RE = re.compile(
    r"(entscheidbar|messbar|kurz(?:,| und) klar|zum weitergeben|abschicken|anpassen"
    r"|so passt|fertig zum|meta|coach|richtung)", re.I)


def _meta_schluss_entfernen(text: str) -> str:
    """Schneidet einen abschließenden Kommentar-Satz (≤ 120 Zeichen, Meta-Vokabular)
    ab – nur wenn davor noch Text steht."""
    t = (text or "").strip()
    m = re.search(r"(?:^|(?<=[.!?…])\s+|\n\s*)([^.!?…\n]{1,118}[.!…]?)\s*$", t)
    if not m or m.start(1) == 0:
        return t
    letzter = m.group(1)
    if _META_SCHLUSS_RE.search(letzter) and not re.search(r"gewinn|verlier|wette:", letzter, re.I):
        return t[:m.start(1)].rstrip()
    return t


# Vorwort vor der eigentlichen Idee („Hier ist ein konkreter Wettvorschlag, den
# du ihm machen kannst:" als eigene Zeile, „Wie wär's mit der Wette: Er muss …"
# inline) – Live-Proben 16.09. trotz „Kein Vorwort", das Reasoning-Modell
# beginnt in ~der Hälfte der Fälle so und der Einstiegs-Detektor verwarf sie.
_VORWORT_RE = re.compile(r"^\s*([^\n]{1,140}:[*_]*)\s*\n+(?=\S)")
_VORWORT_INLINE_RE = re.compile(
    r"^\s*((?:wie\s+w[äa]r[’'`]?s|hier|mein|meine|kleine|eine|also)\b[^:\n]{0,50}"
    r"(?:wette|vorschlag|idee)[^:\n]{0,15}:)\s*(?=\S)", re.I)


def _vorwort_entfernen(text: str) -> str:
    """Schneidet eine kurze Einleitung bis zum Doppelpunkt ab – als eigene Zeile
    oder inline vor dem ersten Satz der Idee."""
    t = (text or "").strip()
    for rx in (_VORWORT_RE, _VORWORT_INLINE_RE):
        m = rx.match(t)
        if m and not re.search(r"gewinn|verlier", m.group(1), re.I) and t[m.end():].strip():
            rest = t[m.end():].strip()
            t = rest[0].upper() + rest[1:]
    return t


def _nachsatz_entfernen(text: str) -> str:
    """Schneidet abschließende Rückfrage-Sätze ab (nur wenn davor noch Text steht)."""
    t = (text or "").strip()
    while True:
        m = _LETZTER_FRAGESATZ_RE.search(t)
        if not m or m.start(1) == 0 or not _NACHSATZ_RE.search(m.group(1)):
            return t
        t = t[:m.start()].rstrip()


# ---------------------------------------------------------------------------
# Wett-Idee 🎲 – Coach-Impuls mit Ein-Tipp-Weitergabe (📨 / 🎲)
# ---------------------------------------------------------------------------

WETT_IDEE_MAX_ZEICHEN = 600   # Prompt verlangt 2–4 Sätze; Live 15.09.: 1100 Zeichen Herrin-Nachricht
WETT_IDEE_MAX_NEU = 3         # „Andere Idee"-Würfe pro Vorschlag
# Geparkter Vorschlag im Dom-Profil (Qdrant statt Chat-State: die Buttons überleben
# einen Neustart – die Dom-Seite tippt oft erst Stunden später):
#   {"kennung", "text", "neu", "schwerpunkt"}  bzw. nach 📨 {"gesendet": kennung}
FELD_WETT_IDEE = "wett_idee_offen"
# Nur GEPAARTE Anführungszeichen um einen langen Block (Live 16.09. 20:34: die alte
# Form nahm das schließende Zeichen eines zitierten Einzelworts wie „später“ als
# Öffner und verwarf jede Idee, nach der noch 120 Zeichen folgten).
_ZITAT_BLOCK_RE = re.compile(r"„[^“”\"]{120,}[“”\"]|\"[^\"]{120,}\"|»[^«]{120,}«|“[^”]{120,}”")


def _idee_verstoesse(idee: str) -> list[str]:
    """Deterministische Drift-Prüfung (Live 15.09.2026: statt einer Idee an die
    Dom-Seite kam eine fertige, 1100 Zeichen lange Herrin-Nachricht an den Sub
    in Anführungszeichen, mit Vokativ-Anrede und erfundener Mechanik – die
    Schablonen-/Nachsatz-Detektoren sahen nichts). Leer = brauchbar."""
    text = (idee or "").strip()
    funde: list[str] = []
    if len(text) > WETT_IDEE_MAX_ZEICHEN:
        funde.append(f"zu lang ({len(text)} Zeichen, höchstens {WETT_IDEE_MAX_ZEICHEN})")
    if re.match(r"^[„“\"»]", text) or _ZITAT_BLOCK_RE.search(text):
        funde.append("fertiger Nachrichtentext in Anführungszeichen statt einer Idee")
    try:
        from bot.services import persona_config
        anrede = (persona_config.sklave_anrede() or "").strip()
    except Exception:
        anrede = ""
    if anrede and re.search(rf"(?:^|[\n„“\"»])\s*{re.escape(anrede)}\s*[,!]", text, re.I):
        funde.append(f"Anrede ‚{anrede}‘ – der Text spricht den Sub an statt die Dom-Seite")
    # Live-Probe 16.09.: das Aussetzen des Safewords wurde als Einsatz angeboten.
    # Das Safeword ist nie Teil einer Wette – jede Erwähnung ist ein Mangel.
    if re.search(r"safe\s*-?\s*word", text, re.I):
        funde.append("Safeword als Teil der Wette – das Safeword gilt immer und ist nie Einsatz")
    return funde


def _wett_idee_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WETTIDEE_SENDEN"), callback_data=f"wettidee:senden:{kennung}"),
        InlineKeyboardButton(t("BUTTON_WETTIDEE_NEU"), callback_data=f"wettidee:neu:{kennung}"),
    ]])


async def _wett_idee_zustellen(bot, idee: str, neu_zaehler: int, schwerpunkt: str = "") -> str:
    """Idee im Dom-Profil parken (callback_data trägt nur die Kennung) und mit
    📨/🎲 an die Dom-Seite schicken. Der Schwerpunkt fährt mit, damit 🎲
    „Andere Idee" beim Wunsch-Thema bleibt."""
    kennung = uuid.uuid4().hex[:8]
    await qdrant.patch_profile_fields("domina", {FELD_WETT_IDEE: {
        "kennung": kennung, "text": idee, "neu": neu_zaehler, "schwerpunkt": schwerpunkt}})
    await telegram_helper.send_domina(
        bot, t("COACH_IMPULS_WETTE", idee=telegram_helper.md_einbett_sicher(idee)),
        parse_mode="Markdown", reply_markup=_wett_idee_buttons(kennung))
    return kennung


def _schwerpunkt_treffer(text: str, schwerpunkt: str) -> bool:
    """Schreibweisen-tolerant: „Filmabend" trifft auch „Film-Abend"/„Film abend"."""
    def norm(x: str) -> str:
        return re.sub(r"[\s\-_]", "", (x or "").lower())
    return bool(schwerpunkt) and norm(schwerpunkt) in norm(text)


def _auswahl_mit_schwerpunkt(eintraege: list, anzahl: int, schwerpunkt: str) -> tuple[list, list]:
    """Subsample auf `anzahl` Einträge, Schwerpunkt-Treffer immer dabei (sonst
    könnte das Zufalls-Subsample genau die Wunsch-Vorliebe herauswürfeln).
    Original-Reihenfolge bleibt. Rückgabe: (Auswahl, Treffer)."""
    treffer_idx = [i for i, e in enumerate(eintraege) if _schwerpunkt_treffer(e, schwerpunkt)]
    rest_idx = [i for i in range(len(eintraege)) if i not in treffer_idx]
    frei = max(0, anzahl - len(treffer_idx))
    if len(rest_idx) > frei:
        rest_idx = random.sample(rest_idx, frei)
    behalten = set(treffer_idx) | set(rest_idx)
    return ([e for i, e in enumerate(eintraege) if i in behalten],
            [eintraege[i] for i in treffer_idx])


async def _wett_idee_generieren(schwerpunkt: str = "") -> str | None:
    """Wett-Idee im Coach-Ton. Dieselben Bausteine wie der Tiny-Task (Live-Befund
    07.09.: die erste Wett-Idee verdrehte die Rollen – Vorlieben in Ich-Perspektive
    ohne Rollen-Rahmen – und garnierte mit den Zutaten des Tipps vom selben Abend):
    Rollen-Frame + Richtungs-Regel (Prompt), Vorlieben-Subsample, Zutaten-Sperrliste,
    Schablonen-Detektor + Drift-Prüfung (_idee_verstoesse) mit genau einem Retry,
    Nachsatz-Schnitt. None = nichts Brauchbares (der Impuls fällt dann auf Quiz)."""
    # lazy: der Scheduler importiert coach_quiz selbst lazy (zirkulärer Import)
    from bot.scheduler.followup import _formel_verstoesse, _verbrauchte_zutaten
    from bot.prompts import followup as followup_prompts, rollen

    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    # Subsample wie beim Tiny-Task (DIV4-Analogon): die volle Liste ankert auf
    # denselben Top-Einträgen. Zeilen wörtlich, Original-Reihenfolge erhalten.
    vorlieben, vorlieben_treffer = _auswahl_mit_schwerpunkt(
        list(sklave_profil.get("vorlieben", []) or []), 8, schwerpunkt)
    interessen, interessen_treffer = _auswahl_mit_schwerpunkt(
        list(domina_profil.get("interessen", []) or []), 6, schwerpunkt)
    try:
        _, _, volltexte = await qdrant.get_recent_tiny_tasks(limit=3)
    except Exception:
        logger.exception("Wett-Idee: letzte Vorschläge nicht lesbar – ohne Zutaten-Sperre")
        volltexte = []
    # Schwerpunkt-Zeilen befreien ihre Zutaten wie eine Kombi-Vorliebe – sonst
    # sperrt ein Tiny-Task vom selben Tag genau das gewünschte Thema.
    befreit = (vorlieben_treffer + interessen_treffer + [schwerpunkt]) if schwerpunkt else None
    zutaten = _verbrauchte_zutaten(list(volltexte or [])[:3], [], befreit)

    sk_hl = sklave_profil.get("hard_limits", []) or []
    do_gr = domina_profil.get("grenzen", []) or []
    system, prompt = followup_prompts.wett_idee(
        sklave_vorlieben=vorlieben, sklave_hard_limits=sk_hl,
        domina_interessen=interessen, verbrauchte_zutaten=zutaten,
        schwerpunkt=schwerpunkt,
    )

    async def _generiere(p: str) -> str | None:
        return await limits_check.generate_mit_limit_retry(
            p, sklave_hard_limits=sk_hl, domina_grenzen=do_gr,
            system=system, temperature=0.9, max_tokens=400,
            # Reasoning (Live-Proben 16.09.): das schnelle Modell drehte die Rolle
            # einer Vorliebe um, bot das Aussetzen des Safewords als Einsatz an und
            # verwechselte Sieger/Verlierer; das Reasoning-Modell blieb in 4/4 stimmig.
            reasoning=True,
        )

    idee = await _generiere(prompt)
    if not idee:
        return None
    idee = _meta_schluss_entfernen(_nachsatz_entfernen(_vorwort_entfernen(idee)))
    funde = _formel_verstoesse(idee) + _idee_verstoesse(idee)
    if funde:
        logger.info("Wett-Idee mit Mängeln (%s) – generiere einmal neu.", "; ".join(funde))
        s = rollen.sub()
        neu = await _generiere(
            prompt + "\n\nACHTUNG: Dein letzter Entwurf hatte diese Mängel: " + "; ".join(funde)
            + ". Formuliere die Wette neu – der Inhalt darf bleiben, aber als kurze IDEE an sie "
            "(kein fertiger Nachrichtentext, keine Anführungszeichen, keine Anrede an "
            f"{s['label_akk']}), ohne Profil-Abgleich ('passt zu …', 'genau {s['poss']}e …', "
            "'…, den du magst') und ohne Kommentar, wovon du dich absetzt."
        )
        if neu:
            neu = _meta_schluss_entfernen(_nachsatz_entfernen(_vorwort_entfernen(neu)))
            if len(_formel_verstoesse(neu) + _idee_verstoesse(neu)) <= len(funde):
                idee = neu
    rest = _idee_verstoesse(idee)
    if rest:
        logger.info("Wett-Idee auch nach Retry unbrauchbar (%s) – kein Versand.", "; ".join(rest))
        logger.debug("Verworfene Wett-Idee: %r", idee)
        return None
    return idee


async def sende_wett_idee(bot, schwerpunkt: str = "") -> bool:
    """Coach-Impuls: Wettvorschlag im Coach-Ton mit Ein-Tipp-Weitergabe – 📨 schickt
    ihn in der Herrin-Stimme an den Sub, 🎲 würfelt eine andere Idee. True nur bei
    Versand. (Bis 15.09.2026 bewusst ohne Flow – Live hat die Dom-Seite die reine
    Text-Idee nie als Wettvorschlag wahrgenommen, geschweige denn abgetippt.)
    schwerpunkt: Thema eines Wunsch-Wettvorschlags (s. coach_impuls_job)."""
    # Währung ⭐: höchstens eine offene Herrin-Wette (angeboten, laufend oder
    # abgelehnt mit ausstehender Strafwahl) – solange fällt der Impuls aus.
    from bot.handlers import waehrung as waehrung_h
    if waehrung_h.wette_offen(await qdrant.get_user_profile("sklave") or {}):
        logger.info("Wett-Idee übersprungen – es ist noch eine Herrin-Wette offen.")
        return False
    idee = await _wett_idee_generieren(schwerpunkt)
    if not idee:
        return False
    chat_id = paare.dom_chat_id()
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("Coach-Impuls-Wette nach Generierung verworfen – Pause/Mode geändert.")
        return False
    await _wett_idee_zustellen(bot, idee, 0, schwerpunkt)
    return True


async def wette_abruf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wette [Thema] der Dom-Seite: Wettvorschlag auf Abruf. Die Generierung
    (Reasoning, 30–70 s) läuft im Hintergrund, der Vorschlag kommt mit 📨/🎲."""
    from bot.handlers import waehrung as waehrung_h
    schwerpunkt = " ".join(context.args or []).strip()[:60]
    sklave = await qdrant.get_user_profile("sklave") or {}
    if waehrung_h.wette_offen(sklave):
        await update.message.reply_text(t("WETTE_ABRUF_OFFEN", lage=waehrung_h.lage_text(sklave)))
        return
    await update.message.reply_text(t(
        "WETTE_ABRUF_DENKT",
        thema=t("WETTE_ABRUF_THEMA", thema=schwerpunkt) if schwerpunkt else ""))
    waehrung_h.im_hintergrund(wett_vorschlag_auf_abruf(context.bot, schwerpunkt))


async def wett_vorschlag_auf_abruf(bot, schwerpunkt: str = "") -> bool:
    """Gemeinsamer Kern von /wette und dem Mini-App-Knopf: Idee erzeugen und mit
    📨/🎲 zustellen; bei Misserfolg eine kurze Meldung an die Dom-Seite."""
    from bot.handlers import waehrung as waehrung_h
    idee = await _wett_idee_generieren(schwerpunkt)
    if state.is_paused():
        return False
    if waehrung_h.wette_offen(await qdrant.get_user_profile("sklave") or {}):
        return False  # während der Generierung kam eine andere Wette dazwischen
    if not idee:
        await telegram_helper.send_domina(bot, t("WETTE_ABRUF_FEHLER"))
        return False
    await _wett_idee_zustellen(bot, idee, 0, schwerpunkt)
    logger.info("Wettvorschlag auf Abruf zugestellt (Schwerpunkt: %s).", schwerpunkt or "–")
    return True


async def callback_wett_idee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons unter dem Wettvorschlag (Rolle via main._callback_gate: Dom).
    senden → Herrin-Ansage an den Sub (Text + Voice, Weg der Sprachnachricht);
    neu → nächste Idee (höchstens WETT_IDEE_MAX_NEU pro Vorschlag).
    Bei Fehlern bleiben Buttons und State stehen – sie kann es gleich nochmal tippen."""
    query = update.callback_query
    try:
        _, action, kennung = (query.data or "").split(":", 2)
    except ValueError:
        await query.answer()
        return
    offen = (await qdrant.get_user_profile("domina") or {}).get(FELD_WETT_IDEE) or {}
    # Doppel-Tap (Live 16.09.): Senden dauert ~9 s (LLM + Stimme), die Buttons fallen
    # erst danach – der zweite Tipp kam als „nicht mehr aktuell" an. Still schlucken.
    if kennung == offen.get("gesendet"):
        await query.answer()
        return
    # Sofort sichtbare Rückmeldung als Toast statt stummer Wartezeit
    # (🎲 läuft übers Reasoning-Modell, bis ~1 Min).
    toast = {"neu": "COACH_WETTIDEE_DENKT", "senden": "COACH_WETTIDEE_SCHICKT"}.get(action)
    await query.answer(t(toast) if toast else None)
    if action not in ("senden", "neu"):
        return
    if offen.get("kennung") != kennung or not offen.get("text"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(t("COACH_WETTIDEE_VERALTET"))
        return
    idee = offen["text"]

    if action == "neu":
        zaehler = int(offen.get("neu", 0) or 0)
        if zaehler >= WETT_IDEE_MAX_NEU:
            await query.message.reply_text(t("COACH_WETTIDEE_NEU_LIMIT"))
            return
        schwerpunkt = offen.get("schwerpunkt", "") or ""
        neu = await _wett_idee_generieren(schwerpunkt)
        if not neu:
            await query.message.reply_text(t("COACH_WETTIDEE_FEHLER"))
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        neu_kennung = await _wett_idee_zustellen(context.bot, neu, zaehler + 1, schwerpunkt)
        logger.info("Wett-Idee neu gewürfelt (%s → %s, Wurf %d).", kennung, neu_kennung, zaehler + 1)
        return

    # senden: in der Herrin-Stimme ausformulieren (Sprech-Tags bei Grok-TTS),
    # Limits-Gate auf das Ergebnis, Text ohne Tags + Voice an den Sub.
    from bot.prompts import followup as followup_prompts
    from bot.services import waehrung
    from bot.handlers import waehrung as waehrung_h
    if waehrung_h.wette_offen(await qdrant.get_user_profile("sklave") or {}):
        await query.message.reply_text(t("COACH_WETTIDEE_LAEUFT"))
        return
    try:
        ansage = grok.clean_text(await grok.simple(
            followup_prompts.wette_an_sklaven(idee, einsatz=waehrung.WETT_EINSATZ), max_tokens=350))
    except Exception:
        logger.exception("Wett-Ansage konnte nicht formuliert werden")
        ansage = ""
    if not ansage:
        await query.message.reply_text(t("COACH_WETTIDEE_FEHLER"))
        return
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    try:
        treffer = await limits_check.verletzungen(
            ansage, sklave_profil.get("hard_limits", []) or [],
            domina_profil.get("grenzen", []) or [])
    except Exception:
        logger.exception("Limits-Check der Wett-Ansage fehlgeschlagen – nicht gesendet.")
        await query.message.reply_text(t("COACH_WETTIDEE_FEHLER"))
        return
    if treffer:
        await query.message.reply_text(
            t("COACH_WETTIDEE_LIMIT", begriffe=", ".join(sorted({v["limit"] for v in treffer}))))
        return
    # Annahme-Pflicht (Bauplan 16.09. abends): Ansage mit ✅/❌, die Wette läuft
    # erst ab seiner Annahme (handlers/waehrung).
    try:
        await waehrung_h.wette_anbieten(context.bot, idee, ansage, kennung)
    except Exception:
        logger.exception("Wett-Ansage an den Sub konnte nicht zugestellt werden")
        await query.message.reply_text(t("COACH_WETTIDEE_FEHLER"))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await qdrant.patch_profile_fields("domina", {FELD_WETT_IDEE: {"gesendet": kennung}})
    await query.message.reply_text(t("COACH_WETTIDEE_GESENDET", einsatz=waehrung.WETT_EINSATZ))
    logger.info("Wettvorschlag an den Sub geschickt (%s) – wartet auf Annahme.", kennung)
