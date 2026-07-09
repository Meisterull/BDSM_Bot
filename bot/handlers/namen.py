"""
Namen festlegen (Domina-only):
- /botname <Name>      – Name der Bot-Herrin (gilt für Sklaven- UND Coach-Seite)
- /sklavenname <Name>  – wie der Sklave angesprochen/genannt wird

Jeweils ohne Argument: aktuellen Wert anzeigen. Mit "-" : entfernen (zurück auf Default).
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import persona_config
from bot.messages import t
from bot.services import telegram_helper

logger = logging.getLogger(__name__)


async def botname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        aktuell = persona_config.bot_name()
        await update.message.reply_text(
            t("NAMEN_BOTNAME_ANZEIGE", aktuell=aktuell or "— (keiner, sie ist „deine Herrin“)")
        )
        return
    name = await persona_config.set_bot_name("" if arg == "-" else arg[:40])
    if name:
        await telegram_helper.reply_markdown_safe(update.message, t("NAMEN_BOTNAME_GESETZT", name=name))
    else:
        await update.message.reply_text(t("NAMEN_BOTNAME_ENTFERNT"))


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setup <Text> – beschreibt die reale Konstellation (Anatomie/Rollen/Ausstattung),
    damit der Bot nicht rät. Gilt für Sklaven- und Coach-Seite."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        aktuell = persona_config.setup_kontext()
        await update.message.reply_text(
            t("NAMEN_SETUP_ANZEIGE", aktuell=aktuell or "— (keiner)")
        )
        return
    txt = await persona_config.set_setup_kontext("" if arg == "-" else arg[:600])
    if txt:
        await update.message.reply_text(t("NAMEN_SETUP_GESETZT"))
    else:
        await update.message.reply_text(t("NAMEN_SETUP_ENTFERNT"))


async def sklavenname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        aktuell = persona_config.sklave_anrede()
        await update.message.reply_text(
            t("NAMEN_ANREDE_ANZEIGE", aktuell=aktuell or "— (neutral)")
        )
        return
    name = await persona_config.set_sklave_anrede("" if arg == "-" else arg[:40])
    if name:
        await telegram_helper.reply_markdown_safe(update.message, t("NAMEN_ANREDE_GESETZT", name=name))
    else:
        await update.message.reply_text(t("NAMEN_ANREDE_ENTFERNT"))
