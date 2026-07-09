"""
Tiny-Task Feedback Handler.

Wenn die Domina einen Tiny-Task-Vorschlag nicht weitergeleitet hat, fragt der Bot
abends nach dem Grund. Antwort wird in der knowledge_base am Vorschlag gespeichert
und fließt in zukünftige Vorschläge als 'aus Fehlern lernen'-Kontext ein.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, grok, kategorie_logik
from bot.messages import t

logger = logging.getLogger(__name__)

# Referenzen auf Fire-and-forget-Tasks halten, sonst GC sie evtl. vor Abschluss.
_BG_TASKS: set = set()

ANTWORT_UEBERNOMMEN = t("TINYFB_ANTWORT_UEBERNOMMEN")
ANTWORT_GUT = t("TINYFB_ANTWORT_GUT")


async def _positives_feedback(point_id: str, action: str) -> str:
    """Speichert 'uebernommen' bzw. 'gut' am Tiny-Task und liefert den Antwort-Text.
    Gemeinsame Logik fuer Button- und Text-Pfad. Verbucht zusätzlich das getrennte
    Domina-Präferenz-Signal für die Kategorien des Vorschlags."""
    tiny_task = await qdrant.get_tiny_task_by_id(point_id) or {}
    kategorien = tiny_task.get("kategorien", [])
    if action == "uebernommen":
        await qdrant.mark_tiny_task_status(point_id, "uebernommen")
        await kategorie_logik.record_domina_praeferenz(kategorien, "genutzt")
        return ANTWORT_UEBERNOMMEN
    await qdrant.mark_tiny_task_status(point_id, "gut_aber_ungenutzt", grund="Vorschlag war gut, heute aber nicht umgesetzt")
    await kategorie_logik.record_domina_praeferenz(kategorien, "gut")
    return ANTWORT_GUT


async def _vorschlag_aus_ablehnung(bot, tiny_task_inhalt: str, kategorien: list, grund: str) -> None:
    """Bei einer Ablehnung mit Begruendung: Grok formuliert daraus eine Regel-Suggestion.
    Wird als 'pending' gespeichert und mit Ja/Nein-Buttons an die Domina geschickt."""
    from bot.handlers import coach_regeln as _cr
    try:
        system = """Du analysierst eine Ablehnung eines BDSM-Aufgaben-Vorschlags.

Leite daraus eine VERALLGEMEINERTE Regel oder Notiz ab, an die sich der Coach
zukuenftig bei Vorschlaegen halten soll. Maximal EIN Satz, konkret, in du-Form.

Wenn aus der Begruendung keine allgemeine Regel ableitbar ist (z.B. nur Tagesform),
antworte NUR mit: KEINE_REGEL

