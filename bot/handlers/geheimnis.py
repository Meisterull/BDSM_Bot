"""
Geheimnis Handler – Domina kann geheime Informationen für späteren Zeitpunkt hinterlegen.
"""
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant
from bot.messages import t


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    state.set_mode(chat_id, "geheimnis_text")

    await update.message.reply_text(t("GEHEIMNIS_START"), parse_mode="Markdown")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Eingabe von Geheimnis-Text und Enthüllungs-Datum."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    mode = s.get("mode")

    if mode == "geheimnis_text":
        s["geheimnis_text_inhalt"] = text
        state.set_mode(chat_id, "geheimnis_datum")
        await update.message.reply_text(t("GEHEIMNIS_DATUM_FRAGE"), parse_mode="Markdown")

    elif mode == "geheimnis_datum":
        geheimnis_text = s.get("geheimnis_text_inhalt", "")
        enthuellung_datum = _parse_datum(text)

        if enthuellung_datum is None:
            await update.message.reply_text(t("GEHEIMNIS_DATUM_FEHLER"), parse_mode="Markdown")
            return

        # State aufräumen
        state.set_mode(chat_id, "chat")
        s.pop("geheimnis_text_inhalt", None)

        # In Qdrant speichern
        await qdrant.save_geheimnis({
            "text": geheimnis_text,
            "enthuellung_datum": enthuellung_datum.isoformat(),
            "status": "wartend",
            "erstellt_am": datetime.now(timezone.utc).isoformat(),
        })

        datum_str = enthuellung_datum.strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            t("GEHEIMNIS_GESPEICHERT", datum=datum_str), parse_mode="Markdown",
        )


def _parse_datum(text: str) -> datetime | None:
    """Parst ein Datum aus Text. Unterstützt TT.MM.YYYY HH:MM und 'in X Tagen'."""
    text = text.strip()

    # "in X Tagen" Format
    match = re.match(r"in\s+(\d+)\s+tag(?:en)?", text, re.IGNORECASE)
    if match:
        tage = int(match.group(1))
        return datetime.now(timezone.utc) + timedelta(days=tage)

    # Eingabe ist lokale Zeit (Deployment-Zeitzone) → als solche interpretieren, dann nach UTC.
    _berlin = ZoneInfo(config.TIMEZONE)

    # TT.MM.YYYY HH:MM Format
    try:
        dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
        return dt.replace(tzinfo=_berlin).astimezone(timezone.utc)
    except ValueError:
        pass

    # TT.MM.YYYY Format (ohne Uhrzeit, Default 12:00)
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
        return dt.replace(hour=12, minute=0, tzinfo=_berlin).astimezone(timezone.utc)
    except ValueError:
        pass

    return None
