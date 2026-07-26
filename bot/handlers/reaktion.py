"""
Reaktion Handler.
Verarbeitet die Antwort der Domina nach Nicht-Erledigung einer Aufgabe.
"""
import logging
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import qdrant, telegram_helper, grok, synonyme
from bot.messages import t

logger = logging.getLogger(__name__)

_JA = synonyme.JA + ("✅",)
_NEIN = synonyme.NEIN + ("❌",)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    text_lower = text.lower()
    s = state.get(chat_id)
    task_id = s.get("reaktion_fuer_task_id")

    if not task_id:
        state.set_mode(chat_id, "chat")
        return

    strafe_id = s.get("strafe_id")

    # Bestrafungs-Bestätigung: Ja/Nein-Flow
    if strafe_id:
        if text_lower in _JA:
            await _bestaetigen(update, context, chat_id, task_id, strafe_id, s)
        elif text_lower in _NEIN:
            state.set_mode(chat_id, "reaktion_alternativ")
            await update.message.reply_text(t("REAKTION_ALTERNATIV_FRAGE"))
        else:
            # Freitext → direkt als alternative Bestrafung weiterleiten
            await _alternativ_senden(update, context, chat_id, task_id, strafe_id, s, text)
        return

    # Kein Bestrafungsvorschlag vorhanden → direkt weiterleiten
    await _weiterleiten(update, context, chat_id, task_id, s, text)


async def handle_alternativ(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die alternative Bestrafung nach 'Nein'."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    task_id = s.get("reaktion_fuer_task_id")
    strafe_id = s.get("strafe_id")

    if not task_id:
        state.set_mode(chat_id, "chat")
        return

    await _alternativ_senden(update, context, chat_id, task_id, strafe_id, s, text)


async def _bestaetigen(update, context, chat_id, task_id, strafe_id, s) -> None:
    """Bestätigt den ursprünglichen Bestrafungsvorschlag und leitet ihn an den Sklaven weiter."""
    strafe = await qdrant.get_strafe(strafe_id)
    bestrafung_text = strafe.get("bestrafung_text", "") if strafe else ""

    # Status bleibt "nicht_erledigt" (Review D8/H5): der frühere Flip auf
    # "reaktion_gesendet" hatte keinen einzigen Leser und ließ den Fehlschlag
    # aus Eskalations-Streak, Lernkurve und Tonlagen-Zählung verschwinden.
    # Die Reaktion ist über domina_reaktion/reaktion_am am Task markiert.
    await qdrant.update_task(task_id, {
        "domina_reaktion": "bestätigt",
        "reaktion_am": datetime.now(timezone.utc).isoformat(),
    })
    await qdrant.update_strafe(strafe_id, {"status": "angeordnet"})
    _clear_state(chat_id, s)

    try:
        from bot.prompts import persona
        system = (
            f"Du bist die Herrin. Ordne deinem Sklaven jetzt die folgende Bestrafung an – direkt, in der Ich-Form.\n\n"
            f"{persona.fuer_sklaven_prompt()}\n\n"
            f"Kein [AUFGABE: ...] Tag."
        )
        nachricht = await grok.simple(
            f"Bestrafung (Kontext, formuliere als deine eigene Anordnung): {bestrafung_text}",
            system=system,
        )
    except Exception as e:
        logger.error("Fehler bei Bestrafungs-Reformulierung: %s", e)
        nachricht = bestrafung_text

    await telegram_helper.send_sklave(context.bot, nachricht)
    await update.message.reply_text(t("REAKTION_ANGEORDNET"))


async def _alternativ_senden(update, context, chat_id, task_id, strafe_id, s, text) -> None:
    """Leitet eine alternative Reaktion der Domina weiter."""
    await qdrant.update_task(task_id, {
        "domina_reaktion": text,
        "reaktion_am": datetime.now(timezone.utc).isoformat(),
    })
    if strafe_id:
        # Stale-Guard (Review D6): nur einen noch VORGESCHLAGENEN Eintrag auf
        # "abgelehnt" setzen – eine parallel schon angeordnete/erledigte Strafe
        # nicht blind überschreiben.
        strafe = await qdrant.get_strafe(strafe_id)
        if strafe and strafe.get("status", "vorgeschlagen") == "vorgeschlagen":
            await qdrant.update_strafe(strafe_id, {"status": "abgelehnt"})
        elif strafe:
            logger.info("Strafe %s nicht auf 'abgelehnt' gesetzt (Status: %s)",
                        strafe_id, strafe.get("status"))
    _clear_state(chat_id, s)

    await _weiterleiten(update, context, chat_id, task_id, s, text, clear=False)


async def _weiterleiten(update, context, chat_id, task_id, s, text, clear=True) -> None:
    """Reformuliert und leitet eine Domina-Nachricht an den Sklaven weiter."""
    if clear:
        await qdrant.update_task(task_id, {
            "domina_reaktion": text,
            "reaktion_am": datetime.now(timezone.utc).isoformat(),
        })
        _clear_state(chat_id, s)

    try:
        from bot.prompts import persona, followup as fp
        system = (
            f"Du bist die Herrin. Reagiere jetzt direkt auf deinen Sklaven, der eine Aufgabe nicht erledigt hat – "
            f"Ich-Form, ein bis drei Sätze.\n\n"
            f"{persona.fuer_sklaven_prompt()}\n\n"
            f"Kein [AUFGABE: ...] Tag."
        )
        nachricht = await grok.simple(
            fp.nutzer_text("Was du ihm im Kern ausrichten willst (formuliere als deine eigene Ansprache)", text),
            system=system,
        )
    except Exception as e:
        logger.error("Fehler bei Reaktions-Reformulierung: %s", e)
        nachricht = text

    await telegram_helper.send_sklave(context.bot, nachricht)
    await update.message.reply_text(t("REAKTION_WEITERGELEITET"))


def _clear_state(chat_id: str, s: dict) -> None:
    state.set_mode(chat_id, "chat")
    s["reaktion_fuer_task_id"] = None
    s.pop("strafe_id", None)
