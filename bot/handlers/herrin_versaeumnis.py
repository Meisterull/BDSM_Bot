"""
Versäumnis der Herrin ⏳ (15.09.2026).

Scheitert eine Aufgabe, die die dominante Seite SELBST gebraucht hätte (Strap-on,
Pegging, gemeinsame Session …), weil SIE keine Zeit hatte oder es vergessen hat,
darf das nicht gegen den Sub zählen. Bisher lief bei „❌ Nicht erledigt" immer
die volle Malus-Kette (Streak-Reset, Wette weg, Bericht, Strafvorschlag, Spott)
– egal, an wem es lag.

Ablauf (Abzweig ganz oben in followup_response._handle_no):
  1. braucht_herrin(task): Kategorie-Kurzschluss (_HERRIN_KATEGORIEN) oder
     Grok-Klassifikation (temperature=0, im Zweifel JA). LLM-Ausfall → JA: eine
     überflüssige Rückfrage an den Sub ist billiger als ein ungerechter Malus.
  2. frage_stellen(): Rückfrage an den Sub in Herrin-Stimme mit zwei Buttons
       herrin:ich:<task>        → bisherige Kette unverändert (followup_response.malus_kette)
       herrin:vergessen:<task>  → versaeumnis_verbuchen()
     Mode "herrin_frage" (getippter Text „lag an mir" / „du hattest keine Zeit"
     geht auch). Der Task bleibt auf 'gefragt' + Marker herrin_frage_offen=True
     (Doppel-Tap-Guard im Payload, nicht im flüchtigen State). Verfällt der
     Mode (STALE_MODE_SECONDS), stellt die bestehende Recovery in main.py später
     wieder die normale Erledigt-Frage.
  3. versaeumnis_verbuchen(): KEIN Streak-Reset, KEINE verlorene Wette, KEIN
     Strafvorschlag, KEIN Status 'nicht_erledigt' – Vertrauens-Score, Lernkurve
     und Eskalationszähler bleiben unberührt.
       Task:  herrin_versaeumt += 1; unter HERRIN_MAX_VERSAEUMNISSE → status
              'offen' + Nachfrage in HERRIN_NACHFRAGE_TAGE; sonst 'verfallen_herrin'.
       Dom-Profil: Liste herrin_versaeumnisse (Stufe/Anzeige ohne Qdrant-Scan).
       Sub:   ein Satz in Herrin-Stimme („geht nicht auf deine Kappe").
       Dom:   Coach-Einschätzung (Stufe nach Gesamtzahl im Zählfenster) + Buttons
                herrinfehl:nachholen:<task> → bleibt offen (Nachfrage steht schon)
                herrinfehl:streichen:<task> → 'verfallen_herrin', Sub bekommt „vom Tisch"
              Wird IMMER gesendet, auch bei Coach-Ruhe/Zuschauer-Modus (Owner-
              Entscheid 15.09.: es geht um ihre eigene Aufgabe).
     Kettenglied: beim Versäumnis wartet die Kette einfach weiter; erst Verfall
     oder Streichen löst die bestehende Weiter/Abbruch-Frage aus.

Sichtbarkeit: Zählerzeile in /aufgaben (Dom-Seite), Fakten-Block für
/rueckblick und die 14-Tage-Lernkurve. Der Sub sieht nur die Herrin-Sätze.
Gate: config.HERRIN_VERSAEUMNIS (Default an).
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import qdrant, grok, telegram_helper
from bot.prompts import followup as fp
from bot.prompts import rollen
from bot.messages import t

logger = logging.getLogger(__name__)

MODE = "herrin_frage"
STATUS_VERFALLEN = "verfallen_herrin"

# Kategorien, die ohne die dominante Seite physisch nicht gehen – hier braucht es
# keine LLM-Klassifikation. Bewusst knapp: nur, wo IHR Körper/ihre Anwesenheit
# zwingend ist. Alles andere entscheidet der Klassifikator anhand des Wortlauts.
_HERRIN_KATEGORIEN = frozenset({
    "Strap_on", "Pegging", "Muschianbetung", "Arschanbetung",
    "Facesitting", "Smothering", "Gesichtsfick",
})

# Obergrenze der Versäumnis-Liste im Dom-Profil (älteste fallen raus).
_PROFIL_MAX = 50

# Freitext-Antworten auf die Rückfrage (Buttons sind der Hauptweg). Erst die
# „an ihr"-Formen prüfen: „ich glaube, du hattest keine Zeit" enthält auch „ich".
_AN_HERRIN = (
    "keine zeit", "vergessen", "an dir", "bei dir", "du hattest", "du warst", "du hast",
    "an ihr", "an ihm", "sie hatte", "er hatte", "nicht da", "verschoben",
    "you ", "your", "forgot", "no time", "she ", "he ",
)
_AN_MIR = (
    "an mir", "bei mir", "meine schuld", "mein fehler", "ich war", "ich hab", "ich habe",
    "ich bin", "selbst", "me", "my fault", "mine", "myself", "ich",
)


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


def _dd_mm(iso: str) -> str:
    dt = _parse(iso)
    if dt is None:
        return "?"
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.")


def frage_buttons(task_id: str) -> InlineKeyboardMarkup:
    """Rückfrage an den Sub: lag es an ihm oder an der Herrin?"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_HERRIN_ICH"), callback_data=f"herrin:ich:{task_id}"),
        InlineKeyboardButton(t("BUTTON_HERRIN_VERGESSEN"), callback_data=f"herrin:vergessen:{task_id}"),
    ]])


