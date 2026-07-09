"""
Resurface Handler – Callback für 'Heute vor 3 Monaten' Re-Issue.

Domina kann via Inline-Button einen alten gut bewerteten Task erneut
als Task an den Sklaven erteilen.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, kategorie_logik
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet Resurface-Buttons."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    if action == "skip":
        await query.message.reply_text(t("RESURFACE_UEBERSPRUNGEN"))
        state.get(paare.dom_chat_id()).pop("resurface_task_id", None)
        return

    if action != "erteilen":
        return

    task_id = parts[2] if len(parts) > 2 else state.get(paare.dom_chat_id()).get("resurface_task_id")
    if not task_id:
        await query.message.reply_text(t("RESURFACE_STATE_WEG"))
        return

    alt = await qdrant.get_task(task_id)
    if not alt:
        await query.message.reply_text(t("RESURFACE_NICHT_GEFUNDEN"))
        return

    aufgabe = alt.get("aufgabe", "")
    kategorie = alt.get("kategorie", "allgemein")
    level = alt.get("level", 3)

    # Dislike-Check: Kategorie darf nicht auf Dislike-Liste stehen
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    dislike_kategorien = kategorie_logik.dislike_kategorien(sklave_profil)
    if kategorie in dislike_kategorien:
        await telegram_helper.reply_markdown_safe(query.message, t("RESURFACE_DISLIKE", kategorie=kategorie_logik.anzeige_name(kategorie)))
        state.get(paare.dom_chat_id()).pop("resurface_task_id", None)
        return

    # Neuen Task speichern (gemeinsame Factory)
    await qdrant.erstelle_task(
        aufgabe, kategorie, level,
        quelle="resurface", extra={"resurface_von": task_id},
    )

    state.get(paare.dom_chat_id()).pop("resurface_task_id", None)

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe), max_tokens=250)
    except Exception as e:
        logger.error("aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
        anweisung = aufgabe
    await telegram_helper.send_sklave(
        context.bot, t("RESURFACE_PREFIX", anweisung=anweisung),
        voice_text=anweisung,
    )
    await query.message.reply_text(
        t("RESURFACE_ERTEILT", kategorie=kategorie_logik.anzeige_name(kategorie)), parse_mode="Markdown",
    )
    logger.info("Resurface-Task neu erteilt (Original: %s)", task_id)