Sonst nur die Regel als reinen Text, ohne Anfuehrungszeichen, ohne Erklaerung."""
        from bot.prompts import followup as fp
        prompt = (
            f"Vorschlag (Kategorien: {', '.join(kategorien) if kategorien else '?'}):\n"
            f"{tiny_task_inhalt}\n\n"
            f"{fp.nutzer_text('Grund der Ablehnung durch die Domina', grund)}"
        )

        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))  # Regel-Ableitung: deterministisch
        if not antwort or antwort.upper().startswith("KEINE_REGEL"):
            logger.info("Tiny-Task-Ablehnung: keine generalisierbare Regel ableitbar.")
            return

        # Als 'notiz' speichern (lockerer Hinweis), Status pending bis Domina bestaetigt
        point_id = await qdrant.save_coach_regel(
            user_id="domina",
            text=antwort,
            typ="notiz",
            status="pending",
            quelle="abgeleitet_ablehnung",
            kontext=f"Abgelehnter Vorschlag: {tiny_task_inhalt[:200]} | Grund: {grund[:200]}",
        )
        await _cr.sende_vorschlag(
            bot, point_id, antwort,
            kontext=f"abgelehnter Vorschlag mit Begruendung „{grund[:80]}“",
        )
    except Exception as e:
        logger.error("Fehler beim Erzeugen einer Regel aus Ablehnung: %s", e)


async def _ist_ablehnungsgrund(tiny_task_inhalt: str, text: str) -> bool:
    """Klassifiziert den Freitext der Domina im Feedback-Modus: echter
    Ablehnungsgrund zum Vorschlag ODER ein anderes Anliegen (Frage/Auftrag/
    neues Thema)? Vorher wurde JEDER Freitext als Ablehnungsgrund gespeichert –
    live vergiftet durch „Kannst du ihm den Wochenplan senden?" (Review D7, B2).
    Fail-safe: bei LLM-Fehlern wie bisher als Ablehnungsgrund behandeln."""
    from bot.prompts import followup as fp
    system = (
        "Der Bot hat die Domina gefragt, warum sie einen Aufgaben-Vorschlag nicht übernommen hat.\n"
        "Klassifiziere ihre Antwort:\n"
        "- ABLEHNUNG: eine Begründung oder Kritik zum Vorschlag (auch knapp: "
        "'zu langweilig', 'keine Zeit gehabt', 'passt gerade nicht')\n"
        "- ANDERES: ein anderes Anliegen – eine Frage, ein Auftrag an den Bot, "
        "Smalltalk oder ein neues Thema, das sich nicht auf den Vorschlag bezieht\n"
        "Antworte NUR mit ABLEHNUNG oder ANDERES."
    )
    prompt = (
        f"Vorschlag, um den es geht:\n{tiny_task_inhalt[:400]}\n\n"
        f"{fp.nutzer_text('Antwort der Domina', text)}"
    )
    try:
        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))
        if (antwort or "").upper().startswith("ANDERES"):
            return False
    except Exception:
        logger.exception("Feedback-Klassifikation fehlgeschlagen – behandle als Ablehnungsgrund")
    return True


async def frage_stellen(bot, tiny_task_payload: dict) -> None:
    """Wird vom Scheduler aufgerufen – fragt die Domina nach dem Grund."""
    point_id = tiny_task_payload.get("qdrant_point_id")
    inhalt = tiny_task_payload.get("inhalt", "")
    kategorien = tiny_task_payload.get("kategorien", [tiny_task_payload.get("kategorie", "?")])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("BUTTON_UEBERNOMMEN"), callback_data=f"tinyfb:uebernommen:{point_id}")],
        [InlineKeyboardButton(t("BUTTON_GUT_NICHT_HEUTE"), callback_data=f"tinyfb:gut:{point_id}")],
    ])
    await telegram_helper.send_domina(
        bot,
        t("TINYFB_FRAGE", kategorien=", ".join(kategorien), inhalt=inhalt),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    # Mode/ID erst NACH erfolgreichem Senden – sonst hinterlässt ein Sendefehler
    # einen Geister-Modus, der die nächste Domina-Nachricht als Feedback-Grund
    # fehlroutet (Trace 06.07., gleiches Muster wie followup_job vermeidet).
    domina_s = state.get(paare.dom_chat_id())
    domina_s["tiny_task_feedback_id"] = point_id
    state.set_mode(paare.dom_chat_id(), "tiny_task_feedback")


async def manuelle_frage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tinyfb – verschickt die Rückfrage zum neuesten offenen Tiny-Task-Vorschlag
    SOFORT über den laufenden Bot (wird also korrekt geloggt, State wird gesetzt →
    auch ein getippter Freitext-Grund wird danach erkannt). Nur Domina."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    pending = await qdrant.get_pending_tiny_tasks_for_feedback(hours_back=72)
    if not pending:
        await update.message.reply_text(t("TINYFB_KEIN_OFFENER"))
        return
    await frage_stellen(context.bot, pending[0])


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button für Übernommen / Gut."""
    query = update.callback_query
    await query.answer()
    _, action, point_id = query.data.split(":", 2)
    await query.edit_message_reply_markup(reply_markup=None)

    if action in ("uebernommen", "gut"):
        antwort = await _positives_feedback(point_id, action)
    else:
        return

    # Mode nur zurücksetzen, wenn er noch UNSERER ist – ein später Tap auf einen
    # alten Button darf keinen gerade aktiven anderen Flow killen.
    if state.get_mode(paare.dom_chat_id()) == "tiny_task_feedback":
        state.set_mode(paare.dom_chat_id(), "chat")
    state.get(paare.dom_chat_id()).pop("tiny_task_feedback_id", None)
    await query.message.reply_text(antwort, parse_mode="Markdown")
    logger.info("Tiny-Task-Feedback (Button) gespeichert (point_id=%s, action=%s)", point_id, action)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Antwort der Domina."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    point_id = s.get("tiny_task_feedback_id")

    if not point_id:
        state.set_mode(chat_id, "chat")
        return

    text_lower = text.lower()

    # Getipptes "abbrechen" nicht als Ablehnungsgrund persistieren (würde sonst
    # eine Regel-Ableitung triggern).
    if text_lower in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        s.pop("tiny_task_feedback_id", None)
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    if text_lower in ("übernommen", "uebernommen", "ja", "genutzt"):
        antwort = await _positives_feedback(point_id, "uebernommen")
    elif text_lower in ("gut", "ok", "passt"):
        antwort = await _positives_feedback(point_id, "gut")
    else:
        tiny_task = await qdrant.get_tiny_task_by_id(point_id) or {}
        tt_inhalt = tiny_task.get("inhalt", "")
        tt_kategorien = tiny_task.get("kategorien", [])
        # Kein Ablehnungsgrund, sondern anderes Anliegen → als normale Chat-
        # Nachricht an den Domina-Handler; die Feedback-Frage bleibt offen
        # (Mode + Buttons unangetastet), sie kann später noch antworten.
        if not await _ist_ablehnungsgrund(tt_inhalt, text):
            logger.info(
                "Tiny-Task-Feedback: Freitext als anderes Anliegen erkannt → Domina-Chat (point_id=%s)",
                point_id,
            )
            from bot.handlers import domina
            await domina.handle(update, context)
            return
        await qdrant.mark_tiny_task_status(point_id, "abgelehnt", grund=text)
        antwort = t("TINYFB_NOTIERT", grund=text[:80])
        # Getrenntes Domina-Präferenz-Signal: sie mochte diese Kategorien-Richtung nicht.
        await kategorie_logik.record_domina_praeferenz(tt_kategorien, "abgelehnt")
        # Ebene 2: implizit lernen – Grok schlaegt eine generalisierte Regel vor.
        if tt_inhalt:
            import asyncio
            _bg = asyncio.create_task(_vorschlag_aus_ablehnung(context.bot, tt_inhalt, tt_kategorien, text))
            _BG_TASKS.add(_bg)
            _bg.add_done_callback(_BG_TASKS.discard)

    state.set_mode(chat_id, "chat")
    s.pop("tiny_task_feedback_id", None)

    from bot.services import telegram_helper
    await telegram_helper.reply_markdown_safe(update.message, antwort)
    logger.info("Tiny-Task-Feedback gespeichert (point_id=%s)", point_id)
