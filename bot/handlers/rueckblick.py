"""
Rückblick Handler – /rueckblick analysiert die letzten 2-4 Wochen.
Nur für Domina.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot import config
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rueckblick – Grok analysiert Entwicklung der letzten Wochen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    await update.message.reply_text(t("RUECKBLICK_WARTE"))

    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)
    ziele = domina_profile.get("ziele", "nicht angegeben")

    # Erledigte Aufgaben der letzten 4 Wochen – serverseitig sortiert
    # (Review D8/M4: sonst ab >100 Tasks willkürliche Teilmenge)
    erledigte_sorted = await qdrant.get_tasks_by_status(["erledigt"], limit=20, sort_by_datum=True)

    # Nicht erledigte Aufgaben
    nicht_erledigt = await qdrant.get_tasks_by_status(["nicht_erledigt"], limit=10, sort_by_datum=True)

    # Fortschritt Einträge
    progress = await qdrant.get_progress_entries("domina", limit=10)

    # Aufgaben nach Kategorie zählen
    kategorien: dict = {}
    for task in erledigte_sorted:
        kat = task.get("kategorie", "allgemein")
        kategorien[kat] = kategorien.get(kat, 0) + 1

    # Gefühle sammeln
    gefuehle = [t.get("gefuehl", "") for t in erledigte_sorted if t.get("gefuehl")][:10]

    # Aufgaben-Text für Prompt
    aufgaben_str = "\n".join(
        f"- {t.get('aufgabe', '')} | Gefühl: {t.get('gefuehl', 'unbekannt')} | Kat: {t.get('kategorie', 'allgemein')}"
        for t in erledigte_sorted[:10]
    ) or "Keine erledigten Aufgaben."

    nicht_erledigt_str = "\n".join(
        f"- {t.get('aufgabe', '')}"
        for t in nicht_erledigt
    ) or "Keine."

    kategorien_str = ", ".join(f"{k}: {v}x" for k, v in kategorien.items()) or "keine Daten"

    from bot.prompts import coach_persona
    system = f"""Du schaust mit der Domina auf die letzten Wochen zurück – wie eine vertraute Freundin, die ehrlich Bilanz zieht.

{coach_persona.fuer_strukturierten_output()}

Geh im Fließtext (keine Überschriften, kein Formular, keine Bullet-Struktur) darauf ein: was in den letzten Wochen passiert ist, was gut läuft, wo noch Luft ist, wie sie sich als Domina entwickelt hat und worauf sie sich in den nächsten Wochen konzentrieren sollte.
Ehrlich statt Motivations-Schaum. Maximal 12 Sätze. Kein [AUFGABE: ...] Tag."""
    prompt = f"""Profil:
  Level: {level}/5 (1=Einsteigerin, 5=sehr erfahren)
  Ziele: {ziele}

Erledigte Aufgaben (letzte Wochen):
{aufgaben_str}

Nicht erledigte Aufgaben:
{nicht_erledigt_str}

Aufgaben nach Kategorie: {kategorien_str}
Gesammelte Gefühle des Sklaven: {', '.join(gefuehle[:5]) if gefuehle else 'keine'}"""

    try:
        analyse = await grok.simple(prompt, system=system, reasoning=True)
        await telegram_helper.reply_markdown_safe(
            update.message, t("RUECKBLICK_PREFIX", analyse=analyse)
        )
    except Exception as e:
        logger.error("Fehler beim Rückblick: %s", e)
        await update.message.reply_text(t("FEHLER_LADEN"))