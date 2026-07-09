"""
/hilfe – rollenspezifisches Hilfe-Menü.

Listet alle Commands gruppiert nach Funktion und zeigt nur was die Rolle nutzen kann.
Datenquelle: bot/commands_katalog.py (gemeinsam mit den BotCommand-Menüs in main.py).
"""
from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.commands_katalog import (
    DOMINA_GRUPPEN, SKLAVE_GRUPPEN, Eintrag,
    anzeige_command, anzeige_gruppe, anzeige_hilfe_text,
)
from bot.messages import t


def _escape(s: str) -> str:
    """Minimaler HTML-Escape für Telegram."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_gruppen(gruppen: list[tuple[str, list[Eintrag]]], titel: str) -> str:
    out = [f"<b>{_escape(titel)}</b>"]
    for name, eintraege in gruppen:
        sichtbar = [e for e in eintraege if e.in_hilfe]
        if not sichtbar:
            continue
        out.append(f"\n<b>{_escape(anzeige_gruppe(name))}</b>")
        for e in sichtbar:
            out.append(f"  <code>{_escape('/' + anzeige_command(e.command))}</code> – {_escape(anzeige_hilfe_text(e))}")
    return "\n".join(out)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hilfe – zeigt Commands je nach Rolle."""
    chat_id = str(update.effective_chat.id)

    if chat_id == paare.dom_chat_id():
        titel = "👑 Help for the Mistress" if config.BOT_LOCALE == "en" else "👑 Hilfe für die Herrin"
        text = _format_gruppen(DOMINA_GRUPPEN, titel)
    elif chat_id == paare.sub_chat_id():
        titel = "🔗 Help for the slave" if config.BOT_LOCALE == "en" else "🔗 Hilfe für den Sklaven"
        text = _format_gruppen(SKLAVE_GRUPPEN, titel)
    else:
        text = t("COMMON_NICHT_AUTORISIERT")

    await update.message.reply_text(text, parse_mode="HTML")
