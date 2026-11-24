"""
Wochenplanung Handler – /wochenplanung Befehl und automatischer Sonntags-Job.
"""
import difflib
import logging
import re
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, kategorie_logik, telegram_helper, limits_check, zeiten
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)

_WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

# Der Generator-Prompt verlangt deutsche Wochentags-Labels (Daten-IDs!) – falls
# das LLM bei nicht-deutscher Antwortsprache trotzdem englische liefert, fängt
# der Parser sie ab und normalisiert auf die deutschen Labels (i18n-Fallback,
# sonst bricht der Plan-Import still).
_WOCHENTAGE_EN = {
    "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
    "Thursday": "Donnerstag", "Friday": "Freitag", "Saturday": "Samstag",
    "Sunday": "Sonntag",
}


def _parse_wochenplan(plan: str) -> list[dict]:
    """Parst den generierten Wochenplan-Text in [{tag, kategorie, aufgabe}, ...]."""
    entries: list[dict] = []
    cur: dict | None = None
    tag_re = re.compile(
        r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag"
        r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b\s*[–\-:]*\s*(.*)$"
    )
    aufgabe_re = re.compile(r"^\**\s*(?:Aufgabe|Task)\**\s*:\s*(.*)$", re.IGNORECASE)
    for raw in (plan or "").splitlines():
        line = raw.strip().lstrip("*").strip()
        m = tag_re.match(line)
        if m:
            if cur and cur["aufgabe"]:
                entries.append(cur)
            kat = m.group(2).strip().strip("[]*_ ").strip()
            tag = _WOCHENTAGE_EN.get(m.group(1), m.group(1))  # en → de (Daten-ID)
            cur = {"tag": tag, "kategorie": kat, "aufgabe": ""}
            continue
        if cur is not None:
            am = aufgabe_re.match(line)
            if am:
                cur["aufgabe"] = am.group(1).strip()
    if cur and cur["aufgabe"]:
        entries.append(cur)
    return entries


def _normalisiere_kategorie(entry: dict, pool: list[str] | None = None) -> str:
    """Gültige Pool-Kategorie für einen Plan-Eintrag.

    Reihenfolge (streng): Keyword aus dem Aufgabentext -> exaktes Plan-Label ->
    case-/unterstrich-insensitiver Treffer -> nächstliegende gültige Kategorie ->
    erst dann "allgemein". So landen erfundene Labels (z. B. "Anal_Cleanup",
    "Impact_Spezial") auf der echten Pool-Kategorie statt im "allgemein"-Topf.
    """
    pool = pool or kategorie_logik.katalog_kategorien()
    kw = kategorie_logik.keyword_match(entry.get("aufgabe", ""))
    if kw != "allgemein":
        return kw
    label = (entry.get("kategorie") or "").strip()
    if label in pool:
        return label
    norm = label.lower().replace(" ", "_").replace("-", "_")
    for cat in pool:
        if cat.lower() == norm:
            return cat
    nahe = difflib.get_close_matches(norm, [c.lower() for c in pool], n=1, cutoff=0.7)
    if nahe:
        return next(c for c in pool if c.lower() == nahe[0])
    return "allgemein"


# --- Abwechslung über die Wochen ----------------------------------------------
# Der Plan kannte seine Vorgänger nicht: Ein fester Kern von Kategorien stand
# Woche für Woche wieder drin, und der Montag wiederholte fast die Szene des
# Tagesvorschlags vom selben Abend (Log-Durchsicht 22.11.2026). Jetzt liegen die
# Kategorien der letzten Pläne im Profil der Dom-Seite. Was in BEIDEN letzten
# Plänen stand, pausiert eine Woche (samt Doppel desselben Themas) und steht
# dann nicht in der Kategorie-Liste; aus dem letzten Plan dürfen höchstens
# _MAX_WIEDERHOLT Kategorien wiederkommen. Darunter ein Netz mit einem
# Neu-Entwurf. Gibt die Dom-Seite selbst ein Thema vor, geht das Thema vor –
# dann stehen die letzten Pläne nur als Hinweis im Auftrag.
FELD_VERLAUF = "wochenplan_verlauf"
_VERLAUF_MAX = 4
_VERLAUF_IM_AUFTRAG = 2
_MAX_WIEDERHOLT = 3
_VORSCHLAEGE_IM_AUFTRAG = 5
# Ein Stichwort kürzer als das trifft zu vieles („anal", „arsch", „plug") und
# zählt für die Text-Prüfung des Netzes nicht.
_STICHWORT_MIN = 6


