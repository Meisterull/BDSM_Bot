"""
Ziel-Tracking Handler – /ziele zeigt Fortschritt zu den gesetzten Zielen.
Nur für Domina.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ziele – zeigt Ziele aus dem Onboarding + aktuellen Fortschritt."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    ziele = domina_profile.get("ziele", "")
    level = domina_profile.get("aktuelles_level", 1)
    erfahrungsstand = domina_profile.get("erfahrungsstand", "Anfänger")
    interessen = domina_profile.get("interessen", [])

    # Erst auf leere Ziele prüfen – sonst widersprechen sich Warte- und Leer-Text.
    if not ziele:
        await update.message.reply_text(t("ZIELE_KEINE"))
        return

    await update.message.reply_text(t("ZIELE_WARTE"))
    erledigte = await qdrant.get_completed_task_count("sklave")

    # Letzte erledigte Aufgaben für Kontext laden – serverseitig sortiert
    # (Review D8/M4: sonst ab >100 Tasks willkürliche Teilmenge)
    letzte_tasks_sorted = await qdrant.get_tasks_by_status(["erledigt"], limit=5, sort_by_datum=True)
    aufgaben_str = "\n".join(
        f"- {t.get('aufgabe', '')} (Gefühl: {t.get('gefuehl', 'unbekannt')})"
        for t in letzte_tasks_sorted
    ) or "Noch keine Aufgaben erledigt."

    from bot.prompts import coach_persona
    system = f"""Du schaust mit der Domina auf ihre Ziele – wie eine vertraute Freundin, die ehrlich einschätzt, wo sie steht.

{coach_persona.fuer_strukturierten_output()}

Sag ihr im Fließtext (kein Formular, keine Überschriften): wo sie bezogen auf ihre Ziele gerade steht, was konkret gut läuft, und EINEN nächsten Fokus, der sie ihren Zielen näher bringt.
Kompakt (max. 10 Sätze), ehrlich statt Schaum. Kein [AUFGABE: ...] Tag."""
    prompt = f"""Ziele der Domina (aus dem Onboarding):
{ziele}

Aktueller Stand:
  Level: {level} von 5 (1=Einsteigerin, 5=sehr erfahren)
  Erfahrungsstand: {erfahrungsstand}
  Interessen: {', '.join(interessen) if interessen else 'nicht angegeben'}
  Erledigte Aufgaben gesamt: {erledigte}

Letzte erledigte Aufgaben:
{aufgaben_str}"""

    try:
        analyse = await grok.simple(prompt, system=system, reasoning=True)
        await telegram_helper.reply_markdown_safe(
            update.message,
            t("ZIELE_PREFIX", ziele=ziele, analyse=analyse),
        )
    except Exception:
        logger.exception("Fehler bei /ziele")
        await update.message.reply_text(t("FEHLER_LADEN"))


async def send_ziel_erinnerung(bot) -> None:
    """
    Wird vom Scheduler aufgerufen (z.B. wöchentlich montags).
    Schickt der Domina eine kurze Ziel-Erinnerung.
    """
    domina_profile = await qdrant.get_user_profile("domina") or {}
    ziele = domina_profile.get("ziele", "")
    level = domina_profile.get("aktuelles_level", 1)
    erledigte = await qdrant.get_completed_task_count("sklave")

    if not ziele:
        return

    from bot.prompts import coach_persona
    system = f"""Schreibe der Domina eine kurze wöchentliche Ziel-Erinnerung – wie eine Freundin, die beiläufig nachhakt, wie es läuft.

{coach_persona.fuer_coach_prompt()}

Drei bis vier Sätze: die Ziele kurz in Erinnerung rufen und einen konkreten kleinen Anstoß für diese Woche geben. Kein [AUFGABE: ...] Tag."""
    prompt = (
        f"Ihre Ziele: {ziele}\n"
        f"Aktuelles Level: {level}/5\n"
        f"Erledigte Aufgaben: {erledigte}"
    )

    try:
        erinnerung = await grok.simple(prompt, system=system)
        # Re-Check NACH dem LLM-Await (TOCTOU): Safeword/Flow im Generierungs-Fenster
        if state.is_paused() or state.get_mode(paare.dom_chat_id()) not in ("chat", None):
            logger.info("Ziel-Erinnerung nach Generierung verworfen – Pause/Mode geändert.")
            return
        await telegram_helper.send_domina(
            bot, t("ZIELE_ERINNERUNG_PREFIX", erinnerung=erinnerung)
        )
    except Exception as e:
        logger.error("Fehler bei Ziel-Erinnerung: %s", e)