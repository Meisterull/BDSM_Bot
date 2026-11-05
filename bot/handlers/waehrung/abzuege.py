"""
Punkteabzug bei nicht erledigten Aufgaben.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot import state
from bot.services import paare, telegram_helper, waehrung
from bot.messages import t
from bot.handlers.waehrung import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Abzüge
# ---------------------------------------------------------------------------

async def abzug_nicht_erledigt() -> dict | None:
    """Fester Abzug bei „nicht erledigt" (malus_kette). None bei 0/Fehler."""
    if waehrung.ABZUG_NICHT_ERLEDIGT <= 0:
        return None
    try:
        return await waehrung.buchen(-waehrung.ABZUG_NICHT_ERLEDIGT, "nicht erledigt")
    except Exception:
        logger.exception("Abzug nicht-erledigt fehlgeschlagen")
        return None


def abzug_buttons(strafe_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ABZUG", betrag=b), callback_data=f"punkteabzug:{b}:{strafe_id}")
        for b in waehrung.STRAFE_ABZUEGE
    ]])


def abzug_anbieten(strafe_id: str) -> None:
    """Strafvorschlag-Kennung als offen merken (Dom-State, einmal pro Vorschlag)."""
    s = state.get(paare.dom_chat_id())
    offen = [x for x in (s.get("abzug_offen") or []) if x != strafe_id]
    s["abzug_offen"] = (offen + [strafe_id])[-kern._OFFEN_MAX:]


async def callback_punkteabzug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """−25 / −50 unter dem Strafvorschlag (Rolle via main._callback_gate: Dom)."""
    query = update.callback_query
    await query.answer()
    try:
        _, betrag_s, strafe_id = query.data.split(":", 2)
        betrag = int(betrag_s)
    except ValueError:
        return
    if betrag not in waehrung.STRAFE_ABZUEGE:
        return
    s = state.get(str(update.effective_chat.id))
    offen = list(s.get("abzug_offen") or [])
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if strafe_id not in offen:
        await query.message.reply_text(t("ABZUG_VERALTET"))
        return
    s["abzug_offen"] = [x for x in offen if x != strafe_id]
    buchung = await waehrung.buchen(-betrag, f"Strafe {strafe_id[:8]}")
    await query.message.reply_text(t("ABZUG_DOM_OK", betrag=-buchung["delta"], stand=buchung["neu"]))
    try:
        await telegram_helper.send_sklave(
            context.bot, t("ABZUG_SUB", betrag=-buchung["delta"], stand=buchung["neu"]))
    except Exception:
        logger.exception("Abzug-Meldung an den Sub fehlgeschlagen")
    await kern.nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)