def _verlauf(domina_profile: dict | None) -> list[dict]:
    """Die gemerkten Pläne, jüngster zuerst: [{"datum": "JJJJ-MM-TT",
    "kategorien": [...]}]. Unlesbare Einträge fallen weg."""
    aus = []
    for e in (domina_profile or {}).get(FELD_VERLAUF) or []:
        if not isinstance(e, dict) or not e.get("datum") or not isinstance(e.get("kategorien"), list):
            continue
        kats = [k.strip() for k in e["kategorien"] if isinstance(k, str) and k.strip()]
        if kats:
            aus.append({"datum": str(e["datum"]), "kategorien": kats})
    return sorted(aus, key=lambda e: e["datum"], reverse=True)[:_VERLAUF_MAX]


def _pausiert(verlauf: list[dict]) -> list[str]:
    """Kategorien, die in beiden letzten Plänen standen – samt Doppel desselben
    Themas (dasselbe unter zwei Namen zählt als dasselbe). Reihenfolge wie im
    letzten Plan."""
    if len(verlauf) < 2:
        return []
    davor = set(verlauf[1]["kategorien"])
    aus: list[str] = []
    for k in verlauf[0]["kategorien"]:
        gruppe = kategorie_logik._mit_gleichem_thema(k)
        if any(x in davor for x in gruppe):
            aus.extend(x for x in gruppe if x not in aus)
    return aus


def _plan_kategorien(entries: list[dict]) -> list[str]:
    """Kategorie je Tag, wie sie im Plan steht; ein erfundenes Etikett wird wie
    beim Anlegen der Aufgaben auf die nächstliegende Kategorie gelegt."""
    pool = kategorie_logik.katalog_kategorien()
    aus = []
    for e in entries:
        label = (e.get("kategorie") or "").strip()
        aus.append(label if label in pool else _normalisiere_kategorie(e, pool))
    return aus


def _im_text(kategorien, text: str) -> list[str]:
    """Welche der Kategorien der Text über ein eindeutiges Stichwort anspricht –
    so fällt eine pausierte Praktik auch unter einem anderen Etikett auf."""
    stichworte = kategorie_logik.keywords()
    tief = kategorie_logik._match_text(text)
    return [k for k in kategorien
            if any(len(w.strip()) >= _STICHWORT_MIN and w in tief for w in stichworte.get(k, ()))]


def _verlauf_block(verlauf: list[dict], pausiert: list[str], mit_thema: bool) -> str:
    """Die letzten Pläne für den Auftrag – mit den Regeln daraus, oder (bei
    eigenem Thema der Dom-Seite) nur als Hinweis."""
    if not verlauf:
        return ""
    zeilen = "".join(f"  - {zeiten.alter_label(e['datum']) or e['datum']}: {', '.join(e['kategorien'])}\n"
                     for e in verlauf[:_VERLAUF_IM_AUFTRAG])
    block = f"Deine letzten Wochenpläne (jüngster zuerst):\n{zeilen}"
    if mit_thema:
        return block + ("→ Das Thema der Woche geht vor. Wo es dir die Wahl lässt, nimm Kategorien, "
                        "die in diesen Plänen fehlten.\n")
    if pausiert:
        block += ("→ Pause in dieser Woche, weil sie in beiden Plänen standen: " + ", ".join(pausiert)
                  + ". Sie fehlen deshalb in der Kategorie-Liste – auch wenn sie sonst bevorzugt werden. "
                    "Bring dieselbe Praktik auch nicht unter einem anderen Etikett.\n")
    block += (f"→ Aus dem letzten Plan kommen höchstens {_MAX_WIEDERHOLT} Kategorien wieder, und dann mit einer "
              "frischen Szene. Die übrigen Tage gehören Kategorien, die zuletzt fehlten.\n"
              "→ Das ist deine interne Auswahl: Erwähne im Plan nicht, was pausiert oder wovon du dich absetzt.\n")
    return block


def _vorschlaege_block(labels: list[str]) -> str:
    """Die letzten Tagesvorschläge als Stichworte – der Plan soll sie nicht noch
    einmal bringen."""
    labels = [x for x in (labels or []) if x][:_VORSCHLAEGE_IM_AUFTRAG]
    if not labels:
        return ""
    return ("Zuletzt als Tagesvorschlag gelaufen (jüngster zuerst): " + "; ".join(labels) + "\n"
            "→ Keine dieser Szenen noch einmal bringen. Vor allem der Wochenanfang setzt sich vom jüngsten "
            "Vorschlag klar ab.\n")


