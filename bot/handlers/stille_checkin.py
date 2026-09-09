"""
Stille-Check-in 🔕 (09.09.2026).

Hat die dominante Seite STILLE_CHECKIN_TAGE Tage weder geschrieben noch etwas
angetippt, fragt der Coach sie SELBST, was gerade los ist – statt weiter
Vorschläge in die Stille zu schicken. Instrument, den Grund zu ERFAHREN statt
zu raten (Anlass 09.09.2026: wochenlange Funkstille auf der Dom-Seite – jede
Vermutung über das Warum blieb Vermutung).

Ablauf:
  scheduler.stille_checkin_job → frage_stellen(): Coach-Stimme + vier Buttons,
  Freitext geht ebenfalls.
    ⏳ keine Zeit               → Coach-Ruhe STILLE_RUHE_TAGE (state.set_coach_ruhe)
    🎯 Vorschläge passen nicht  → Rückfrage → Antwort wird Coach-Regel
    🤷 läuft gerade ohne Bot    → Zuschauer-Modus (nur noch Berichte)
    😬 irgendwas nervt          → Rückfrage → Antwort wird Coach-Notiz
  Freitext → Klassifikation (temp 0) → dieselben Reaktionen. Ausnahme: „läuft
  ohne Bot“ aus Freitext schaltet NICHT automatisch, sondern bietet den
  Zuschauer-Modus per Button an (eine Fehlklassifikation würde den Coach sonst
  dauerhaft stummschalten).

Vertraulichkeit (Owner-Entscheid): die Antwort bleibt beim Coach – Profil-Feld
`stille_checkin` (Domina) + coach_regeln (quelle='stille_checkin'). Der Sub
sieht in /stats nur, DASS gefragt/geantwortet wurde. Kein Inhalt im INFO-Log.

Pro Stille-Phase höchstens zwei Fragen (die zweite nach STILLE_ZWEITE_FRAGE_TAGE
ohne Antwort), danach Ruhe, bis sie wieder aktiv war und erneut verstummt.
Coach-Ruhe/Zuschauer-Modus gaten alle proaktiven Dom-Jobs zentral in
scheduler._flow_aktiv (+ training.daily_training); Rücknahme: /einstellungen → 9.
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, grok
from bot.prompts import coach_persona
from bot.messages import t

logger = logging.getLogger(__name__)

MODE = "stille_checkin"
# Button-Typen ↔ Klassifikations-Token des Freitexts (Großschreibung).
ANTWORT_TYPEN = ("keine_zeit", "aufgaben", "ohne_bot", "nervt")
_KLASSEN = ("KEINE_ZEIT", "AUFGABEN", "OHNE_BOT", "NERVT", "SONSTIGES", "ANDERES")
# Toleranz, innerhalb derer die Antwort auf den Check-in selbst nicht als
# „neue Aktivität“ zählt (sonst würde jede Antwort eine neue Stille-Phase
# eröffnen und nach 7 Tagen erneut gefragt).
_PHASEN_TOLERANZ = timedelta(minutes=10)
_NUMMERIERT_RE = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _dd_mm(iso: str | None) -> str:
    dt = _parse(iso or "")
    if dt is None:
        return "?"
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.")


def _buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("BUTTON_STILLE_KEINE_ZEIT"), callback_data="stille:keine_zeit")],
        [InlineKeyboardButton(t("BUTTON_STILLE_AUFGABEN"), callback_data="stille:aufgaben")],
        [InlineKeyboardButton(t("BUTTON_STILLE_OHNE_BOT"), callback_data="stille:ohne_bot")],
        [InlineKeyboardButton(t("BUTTON_STILLE_NERVT"), callback_data="stille:nervt")],
    ])


def _beenden(chat_id: str) -> None:
    """Mode nur zurücksetzen, wenn er noch UNSERER ist (Muster tiny_task_feedback)."""
    state.get(chat_id).pop("stille_rueckfrage", None)
    if state.get_mode(chat_id) == MODE:
        state.set_mode(chat_id, "chat")


async def letzte_domina_eingabe() -> datetime | None:
    """Jüngste Eingabe der Dom-Seite: persistierter Eingangsstempel (Text ODER
    Button, state.touch_eingang) ∪ neueste erteilte Aufgabe / Coach-Gespräch
    (scheduler._letzte_domina_aktivitaet, 30 Tage) ∪ Coach-Gespräch bis 400
    Tage zurück (damit auch eine lange Stille einen Anker hat). None = nie."""
    from bot.scheduler import followup as fu  # lazy: zirkulären Import vermeiden
    stempel: list[datetime] = []
    ts = state.letzter_eingang(paare.dom_chat_id())
    if ts:
        stempel.append(datetime.fromtimestamp(ts, tz=timezone.utc))
    aktiv = await fu._letzte_domina_aktivitaet()
    if aktiv:
        stempel.append(aktiv)
    if not stempel:
        jetzt = _jetzt()
        conv = await qdrant.get_conversations_in_range(
            "domina", (jetzt - timedelta(days=400)).isoformat(), jetzt.isoformat(), limit=1)
        if conv:
            dt = _parse(conv[-1].get("datum", ""))
            if dt:
                stempel.append(dt)
    return max(stempel) if stempel else None


def gleiche_phase(letzte: datetime, checkin: dict) -> bool:
    """True, wenn seit dem letzten Check-in-Dialog (Frage bzw. Antwort) keine
    NEUE Aktivität kam – die Antwort selbst zählt nicht (Toleranz)."""
    ende = max(_parse(checkin.get("gefragt_am", "")) or datetime(2000, 1, 1, tzinfo=timezone.utc),
               _parse(checkin.get("antwort_am", "")) or datetime(2000, 1, 1, tzinfo=timezone.utc))
    return letzte <= ende + _PHASEN_TOLERANZ


# ---------------------------------------------------------------------------
# Frage
# ---------------------------------------------------------------------------

def _frage_prompt(tage: int, zweite: bool, tage_seit_frage: int) -> tuple[str, str]:
    system = (
        coach_persona.fuer_coach_prompt()
        + "\n\nAUFGABE: Deine Freundin (die dominante Seite) hat sich seit "
        f"{tage} Tagen nicht bei dir gemeldet – nichts geschrieben, nichts angetippt. "
        "Schreib ihr JETZT eine kurze Nachricht, die ehrlich fragt, was gerade los ist.\n"
        "STRIKT:\n"
        "- Kein Vorwurf, kein Schuldgefühl, kein Druck, kein „du musst“.\n"
        "- KEIN Aufgaben-Vorschlag, keine Idee für den Sub, kein Aufzählen, was du alles könntest.\n"
        "- Nenne beiläufig die vier Möglichkeiten (keine Zeit / die Vorschläge passen nicht / "
        "es läuft gerade ohne dich / irgendwas nervt) und sag, dass sie unten antippen oder "
        "kurz schreiben kann.\n"
        "- Sag, dass eine Pause auch völlig okay ist.\n"
        "- Keine Überschrift, kein Vorwort, keine Meta-Erklärung über Bots/Systeme, "
        "höchstens ein Emoji.\n"
    )
    if zweite:
        system += (
            f"- Du hast vor {tage_seit_frage} Tagen schon einmal genau das gefragt und keine "
            "Antwort bekommen. Frag ein LETZTES Mal, noch leichter und kürzer (2–3 Sätze), "
            "und sag, dass du dich danach zurückhältst, bis sie sich von selbst meldet.\n"
        )
    else:
        system += "- Länge: 2–4 Sätze.\n"
    system += "Antworte NUR mit der Nachricht."
    prompt = f"Tage ohne Rückmeldung: {tage}"
    return system, prompt


def _brauchbar(text: str) -> bool:
    """Deterministische Abnahme des LLM-Textes (Lernmuster: Detektor > Regel):
    nicht leer, nicht ausufernd, eine echte Frage, keine nummerierte Liste
    (= Aufgaben-/Optionen-Aufzählung)."""
    if not text or len(text) > 900:
        return False
    if "?" not in text:
        return False
    if _NUMMERIERT_RE.search(text):
        return False
    return True


async def _frage_text(tage: int, zweite: bool, tage_seit_frage: int) -> str:
    system, prompt = _frage_prompt(tage, zweite, tage_seit_frage)
    text = ""
    try:
        text = grok.clean_text(await grok.simple(prompt, system=system, temperature=0.8, max_tokens=350))
    except Exception:
        logger.exception("Stille-Check-in: Frage-Generierung fehlgeschlagen – Fallback-Text")
    if not _brauchbar(text):
        if text:
            logger.info("Stille-Check-in: LLM-Frage verworfen (%d Zeichen) – Fallback-Text", len(text))
        key = "STILLE_FRAGE_ZWEITE_FALLBACK" if zweite else "STILLE_FRAGE_FALLBACK"
        text = t(key, tage=(tage_seit_frage if zweite else tage))
    return text


async def frage_stellen(bot, tage: int, zweite: bool = False, tage_seit_frage: int = 0) -> bool:
    """Schickt die Check-in-Frage an die Dom-Seite. True nur bei Versand.
    Mode erst NACH erfolgreichem Send (Muster tiny_task_feedback.frage_stellen)."""
    chat_id = paare.dom_chat_id()
    text = await _frage_text(tage, zweite, tage_seit_frage)
    # TOCTOU-Re-Check nach dem LLM-Await (Muster spiel_impuls/coach_quiz).
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("Stille-Check-in nach Generierung verworfen – Pause/Mode geändert.")
        return False
    await telegram_helper.send_domina(bot, text, parse_mode="Markdown", reply_markup=_buttons())
    state.get(chat_id).pop("stille_rueckfrage", None)
    state.set_mode(chat_id, MODE)
    logger.info("Stille-Check-in gesendet (%s Frage, %d Tage still).", "zweite" if zweite else "erste", tage)
    return True


# ---------------------------------------------------------------------------
# Antwort verbuchen + Reaktionen
# ---------------------------------------------------------------------------

async def _verbuche_antwort(typ: str, kurz: str = "") -> None:
    profil = await qdrant.get_user_profile("domina") or {}
    ci = dict(profil.get("stille_checkin") or {})
    ci["antwort_am"] = _jetzt().isoformat()
    ci["antwort_typ"] = typ
    if kurz:
        ci["antwort_kurz"] = kurz[:200]
    await qdrant.patch_profile_fields("domina", {"stille_checkin": ci})


def _ruhe_setzen(tage: int) -> str:
    bis = _jetzt() + timedelta(days=max(1, tage))
    state.set_coach_ruhe("ruhe", bis=bis.isoformat())
    return _dd_mm(bis.isoformat())


async def _reaktion(typ: str, antworten) -> None:
    """Sanfte Auto-Reaktion je Antwort-Typ. `antworten(text, reply_markup=None)`
    ist ein Awaitable-Sender (reply_text des Callbacks bzw. der Nachricht)."""
    if typ == "keine_zeit":
        bis = _ruhe_setzen(config.STILLE_RUHE_TAGE)
        logger.info("Stille-Check-in: Antwort 'keine Zeit' → Coach-Ruhe bis %s", bis)
        await antworten(t("STILLE_ANTWORT_KEINE_ZEIT", bis=bis))
    elif typ == "ohne_bot":
        state.set_coach_ruhe("zuschauer")
        logger.info("Stille-Check-in: Antwort 'läuft ohne Bot' → Zuschauer-Modus")
        await antworten(t("STILLE_ANTWORT_OHNE_BOT"))
    elif typ == "aufgaben":
        await antworten(t("STILLE_RUECKFRAGE_AUFGABEN"))
    elif typ == "nervt":
        await antworten(t("STILLE_RUECKFRAGE_NERVT"))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Buttons der Check-in-Frage (Rolle via main._callback_gate: Dom)."""
    query = update.callback_query
    await query.answer()
    typ = (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
    chat_id = paare.dom_chat_id()

    if typ == "ohne_bot_ja":
        # Angebot nach Freitext-Klassifikation – Guard: nicht doppelt schalten.
        ruhe = state.coach_ruhe()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if ruhe and ruhe.get("modus") == "zuschauer":
            return
        await _reaktion("ohne_bot", query.message.reply_text)
        return

    if typ not in ANTWORT_TYPEN:
        return

    # Doppel-Tap-/Stale-Guard (Muster D9/N3): nur eine noch UNBEANTWORTETE Frage
    # entscheiden – ein zweiter/verspäteter Tap würde die Reaktion sonst
    # erneut auslösen (Ruhe verlängern, Zuschauer-Modus neu setzen).
    profil = await qdrant.get_user_profile("domina") or {}
    ci = profil.get("stille_checkin") or {}
    if not ci.get("gefragt_am") or ci.get("antwort_am"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _verbuche_antwort(typ)
    if typ in ("aufgaben", "nervt"):
        # Rückfrage: nächster Freitext wird Regel/Notiz. Mode nur setzen, wenn
        # sie gerade frei ist (oder noch in unserem Mode) – keinen Flow kapern.
        if state.get_mode(chat_id) in ("chat", None, MODE):
            state.get(chat_id)["stille_rueckfrage"] = typ
            state.set_mode(chat_id, MODE)
    else:
        _beenden(chat_id)
    await _reaktion(typ, query.message.reply_text)
    logger.info("Stille-Check-in beantwortet (Button, Typ=%s).", typ)


# ---------------------------------------------------------------------------
# Freitext
# ---------------------------------------------------------------------------

async def _klassifiziere(text: str) -> str:
    """Freitext im Check-in-Mode einordnen (temp 0). Fail-safe: SONSTIGES
    (= Antwort auf die Frage, keine automatische Schaltung)."""
    from bot.prompts import followup as fp
    system = (
        "Der Coach hat die dominante Seite gefragt, warum sie sich seit über einer Woche "
        "nicht gemeldet hat. Klassifiziere ihre Antwort:\n"
        "- KEINE_ZEIT: keine Zeit, Stress, Alltag, Krankheit, braucht gerade Pause\n"
        "- AUFGABEN: die Vorschläge/Aufgaben/Inhalte passen nicht (Richtung, Härte, Themen, Länge)\n"
        "- OHNE_BOT: es läuft gerade ohne den Bot / sie braucht ihn gerade nicht\n"
        "- NERVT: etwas am Bot stört sie (zu viel, Ton, Technik, Fragen)\n"
        "- SONSTIGES: eine Antwort auf die Frage, die in keine der Kategorien passt\n"
        "- ANDERES: kein Bezug zur Frage – ein neues Anliegen, ein Auftrag an den Bot, "
        "eine Frage, Smalltalk, ein anderes Thema\n"
        "Antworte NUR mit einem der Wörter."
    )
    prompt = fp.nutzer_text("Antwort der dominanten Seite", text)
    try:
        roh = grok.clean_text(await grok.simple(prompt, system=system, temperature=0)).upper()
        for k in _KLASSEN:
            if roh.startswith(k):
                return k
    except Exception:
        logger.exception("Stille-Check-in: Klassifikation fehlgeschlagen – behandle als SONSTIGES")
    return "SONSTIGES"


async def _kurzfassung(text: str) -> str:
    from bot.prompts import followup as fp
    system = (
        "Fasse die Antwort in EINEM Satz zusammen (max. 25 Wörter), dritte Person "
        "(„Sie …“), sachlich, ohne Bewertung. Nur der Satz."
    )
    try:
        kurz = grok.clean_text(await grok.simple(
            fp.nutzer_text("Antwort", text), system=system, temperature=0, max_tokens=80))
        if kurz:
            return kurz[:200]
    except Exception:
        logger.exception("Stille-Check-in: Kurzfassung fehlgeschlagen – Rohtext gekürzt")
    return text.strip()[:200]


async def _regel_aus(text: str) -> str:
    """Aus 'die Vorschläge passen nicht' eine Coach-Regel ableiten (Muster
    tiny_task_feedback._vorschlag_aus_ablehnung). '' = keine ableitbar."""
    from bot.prompts import followup as fp
    system = (
        "Die dominante Seite erklärt, warum die Aufgaben-Vorschläge des Coachs für sie "
        "nicht passen. Leite daraus EINE konkrete Regel ab, an die sich der Coach bei "
        "künftigen Vorschlägen halten soll: ein Satz, du-Form, konkret.\n"
        "Ist keine allgemeine Regel ableitbar (nur Tagesform, nur Gefühl), antworte "
        "NUR mit: KEINE_REGEL\nSonst nur die Regel als reinen Text."
    )
    try:
        regel = grok.clean_text(await grok.simple(
            fp.nutzer_text("Erklärung", text), system=system, temperature=0, max_tokens=120))
        if regel and not regel.upper().startswith("KEINE_REGEL"):
            return regel[:300]
    except Exception:
        logger.exception("Stille-Check-in: Regel-Ableitung fehlgeschlagen")
    return ""


async def _speichere_erkenntnis(text: str, art: str, kurz: str = "") -> None:
    """aufgaben → Coach-Regel (Fallback Notiz), nervt → Coach-Notiz. Direkt
    aktiv (wie /merken): es sind ihre eigenen Worte, keine Vermutung."""
    kontext = ("Stille-Check-in: Vorschläge passen nicht" if art == "aufgaben"
               else "Stille-Check-in: etwas nervt")
    if art == "aufgaben":
        regel = await _regel_aus(text)
        if regel:
            await qdrant.save_coach_regel("domina", regel, typ="regel", status="aktiv",
                                          quelle="stille_checkin", kontext=kontext)
            logger.info("Stille-Check-in: Coach-Regel gespeichert (%d Zeichen).", len(regel))
            return
    notiz = kurz or await _kurzfassung(text)
    await qdrant.save_coach_regel("domina", notiz, typ="notiz", status="aktiv",
                                  quelle="stille_checkin", kontext=kontext)
    logger.info("Stille-Check-in: Coach-Notiz gespeichert (%d Zeichen).", len(notiz))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Freitext der Dom-Seite im Mode stille_checkin."""
    chat_id = str(update.effective_chat.id)
    text = (update.message.text or "").strip()
    s = state.get(chat_id)

    if text.lower() in ("abbrechen", "/abbrechen"):
        _beenden(chat_id)
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    rueckfrage = s.get("stille_rueckfrage")
    if rueckfrage:
        kurz = await _kurzfassung(text)
        await _speichere_erkenntnis(text, rueckfrage, kurz)
        await _verbuche_antwort(rueckfrage, kurz)
        _beenden(chat_id)
        await update.message.reply_text(t("STILLE_NOTIERT"))
        logger.info("Stille-Check-in: Rückfrage (%s) beantwortet.", rueckfrage)
        return

    klasse = await _klassifiziere(text)
    if klasse == "ANDERES":
        # Kein Bezug zur Frage → normaler Coach-Chat; die Frage bleibt offen
        # (Mode + Buttons unangetastet, Muster tiny_task_feedback).
        logger.info("Stille-Check-in: Freitext als anderes Anliegen erkannt → Coach-Chat.")
        from bot.handlers import domina
        await domina.handle(update, context)
        return

    kurz = await _kurzfassung(text)
    typ = klasse.lower()
    if klasse in ("AUFGABEN", "NERVT"):
        await _speichere_erkenntnis(text, typ, kurz)
    await _verbuche_antwort(typ, kurz)
    _beenden(chat_id)
    logger.info("Stille-Check-in beantwortet (Freitext, Klasse=%s).", klasse)

    if klasse == "KEINE_ZEIT":
        await _reaktion("keine_zeit", update.message.reply_text)
    elif klasse == "OHNE_BOT":
        angebot = InlineKeyboardMarkup([[InlineKeyboardButton(
            t("BUTTON_STILLE_OHNE_BOT_JA"), callback_data="stille:ohne_bot_ja")]])
        await update.message.reply_text(t("STILLE_OHNE_BOT_ANGEBOT"), reply_markup=angebot)
    else:
        await update.message.reply_text(t("STILLE_FREITEXT_DANKE"))


# ---------------------------------------------------------------------------
# Status (für /einstellungen und /stats)
# ---------------------------------------------------------------------------

def ruhe_status_text() -> str:
    """Kurzer Status der Coach-Ruhe des Paares; '' = alles normal."""
    ruhe = state.coach_ruhe()
    if not ruhe:
        return ""
    if ruhe.get("modus") == "zuschauer":
        return "Zuschauer-Modus (nur Berichte)"
    return f"Coach-Ruhe bis {_dd_mm(ruhe.get('bis'))}"


def einstellungs_hinweis() -> str:
    """Hinweis-Text für /einstellungen → 9 (Konvention der Datei: deutsch, Markdown)."""
    status = ruhe_status_text() or "aus – alles normal"
    return (
        f"Aktuell: *{status}*\n\n"
        "Was soll gelten?\n"
        "`-` = alles normal (Ruhe / Zuschauer-Modus aus)\n"
        f"`ruhe` = {config.STILLE_RUHE_TAGE} Tage Coach-Ruhe: keine Vorschläge, Fragen und "
        "Impulse vom Coach (`ruhe 7` für 7 Tage)\n"
        "`zuschauer` = Zuschauer-Modus: dauerhaft nur noch Berichte über den Sklaven, "
        "nichts Proaktives\n\n"
        "_Berichte (erledigte Aufgaben, Gefühle, Wünsche) kommen in beiden Modi weiter._"
    )


def einstellung_anwenden(eingabe: str) -> bool:
    """Eingabe aus /einstellungen → 9 anwenden. False = nicht verstanden."""
    w = (eingabe or "").strip().lower()
    if w == "-":
        state.set_coach_ruhe(None)
        logger.info("Coach-Ruhe/Zuschauer-Modus per /einstellungen aufgehoben.")
        return True
    if w.startswith("ruhe"):
        rest = w[4:].strip()
        tage = config.STILLE_RUHE_TAGE
        if rest:
            if not rest.isdigit():
                return False
            tage = min(90, max(1, int(rest)))
        bis = _ruhe_setzen(tage)
        logger.info("Coach-Ruhe per /einstellungen gesetzt bis %s.", bis)
        return True
    if w.startswith("zuschauer"):
        state.set_coach_ruhe("zuschauer")
        logger.info("Zuschauer-Modus per /einstellungen gesetzt.")
        return True
    return False


async def status_zeilen() -> list[str]:
    """Zeilen für /stats (Sub-Seite): nur DASS – nie den Inhalt."""
    zeilen = []
    status = ruhe_status_text()
    if status:
        zeilen.append(f"🔕 {status}")
    profil = await qdrant.get_user_profile("domina") or {}
    ci = profil.get("stille_checkin") or {}
    if ci.get("gefragt_am"):
        zustand = "beantwortet" if ci.get("antwort_am") else "unbeantwortet"
        zeilen.append(f"💬 Stille-Check-in vom {_dd_mm(ci.get('gefragt_am'))}: {zustand}")
    return zeilen
