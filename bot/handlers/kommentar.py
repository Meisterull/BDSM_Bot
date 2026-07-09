"""
Kommentar Handler – Domina kann optionalen Kommentar zu erledigter Aufgabe hinterlassen.
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet den optionalen Kommentar der Domina zu einer erledigten Aufgabe."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    task_id = s.get("kommentar_task_id")

    if not task_id:
        state.set_mode(chat_id, "chat")
        return

    # State aufräumen
    state.set_mode(chat_id, "chat")
    s.pop("kommentar_task_id", None)

    # Kommentar in die Stimme der Herrin gießen (Persona aus prompts/persona.py)
    from bot.prompts import persona, followup as fp
    system = (
        f"Formuliere den Kommentar der Herrin als Nachricht direkt an den Sklaven "
        f"über seine erledigte Aufgabe – Ich-Form, ein bis drei Sätze.\n\n"
        f"{persona.fuer_sklaven_prompt()}\n\n"
        f"WICHTIG: Behalte den Kern der Aussage bei, aber schütze persönliche Details "
        f"die nicht für den Sklaven bestimmt sind. Keine wörtlichen Zitate sensibler Informationen.\n\n"
        f"Kein [AUFGABE: ...] Tag. Kein Markdown."
    )
    try:
        kommentartext = await grok.simple(
            fp.nutzer_text("Original-Kommentar der Herrin", text), system=system
        )
        # Privacy-Check: wenn Grok-Aufruf leer/unbrauchbar, generischen Text statt Original
        if not kommentartext or len(kommentartext.strip()) < 5:
            kommentartext = "Zu deiner Aufgabe habe ich dir etwas zu sagen – behalt es im Hinterkopf."
    except Exception as e:
        logger.error("Fehler bei Kommentar-Formulierung: %s", e)
        # Niemals Original-Kommentar direkt senden (Privacy-Leak!)
        kommentartext = "Zu deiner Aufgabe habe ich dir etwas zu sagen – behalt es im Hinterkopf."

    # Kommentar in Qdrant speichern – mit eigenem Zeitstempel: die wöchentliche
    # Kommentar-Analyse filtert nach kommentar_am, nicht nach erteilt_am (sonst
    # fallen Kommentare zu >7 Tage alten Aufgaben für immer durch, Trace 06.07.)
    await qdrant.update_task(task_id, {
        "domina_kommentar": kommentartext,
        "kommentar_am": datetime.now(timezone.utc).isoformat(),
    })

    # Kommentar an Sklaven senden
    await telegram_helper.send_sklave(
        context.bot, t("KOMMENTAR_PREFIX", kommentar=kommentartext),
    )

    await update.message.reply_text(t("KOMMENTAR_GESENDET"))


async def ueberspringen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Überspringt den optionalen Kommentar-State."""
    chat_id = str(update.effective_chat.id)
    if not paare.ist_autorisiert(chat_id):
        return
    s = state.get(chat_id)

    if s.get("mode") == "aufgabe_kommentar":
        state.set_mode(chat_id, "chat")
        s.pop("kommentar_task_id", None)
        await update.message.reply_text(t("KOMMENTAR_UEBERSPRUNGEN"))