def _vielfalt_maengel(plan: str, verlauf: list[dict], pausiert: list[str]) -> list[str]:
    """Wo der Plan die Abwechslungs-Regeln verletzt – ein Eintrag je Tag bzw.
    Regel, in Worten für den Nachbesserungs-Auftrag. Leer = in Ordnung (auch
    bei einem Plan, der sich nicht lesen lässt: dann gibt es nichts zu prüfen)."""
    entries = _parse_wochenplan(plan)
    if not entries or not verlauf:
        return []
    kats = _plan_kategorien(entries)
    maengel = []
    for e, kat in zip(entries, kats):
        if kat in pausiert:
            maengel.append(f"{e['tag']}: {kat} pausiert diese Woche")
            continue
        versteckt = _im_text(pausiert, e.get("aufgabe", ""))
        if versteckt:
            maengel.append(f"{e['tag']}: bringt {versteckt[0]} unter anderem Etikett")
    letzter = [k for k in verlauf[0]["kategorien"] if k not in pausiert]
    wieder = [k for k in dict.fromkeys(kats) if k in letzter]
    if len(wieder) > _MAX_WIEDERHOLT:
        maengel.append(f"{len(wieder)} Kategorien aus dem letzten Plan wiederholt ({', '.join(wieder)}) – "
                       f"höchstens {_MAX_WIEDERHOLT}")
    return maengel


async def _merke_plan(entries: list[dict]) -> None:
    """Kategorien des gesendeten Plans in den Verlauf (Profil der Dom-Seite).
    Ein zweiter Plan am selben Tag ersetzt den ersten – sonst gälten seine
    Kategorien gleich als „in beiden letzten Plänen"."""
    kats = [k for k in _plan_kategorien(entries) if k and k != "allgemein"]
    if not kats:
        return
    heute = zeiten.heute_lokal().isoformat()
    profil = await qdrant.get_user_profile("domina") or {}
    verlauf = [e for e in _verlauf(profil) if e["datum"] != heute]
    neu = ([{"datum": heute, "kategorien": kats}] + verlauf)[:_VERLAUF_MAX]
    await qdrant.patch_profile_fields("domina", {FELD_VERLAUF: neu})


def _plan_buttons(nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WOCHENPLAN_ALLE"), callback_data=f"wochenplan:alle:{nonce}"),
        InlineKeyboardButton(t("BUTTON_WOCHENPLAN_VERWERFEN"), callback_data=f"wochenplan:verwerfen:{nonce}"),
    ]])


