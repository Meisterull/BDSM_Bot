"""
Strafen-Protokoll Handler – Domina kann vergangene Strafen einsehen.
"""
from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import qdrant
from bot.messages import t


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    strafen = await qdrant.get_strafen("sklave", limit=10)
    if not strafen:
        await update.message.reply_text(t("STRAFEN_KEINE"))
        return

    text = t("STRAFEN_TITEL")
    for s in strafen:
        datum = s.get("datum", "")[:10]
        aufgabe = s.get("aufgabe", "")[:50]
        # Vollständige Status-Map (D9/N23): 'abgelehnt' (reaktion._alternativ_senden)
        # erschien vorher fälschlich als „vorgeschlagen".
        status = {
            "angeordnet": "✅ angeordnet",
            "abgelehnt": "❌ abgelehnt",
        }.get(s.get("status"), "💭 vorgeschlagen")
        text += f"[{datum}] {status}\n_{aufgabe}_\n"
        # Die eigentliche Strafe stand bisher nur in der DB, nie im Protokoll
        # (Test-Befund F3) – ohne sie ist das Protokoll als Nachschlagewerk wertlos.
        strafe = " ".join((s.get("bestrafung_text") or "").split())
        if strafe:
            text += f"→ {strafe[:200]}\n"
        text += "\n"

    from bot.services import telegram_helper
    await telegram_helper.reply_markdown_safe(update.message, text)
