"""
Lerntagebuch Handler – /lerntagebuch erzwingt einen manuellen Verdichtungs-Lauf
über die letzten 7 Tage (oder die angegebene Tagezahl) der Domina-Coach-Gespräche
und speichert das Ergebnis in der knowledge_base als Langzeit-Wissen für den Coach.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.scheduler.followup import generiere_lerntagebuch
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    # Optionales Argument: /lerntagebuch 14  → letzte 14 Tage
    days = 7
    if context.args:
        try:
            days = max(1, min(90, int(context.args[0])))
        except ValueError:
            pass

    await update.message.reply_text(t("LERNTAGEBUCH_WARTE", days=days))

    result = await generiere_lerntagebuch(days=days, min_eintraege=1)

    if result["status"] == "leer":
        await update.message.reply_text(t("LERNTAGEBUCH_LEER", zeitraum=result["zeitraum"]))
        return

    if result["status"] == "fehler":
        await update.message.reply_text(
            t("LERNTAGEBUCH_FEHLER", fehler=result.get("fehler", "unbekannt"))
        )
        return

    inhalt = result["inhalt"]
    zeitraum = result["zeitraum"]
    anzahl = result["eintraege"]

    # Telegram-Limit (4096) berücksichtigen – Platz für Header UND den
    # angehängten Gekürzt-Hinweis reservieren (sonst >4096 → "message too long").
    header = t("LERNTAGEBUCH_HEADER", zeitraum=zeitraum, anzahl=anzahl)
    gekuerzt_hinweis = t("LERNTAGEBUCH_GEKUERZT")
    body = inhalt
    max_body = 4096 - len(header) - len(gekuerzt_hinweis) - 10
    if len(body) > max_body:
        body = body[:max_body] + gekuerzt_hinweis

    from bot.services import telegram_helper
    await telegram_helper.reply_markdown_safe(update.message, header + body)
