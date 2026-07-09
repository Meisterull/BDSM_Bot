from telegram import Update
from telegram.ext import ContextTypes
from bot import config
from bot.services import paare
from bot.scheduler.followup import _send_tiny_task_vorschlag
from bot.messages import t


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    await update.message.reply_text(t("TINYTASK_WARTE"))
    await _send_tiny_task_vorschlag(context.bot)