def entscheidung_buttons(task_id: str) -> InlineKeyboardMarkup:
    """Ein-Tipp-Entscheidung der Dom-Seite unter der Coach-Einschätzung."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_HERRIN_NACHHOLEN"), callback_data=f"herrinfehl:nachholen:{task_id}"),
        InlineKeyboardButton(t("BUTTON_HERRIN_STREICHEN"), callback_data=f"herrinfehl:streichen:{task_id}"),
    ]])


def _beenden(chat_id: str) -> None:
    """Mode nur zurücksetzen, wenn er noch UNSERER ist (Muster stille_checkin)."""
    s = state.get(chat_id)
    s.pop("herrin_frage_task_id", None)
    s["followup_task_id"] = None
    if state.get_mode(chat_id) == MODE:
        state.set_mode(chat_id, "chat")


def _brauchbar(text: str) -> bool:
    return bool(text) and len(text) >= 20 and "[AUFGABE" not in text.upper()


def klassifiziere_text(text: str) -> str | None:
    """'vergessen' (lag an der Herrin), 'ich' (lag am Sub) oder None (unklar)."""
    tl = f" {(text or '').strip().lower()} "
    if any(w in tl for w in _AN_HERRIN):
        return "vergessen"
    if any(f" {w} " in tl or tl.strip() == w for w in _AN_MIR):
        return "ich"
    return None


# ---------------------------------------------------------------------------
# Schritt 1: Braucht die Aufgabe die Herrin?
# ---------------------------------------------------------------------------

async def braucht_herrin(task: dict) -> bool:
    """Kategorie-Kurzschluss, sonst Grok (temperature=0, im Zweifel JA)."""
    kategorie = task.get("kategorie") or ""
    if kategorie in _HERRIN_KATEGORIEN:
        logger.info("Herrin-Beteiligung: JA per Kategorie %s", kategorie)
        return True
    aufgabe = (task.get("aufgabe") or "").strip()
    if not aufgabe:
        return False
    try:
        antwort = grok.clean_text(await grok.simple(
            fp.herrin_beteiligung_check(aufgabe[:1000], kategorie),
            temperature=0, max_tokens=10,
        ))
    except Exception:
        logger.exception("Herrin-Beteiligung: Klassifikation fehlgeschlagen – im Zweifel nachfragen")
        return True
    ergebnis = antwort.upper().startswith("JA")
    logger.info("Herrin-Beteiligung geprüft: %s (Kategorie %s)", "JA" if ergebnis else "NEIN",
                kategorie or "-")
    return ergebnis


# ---------------------------------------------------------------------------
# Schritt 2: Rückfrage an den Sub
# ---------------------------------------------------------------------------

async def frage_stellen(message, chat_id: str, task_id: str) -> None:
    """Rückfrage „an dir oder an mir?" – State/Marker erst NACH erfolgreichem Send."""
    await message.reply_text(t("HERRIN_FRAGE"), reply_markup=frage_buttons(task_id))
    await qdrant.update_task(task_id, {"herrin_frage_offen": True})
    s = state.get(chat_id)
    s["herrin_frage_task_id"] = task_id
    s["followup_task_id"] = None
    state.set_mode(chat_id, MODE)
    logger.info("Herrin-Rückfrage an den Sub gestellt (Task %s).", task_id)