async def sende_plan(bot, chat_id: str, plan: str, kopf: str) -> None:
    """Sendet den Plan; hängt 'Alle erstellen'-Buttons an, wenn er parsebar ist."""
    entries = _parse_wochenplan(plan)
    if entries:
        s = state.get(str(chat_id))
        s["wochenplan_entries"] = entries
        # Nonce in die Callback-Daten: ein liegengebliebener Button eines
        # FRÜHEREN Plans darf nicht die neuesten State-Einträge erstellen.
        nonce = uuid.uuid4().hex[:8]
        s["wochenplan_nonce"] = nonce
        await bot.send_message(chat_id=chat_id, text=f"{kopf}\n\n{plan}", reply_markup=_plan_buttons(nonce))
        # Erst NACH dem Senden merken – ein nie zugestellter Plan soll den nächsten nicht einschränken.
        try:
            await _merke_plan(entries)
        except Exception as e:
            logger.warning("Wochenplan nicht im Verlauf gemerkt: %s", e)
    else:
        await bot.send_message(chat_id=chat_id, text=f"{kopf}\n\n{plan}")


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Startet die Wochenplanung – fragt nach Thema/Fokus."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    state.set_mode(chat_id, "wochenplanung_thema")

    await update.message.reply_text(t("WOCHENPLAN_THEMA_FRAGE"), parse_mode="Markdown")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Themen-Eingabe und generiert den Wochenplan."""
    chat_id = str(update.effective_chat.id)
    thema = update.message.text.strip()

    state.set_mode(chat_id, "chat")

    # Getipptes "abbrechen" nicht als Wochen-Thema interpretieren.
    if thema.lower() in ("abbrechen", "/abbrechen"):
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    await update.message.reply_text(t("WOCHENPLAN_WARTE"))

    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_kategorien = await qdrant.get_recent_task_kategorien("sklave", limit=7)
    bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave")

    try:
        plan = await _generiere_wochenplan(
            domina_profile=domina_profile,
            sklave_profile=sklave_profile,
            thema=thema,
            letzte_kategorien=letzte_kategorien,
            bewertungs_kontext=bewertungs_kontext,
        )
        await sende_plan(context.bot, chat_id, plan, t("WOCHENPLAN_TITEL"))
    except Exception as e:
        logger.error("Fehler beim Wochenplan: %s", e)
        await update.message.reply_text(t("WOCHENPLAN_FEHLER"))


async def _generiere_wochenplan(
    domina_profile: dict,
    sklave_profile: dict,
    thema: str,
    letzte_kategorien: list,
    bewertungs_kontext: str,
) -> str:
    interessen = domina_profile.get("interessen", [])
    hard_limits = sklave_profile.get("hard_limits", []) or []
    domina_grenzen = domina_profile.get("grenzen", []) or []

    from bot.prompts import coach_persona

    # Review D10: gesperrte Kategorien nicht erst anbieten und dann verbieten;
    # das Vorschlags-Feedback der Dom-Seite und die kinderfreien Zeiten fehlten
    # dem Wochenplan ganz.
    gesperrt = set(kategorie_logik.gesperrte_kategorien(sklave_profile))
    praeferenzen = domina_profile.get("kategorie_praeferenzen", {}) or {}
    gern, ungern = kategorie_logik.domina_praeferenz_rangliste(praeferenzen)
    # Ein Thema, das die Dom-Seite abgelehnt hat, ruht wie beim Tagesvorschlag
    # auch hier (stand bisher weiter in der Kategorie-Liste des Plans) – außer
    # sie nennt es selbst als Thema der Woche.
    thema_ruht = [k for k in kategorie_logik.thema_pausiert(praeferenzen)
                  if k not in kategorie_logik.kategorien_in_text(thema or "")]
    gern = [k for k in gern if k not in thema_ruht]
    ungern = [k for k in ungern if k not in thema_ruht]
    feedback_teile = ([f"gern übernommen: {', '.join(gern)}"] if gern else []) + \
                     ([f"eher abgelehnt: {', '.join(ungern)}"] if ungern else []) + \
                     ([f"reizt sie gerade nicht (ganz weglassen, auch als Beiwerk): {', '.join(thema_ruht)}"]
                      if thema_ruht else [])
    # Abwechslung über die Wochen (s. o.): ohne eigenes Thema gelten die Regeln,
    # mit Thema stehen die letzten Pläne nur als Hinweis da.
    verlauf = _verlauf(domina_profile)
    mit_thema = bool((thema or "").strip())
    pausiert = [] if mit_thema else _pausiert(verlauf)
    nicht_waehlbar = gesperrt | set(pausiert) | set(thema_ruht)
    try:
        vorschlags_labels, _, _ = await qdrant.get_recent_tiny_tasks(limit=_VORSCHLAEGE_IM_AUFTRAG)
    except Exception as e:
        logger.warning("Letzte Tagesvorschläge nicht ladbar (Wochenplan ohne diese Liste): %s", e)
        vorschlags_labels = []
    feedback_zeile = ("Ihr Feedback auf frühere Vorschläge (ihre Vorlieben): "
                      + "; ".join(feedback_teile) + "\n") if feedback_teile else ""
    kinderfrei = domina_profile.get("kinderfreie_zeiten", []) or []
    kind_anzahl = domina_profile.get("kind_anzahl")
    if kind_anzahl == 0:
        kinder_zeile = ""
    elif kinderfrei:
        kinder_zeile = (f"Kinder im Haus. Kinderfreie Zeiten: {', '.join(kinderfrei)} – "
                        "alles Hörbare oder Sichtbare gehört in diese Zeiten, tagsüber nur Diskretes.\n")
    else:
        kinder_zeile = "Kinder im Haus – alle Aufgaben müssen diskret, leise und kinderfrei bleiben.\n"

    system = f"""Du erstellst der Domina einen Wochenplan für ihren Sklaven – Mo bis So, eine Aufgabe pro Tag.

{coach_persona.fuer_aufgaben_vorschlag()}

