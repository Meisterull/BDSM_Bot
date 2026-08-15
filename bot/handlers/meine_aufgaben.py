"""
Manuelles Aufgaben-Menü für den Sklaven.

Der automatische Followup-Job stellt bewusst nur EINE Frage pro Tag (ruhig,
kein Verhör). Über /meineaufgaben kann der Sklave seine offenen Aufgaben
jederzeit selbst ansehen und abschließen – pro Aufgabe mit derselben
Followup-Frage. So lassen sich pro Tag mehrere Tasks abschließen, ohne dass
der Bot von sich aus mehrfach nachfragt.

Der Abschluss läuft über die bestehenden ✅/❌-Buttons aus followup_response,
deren Callback die task_id aus den Callback-Daten liest (modus-unabhängig).
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.prompts import followup as fp
from bot.handlers import followup_response
from bot.messages import t

logger = logging.getLogger(__name__)

# Aufgaben, die der Sklave selbst abschließen darf: noch nicht abgeschlossen.
# "gefragt" ist dabei, damit eine bereits gestellte (aber unbeantwortete) Frage
# erneut geöffnet werden kann und nichts hängen bleibt.
_OFFENE_STATUS = ["offen", "gefragt"]


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/meineaufgaben – listet offene Aufgaben des Sklaven mit Abschließen-Buttons."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    tasks = [t for t in await qdrant.get_tasks_by_status(_OFFENE_STATUS)
             if t.get("user_id", "sklave") == qdrant.mandanten_key("sklave") and t.get("qdrant_point_id")]
    if not tasks:
        await update.message.reply_text(t("MEINEAUFGABEN_KEINE"))
        return

    tasks.sort(key=lambda t: t.get("erteilt_am", ""))  # älteste zuerst

    lines = [t("MEINEAUFGABEN_TITEL")]
    buttons = []
    for i, task in enumerate(tasks, 1):
        aufgabe = task.get("aufgabe", "")
        kurz = aufgabe if len(aufgabe) <= 60 else aufgabe[:57] + "…"
        lines.append(f"{i}. {kurz}")
        buttons.append([InlineKeyboardButton(
            t("BUTTON_NR_ABSCHLIESSEN", nr=i), callback_data=f"meinetask:{task['qdrant_point_id']}"
        )])

    await telegram_helper.reply_markdown_safe(
        update.message,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Button: gewählte Aufgabe abschließen → Followup-Frage + ✅/❌-Buttons."""
    query = update.callback_query
    await query.answer()
    try:
        _, point_id = query.data.split(":", 1)
    except ValueError:
        return
    chat_id = str(query.message.chat_id)

    # Steckt der Sklave gerade in einer Gefühl-Abfrage für eine ANDERE Aufgabe,
    # den Tap NICHT durchlassen (Guard analog followup_response.callback): sonst
    # überschreibt er followup_task_id/Mode und Task A bliebe dauerhaft in
    # gefuehl_pending hängen (kein Job holt den Status ab, Heilung erst Neustart).
    if (state.get_mode(chat_id) == "gefuehl"
            and state.get(chat_id).get("followup_task_id") not in (None, point_id)):
        await query.message.reply_text(t("FOLLOWUP_ERST_BEANTWORTEN"))
        return

    task = await qdrant.get_task(point_id)
    if not task or task.get("status") not in _OFFENE_STATUS:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(t("MEINEAUFGABEN_NICHT_OFFEN"))
        return

    aufgabe = task.get("aufgabe", "")

    # Kontext für eine Frage in der Stimme der Herrin (wie der Auto-Job).
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    streak = sklave_profil.get("streak", 0)
    stimmung_entry = await qdrant.get_latest_stimmung("sklave")
    aktuelle_stimmung = stimmung_entry.get("zusammenfassung", "") if stimmung_entry else ""
    erteilt = task.get("erteilt_am", "")
    try:
        # Kalendertage in Bot-Zeitzone – UTC-Tage würden eine nach Mitternacht
        # erteilte Aufgabe fälschlich als "gestern" labeln.
        tz = ZoneInfo(config.TIMEZONE)
        if erteilt:
            erteilt_dt = datetime.fromisoformat(erteilt)
            if erteilt_dt.tzinfo is None:
                erteilt_dt = erteilt_dt.replace(tzinfo=timezone.utc)
            erteilt_d = erteilt_dt.astimezone(tz).date()
        else:
            erteilt_d = datetime.now(tz).date()
        _tage = (datetime.now(tz).date() - erteilt_d).days
        tage_her = _tage if 0 <= _tage <= 30 else 1
    except (ValueError, TypeError):
        tage_her = 1

    # Frage generieren – mit Roh-Text-Fallback bei Grok-Ausfall (sonst kein Abschluss möglich).
    try:
        prompt = fp.followup_frage(aufgabe, streak=streak, stimmung=aktuelle_stimmung, tage_her=tage_her)
        frage = await grok.simple(prompt)
    except Exception as e:
        logger.error("Manuelle Followup-Frage fehlgeschlagen, nutze Fallback: %s", e)
        frage = t("FALLBACK_FOLLOWUP_FRAGE", aufgabe=aufgabe)

    # Frage zuerst ZUSTELLEN, Status/Mode danach (D9/M2, Muster D8/M1): bei
    # Send-Fehler bleibt sonst ein 'gefragt'-Task ohne gestellte Frage und der
    # Sklave hängt im Followup-Mode. Buttons bleiben dann stehen → Re-Tap möglich.
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(frage, reply_markup=followup_response.frage_buttons(point_id))

    # Status/State wie beim Auto-Job: 'gefragt' verhindert eine zweite (automatische)
    # Frage am nächsten Morgen; followup_task_id ermöglicht zusätzlich den ja/nein-Text-Pfad.
    await qdrant.update_task(point_id, {"status": "gefragt"})
    # Direkt setzen statt set_followup_task: der Sklave hat GENAU diese Aufgabe per Button
    # gewählt – der Race-Guard (überspringt im Mode "followup" still) würde sonst die
    # Zuordnung auf eine ältere Aufgabe zeigen lassen und „ja" den falschen Task abschließen.
    s = state.get(chat_id)
    s["followup_task_id"] = point_id
    state.set_mode(chat_id, "followup")