async def _antwort(message, context, chat_id: str, task: dict, task_id: str, action: str) -> None:
    await qdrant.update_task(task_id, {"herrin_frage_offen": False})
    _beenden(chat_id)
    aufgabe = task.get("aufgabe", "")
    if action == "ich":
        from bot.handlers import followup_response  # lazy: zirkulärer Import
        logger.info("Herrin-Rückfrage: lag am Sub → Malus-Kette (Task %s).", task_id)
        await followup_response.malus_kette(message, context, chat_id, task_id, aufgabe)
        return
    await versaeumnis_verbuchen(message, context, chat_id, task, task_id)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons der Rückfrage (Rolle via main._callback_gate: Sub)."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, task_id = query.data.split(":", 2)
    except ValueError:
        return
    if action not in ("ich", "vergessen"):
        return
    chat_id = str(query.message.chat_id)
    task = await qdrant.get_task(task_id)
    if not task:
        await query.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
        return
    # Doppel-Tap-/Stale-Guard: nur eine noch OFFENE Rückfrage entscheiden – ein
    # zweiter Tap würde sonst Malus ODER Versäumnis erneut auslösen.
    if task.get("status") not in ("offen", "gefragt") or not task.get("herrin_frage_offen"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(t("MEINEAUFGABEN_NICHT_OFFEN"))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _antwort(query.message, context, chat_id, task, task_id, action)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text-Pfad im Mode herrin_frage („lag an mir" / „du hattest keine Zeit")."""
    chat_id = str(update.effective_chat.id)
    text = (update.message.text or "").strip()
    task_id = state.get(chat_id).get("herrin_frage_task_id")
    if not task_id:
        _beenden(chat_id)
        return
    task = await qdrant.get_task(task_id)
    if not task or not task.get("herrin_frage_offen") or task.get("status") not in ("offen", "gefragt"):
        _beenden(chat_id)
        await update.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
        return
    action = klassifiziere_text(text)
    if action is None:
        await update.message.reply_text(t("HERRIN_FRAGE_KLARSTELLUNG"))
        return
    await _antwort(update.message, context, chat_id, task, task_id, action)


# ---------------------------------------------------------------------------
# Schritt 3: Versäumnis verbuchen
# ---------------------------------------------------------------------------

async def _profil_liste() -> list[dict]:
    profil = await qdrant.get_user_profile("domina") or {}
    return list(profil.get("herrin_versaeumnisse") or [])


def _im_fenster(liste: list[dict], tage: int | None = None) -> list[dict]:
    grenze = _jetzt() - timedelta(days=tage or config.HERRIN_VERSAEUMNIS_FENSTER_TAGE)
    out = []
    for e in liste:
        dt = _parse(e.get("am", ""))
        if dt is not None and dt >= grenze:
            out.append(e)
    return out


async def _verbuche_im_profil(eintrag: dict) -> int:
    """Hängt den Eintrag an die Profil-Liste (gedeckelt) und gibt die Anzahl
    im Zählfenster INKLUSIVE des neuen Eintrags zurück."""
    liste = await _profil_liste()
    liste.append(eintrag)
    liste = liste[-_PROFIL_MAX:]
    await qdrant.patch_profile_fields("domina", {"herrin_versaeumnisse": liste})
    return len(_im_fenster(liste))


async def _markiere_verfallen_im_profil(task_id: str) -> None:
    liste = await _profil_liste()
    geaendert = False
    for e in liste:
        if e.get("task_id") == task_id and not e.get("verfallen"):
            e["verfallen"] = True
            geaendert = True
    if geaendert:
        await qdrant.patch_profile_fields("domina", {"herrin_versaeumnisse": liste})


async def _herrin_satz_an_sub(aufgabe: str, verfallen: bool) -> str:
    try:
        text = grok.clean_text(await grok.simple(
            fp.reaktion_herrin_versaeumt(aufgabe, verfallen), max_tokens=200))
    except Exception:
        logger.exception("Herrin-Satz (Versäumnis) fehlgeschlagen – Fallback")
        text = ""
    if not _brauchbar(text):
        text = t("FALLBACK_HERRIN_VERFALLEN" if verfallen else "FALLBACK_HERRIN_VERSAEUMT")
    return text


async def _coach_einschaetzung(bot, task_id: str, aufgabe: str, anzahl_task: int,
                               anzahl_gesamt: int, verfallen: bool) -> None:
    """Coach-Stimme an die Dom-Seite – bewusst OHNE Coach-Ruhe-Gate (Owner-Entscheid)."""
    s = rollen.sub()
    try:
        text = grok.clean_text(await grok.simple(
            fp.coach_versaeumnis(aufgabe, anzahl_task, anzahl_gesamt, verfallen,
                                 config.HERRIN_NACHFRAGE_TAGE, config.HERRIN_VERSAEUMNIS_FENSTER_TAGE),
            temperature=0.8, max_tokens=450,
        ))
    except Exception:
        logger.exception("Coach-Einschätzung (Versäumnis) fehlgeschlagen – Fallback")
        text = ""
    if not _brauchbar(text):
        if text:
            logger.info("Coach-Einschätzung verworfen (%d Zeichen) – Fallback-Text", len(text))
        key = "HERRIN_COACH_FALLBACK_VERFALLEN" if verfallen else "HERRIN_COACH_FALLBACK"
        text = t(key, aufgabe=aufgabe[:120], n=anzahl_task, tage=config.HERRIN_NACHFRAGE_TAGE,
                 sub_nom=s["label_nom"], sub_akk=s["label_akk"], poss=s["poss"] + "e")
    buttons = None if verfallen else entscheidung_buttons(task_id)
    try:
        await telegram_helper.send_domina(bot, text, parse_mode="Markdown", reply_markup=buttons)
    except Exception:
        logger.exception("Coach-Einschätzung konnte nicht zugestellt werden")
        return
    logger.info("Coach-Einschätzung (Versäumnis) gesendet: Task %s, %d. Mal, gesamt %d%s.",
                task_id, anzahl_task, anzahl_gesamt, " – verfallen" if verfallen else "")


async def _kette_pruefen(bot, task: dict) -> None:
    """Verfallenes/gestrichenes Kettenglied: bestehende Weiter/Abbruch-Frage."""
    if not task.get("kette_id"):
        return
    try:
        from bot.handlers import kette_adaptiv
        await kette_adaptiv.frage_bei_fehlschlag(bot, task)
    except Exception:
        logger.exception("Kette-Fehlschlag-Entscheidung (Versäumnis) konnte nicht angestoßen werden")


async def versaeumnis_verbuchen(message, context, chat_id: str, task: dict, task_id: str) -> None:
    """Die Aufgabe ist an der Herrin gescheitert: kein Malus für den Sub."""
    aufgabe = task.get("aufgabe", "")
    anzahl_task = int(task.get("herrin_versaeumt") or 0) + 1
    jetzt = _jetzt().isoformat()
    verfallen = anzahl_task >= max(1, config.HERRIN_MAX_VERSAEUMNISSE)
    updates: dict = {
        "herrin_versaeumt": anzahl_task,
        "herrin_versaeumt_am": list(task.get("herrin_versaeumt_am") or []) + [jetzt],
        "herrin_frage_offen": False,
    }
    if verfallen:
        updates.update(status=STATUS_VERFALLEN, verfallen_am=jetzt, herrin_entscheidung_offen=False)
    else:
        updates.update(
            status="offen",
            follow_up_datum=qdrant.followup_zeitpunkt_utc(config.HERRIN_NACHFRAGE_TAGE),
            herrin_entscheidung_offen=True,
        )
    await qdrant.update_task(task_id, updates)

    try:
        anzahl_gesamt = await _verbuche_im_profil({
            "am": jetzt, "task_id": task_id, "aufgabe": aufgabe[:120], "verfallen": verfallen,
        })
    except Exception:
        logger.exception("Versäumnis-Liste im Dom-Profil nicht schreibbar – Stufe aus Task-Zähler")
        anzahl_gesamt = anzahl_task
    logger.info("Herrin-Versäumnis verbucht: Task %s, %d. Mal (gesamt %d im Fenster)%s.",
                task_id, anzahl_task, anzahl_gesamt, " – verfallen" if verfallen else "")

    # Sub: ein Satz in Herrin-Stimme, kein Sticker, kein Spott.
    await message.reply_text(await _herrin_satz_an_sub(aufgabe, verfallen))

    # Dom: Coach-Einschätzung (+ Buttons, solange die Aufgabe noch lebt).
    await _coach_einschaetzung(context.bot, task_id, aufgabe, anzahl_task, anzahl_gesamt, verfallen)

    if verfallen:
        await _kette_pruefen(context.bot, {**task, **updates})


# ---------------------------------------------------------------------------
# Dom-Seite: Nachholen / Streichen
# ---------------------------------------------------------------------------

async def callback_domina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons unter der Coach-Einschätzung (Rolle via main._callback_gate: Dom)."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, task_id = query.data.split(":", 2)
    except ValueError:
        return
    if action not in ("nachholen", "streichen"):
        return
    task = await qdrant.get_task(task_id)
    if not task:
        await query.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    # Guard: schon entschieden ODER die Aufgabe lebt nicht mehr (der Sub hat sie
    # inzwischen erledigt / sie wurde gelöscht) → nichts mehr umschalten.
    if not task.get("herrin_entscheidung_offen") or task.get("status") not in ("offen", "gefragt"):
        if task.get("herrin_entscheidung_offen"):
            await qdrant.update_task(task_id, {"herrin_entscheidung_offen": False})
        await query.message.reply_text(t("HERRIN_ENTSCHEIDUNG_VERALTET"))
        return

    s = rollen.sub()
    if action == "nachholen":
        await qdrant.update_task(task_id, {"herrin_entscheidung_offen": False})
        await query.message.reply_text(t("HERRIN_NACHHOLEN_OK", sub_akk=s["label_akk"]))
        logger.info("Versäumnis-Entscheidung: nachholen (Task %s).", task_id)
        return

    jetzt = _jetzt().isoformat()
    await qdrant.update_task(task_id, {
        "herrin_entscheidung_offen": False, "status": STATUS_VERFALLEN, "verfallen_am": jetzt,
    })
    try:
        await _markiere_verfallen_im_profil(task_id)
    except Exception:
        logger.exception("Versäumnis-Liste: verfallen-Markierung fehlgeschlagen")
    await query.message.reply_text(t("HERRIN_STREICHEN_OK", sub_nom=s["label_nom"]))
    logger.info("Versäumnis-Entscheidung: gestrichen (Task %s).", task_id)
    try:
        await telegram_helper.send_sklave(
            context.bot, await _herrin_satz_an_sub(task.get("aufgabe", ""), verfallen=True))
    except Exception:
        logger.exception("Vom-Tisch-Satz an den Sub konnte nicht zugestellt werden")
    await _kette_pruefen(context.bot, {**task, "status": STATUS_VERFALLEN})


# ---------------------------------------------------------------------------
# Sichtbarkeit: /aufgaben, /rueckblick, Lernkurve
# ---------------------------------------------------------------------------

async def versaeumnisse_im_fenster(tage: int | None = None) -> list[dict]:
    return _im_fenster(await _profil_liste(), tage)


async def zaehler_zeile() -> str:
    """Kopfzeile für /aufgaben (Markdown) – leer, wenn im Fenster nichts hängen blieb."""
    if not config.HERRIN_VERSAEUMNIS:
        return ""
    try:
        liste = await versaeumnisse_im_fenster()
    except Exception:
        logger.exception("Versäumnis-Zähler nicht ladbar")
        return ""
    if not liste:
        return ""
    verfallen = sum(1 for e in liste if e.get("verfallen"))
    zusatz = t("AUFGABEN_HERRIN_ZAEHLER_VERFALLEN", v=verfallen) if verfallen else ""
    return t("AUFGABEN_HERRIN_ZAEHLER", n=len(liste),
             tage=config.HERRIN_VERSAEUMNIS_FENSTER_TAGE, verfallen=zusatz)


async def prompt_fakten(tage: int) -> str:
    """Zeilen für Coach-Prompts (/rueckblick, Lernkurve): welche Aufgaben in den
    letzten `tage` Tagen an der Dom-Seite hängen blieben. Leer = nichts."""
    if not config.HERRIN_VERSAEUMNIS:
        return ""
    try:
        liste = await versaeumnisse_im_fenster(tage)
    except Exception:
        logger.exception("Versäumnis-Fakten nicht ladbar")
        return ""
    if not liste:
        return ""
    zeilen = [
        f"- {e.get('aufgabe', '')[:80]} ({_dd_mm(e.get('am', ''))}"
        f"{', inzwischen verfallen' if e.get('verfallen') else ''})"
        for e in liste[-8:]
    ]
    return "\n".join(zeilen)