Format (REINER TEXT, kein Markdown, keine Sterne, keine Backticks):
🗓 Wochenplan

Montag – [Kategorie]
Aufgabe: <konkrete Aufgabe in einem Satz, die zu IHM passt – kein generischer 101-Task>
Warum: <kurze Begründung, warum das an DIESEM Tag dran ist – aus Beobachtbarem (wie er zuletzt reagiert hat, was die Woche hergibt, was es mit ihm macht) oder als deine Vermutung; NIE als Abgleich mit seinen Vorlieben/Wünschen ("er wünscht sich", "trifft seine Vorliebe", "sein Wunsch") und nicht "weil Abwechslung wichtig ist">

Dienstag – [Kategorie]
…

(für alle 7 Tage Mo bis So)

Am Ende: zwei bis drei Sätze locker geschrieben – wie eine Freundin den Plan zusammenfasst. Was ist der rote Faden, worauf solltest du achten. Danach NICHTS mehr anhängen – keine Kategorie-Namen, keine Tags, keine losen Wörter.
Kein [AUFGABE: ...] Tag – das sind Vorschläge, keine automatischen Aufgaben.
Maximale Länge: {config.WOCHENPLAN_WORTLIMIT} Wörter.

[Kategorie] AUSSCHLIESSLICH exakt aus dieser Liste wählen (genau so geschrieben, mit Unterstrich; nicht übersetzen, nicht erfinden):
{', '.join(k for k in kategorie_logik.katalog_kategorien() if k not in nicht_waehlbar)}

Logische Schlüssigkeit ist PFLICHT – jede Aufgabe muss in sich stimmig sein UND wirklich zur gewählten Kategorie passen:
- Man kann nur reinigen/aufnehmen, was vorher real entstanden ist. "Creampie_Cleanup" verlangt eine ECHTE Ejakulation IN die zu reinigende Öffnung – ein Strapon/Dildo erzeugt KEINEN Creampie. Also niemals "Sperma aus dem Arsch lecken" nach Strapon-Pegging o. Ä.
- Keine widersprüchlichen oder physisch unmöglichen Kombinationen.

WICHTIG: Die Struktur-Wörter sind festes Format-Protokoll und bleiben IMMER exakt so –
auch wenn du in einer anderen Sprache antwortest: die Wochentags-Labels (Montag bis Sonntag)
sowie die Zeilen-Präfixe "Aufgabe:" und "Warum:". Nur die Inhalte dahinter folgen der Sprache."""
    prompt = f"""Thema/Fokus der Woche: {thema if thema else 'abwechslungsreich'}
{coach_persona.level_zeile(domina_profile.get('aktuelles_level', 1))}
Interessen der Domina: {', '.join(interessen) if interessen else 'nicht angegeben'}
Letzte Aufgaben-Kategorien (zur Abwechslung): {', '.join(letzte_kategorien) if letzte_kategorien else 'keine'}
{_verlauf_block(verlauf, pausiert, mit_thema)}{_vorschlaege_block(vorschlags_labels)}Was der Domina gut gefiel: {bewertungs_kontext if bewertungs_kontext else 'keine Daten'}
{feedback_zeile}{kinder_zeile}{coach_persona.sklaven_kontext_block(sklave_profile, domina_grenzen)}"""
    # Ausstattungs-Impuls (Inventar): an EINEM Tag der Woche ein vorhandenes
    # Gerät tragend einbauen – nicht die ganze Woche damit bestücken. Geräte
    # eines pausierten Themas stehen dafür nicht zur Wahl.
    from bot.services import inventar
    ruht = set(pausiert) | set(thema_ruht)
    impuls = await inventar.impuls_wahl(
        ohne=(lambda eintrag: bool(kategorie_logik.kategorien_in_text(eintrag) & ruht)) if ruht else None)
    if impuls:
        prompt += ("\n" + coach_persona.inventar_impuls_block(impuls)
                   + "Setze ihn an genau EINEM Tag der Woche ein, nicht an mehreren.\n")
    # Kategorien wählt das LLM erst beim Generieren → alle vorhandenen Wissens-Briefe beilegen.
    skill_block = await coach_persona.skill_kontext_block()
    if skill_block:
        prompt += "\n\n" + skill_block

    # Limits-Check: wenn der Plan Grenzen verletzt, einmal verschärft neu generieren.
    plan = await limits_check.generate_mit_limit_retry(
        prompt, hard_limits, domina_grenzen, system=system, reasoning=True,
    )
    if plan is None:
        raise ValueError("Wochenplan auch nach Re-Generierung Grenzen-verletzend – verworfen.")
    if mit_thema:
        return plan
    # Netz unter den Abwechslungs-Regeln: ein Neu-Entwurf mit den Fundstellen im
    # Auftrag; es gilt der Entwurf mit weniger Verstößen (der erste bei Gleichstand).
    maengel = _vielfalt_maengel(plan, verlauf, pausiert)
    if maengel:
        logger.info("Wochenplan wiederholt die Vorwochen (%s) – generiere einmal neu.", "; ".join(maengel))
        nachbessern = ("\n\nDein erster Entwurf hat die Regeln zu den letzten Wochenplänen verletzt: "
                       + "; ".join(maengel) + ". Schreib den Plan neu und besetze diese Tage anders.\n")
        try:
            neu = await limits_check.generate_mit_limit_retry(
                prompt + nachbessern, hard_limits, domina_grenzen, system=system, reasoning=True,
            )
        except Exception as e:
            logger.warning("Wochenplan: Neu-Entwurf fehlgeschlagen – der erste Entwurf gilt: %s", e)
            neu = None
        if neu:
            rest = _vielfalt_maengel(neu, verlauf, pausiert)
            if len(rest) < len(maengel):
                plan = neu
                if rest:
                    logger.info("Wochenplan: Neu-Entwurf gilt, Rest-Verstöße: %s", "; ".join(rest))
    return plan


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wochenplan-Buttons: alle Tage als Aufgaben-Serie erstellen oder nur als Vorschlag belassen."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 2:
        return
    action = parts[1]
    nonce = parts[2] if len(parts) > 2 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    s = state.get(paare.dom_chat_id())

    # Veralteter Button (anderer/kein Nonce): NICHT die neuesten State-Einträge
    # erstellen – der Button gehört zu einem früheren Plan.
    if nonce != s.get("wochenplan_nonce"):
        await query.message.reply_text(t("WOCHENPLAN_NICHT_IM_SPEICHER"))
        return

    entries = s.pop("wochenplan_entries", None)
    s.pop("wochenplan_nonce", None)

    if action == "verwerfen":
        await query.message.reply_text(t("WOCHENPLAN_NUR_VORSCHLAG"))
        return
    if not entries:
        await query.message.reply_text(t("WOCHENPLAN_NICHT_IM_SPEICHER"))
        return

    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    hard_limits = sklave_profil.get("hard_limits", []) or []
    domina_grenzen = domina_profil.get("grenzen", []) or []
    level = domina_profil.get("aktuelles_level", 1)

    serie_id = str(uuid.uuid4())
    gesamt = len(entries)
    pool = kategorie_logik.alle_kategorien(sklave_profil)

    erstellt = 0
    uebersprungen = 0
    erste_aufgabe = None
    for i, e in enumerate(entries):
        aufgabe = e.get("aufgabe", "")
        if not aufgabe or await limits_check.verletzungen(aufgabe, hard_limits, domina_grenzen):
            uebersprungen += 1
            continue
        await qdrant.erstelle_task(
            aufgabe, _normalisiere_kategorie(e, pool), level,
            status="offen" if erstellt == 0 else "serie_wartend",
            quelle="wochenplan",
            followup_in_tagen=i + 1,
            extra={"serie_id": serie_id, "serie_tag": i + 1, "serie_gesamt": gesamt},
        )
        if erste_aufgabe is None:
            erste_aufgabe = aufgabe
        erstellt += 1

    if erste_aufgabe:
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(erste_aufgabe), max_tokens=250)
        except Exception as ex:
            logger.error("Wochenplan: aufgabe_an_sklaven fehlgeschlagen: %s", ex)
            anweisung = erste_aufgabe
        await telegram_helper.send_sklave(context.bot, anweisung, voice_text=anweisung)

    msg = t("WOCHENPLAN_ERSTELLT", anzahl=erstellt)
    if uebersprungen:
        msg += t("WOCHENPLAN_UEBERSPRUNGEN", anzahl=uebersprungen)
    await query.message.reply_text(msg)
    logger.info("Wochenplan als Serie erstellt: %d Aufgaben (serie_id=%s), %d übersprungen.",
                erstellt, serie_id, uebersprungen)
