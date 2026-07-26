"""
Psycho-Training Handler für die Domina.
Kombiniert Mindset, Situationsübungen und Reflexion.

/training → manuelle Übung
Täglicher Job → kurze Mindset-Frage oder Challenge
"""
import logging
from telegram import Update, Bot
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Training Typen
# ---------------------------------------------------------------------------

TRAINING_TYPEN = [
    "mindset",        # Selbstsicherheit & innere Haltung
    "situation",      # Situationsübung mit Feedback
    "reflexion",      # Gefühle & Gedanken in der Rolle
    "challenge",      # Kleine mentale Herausforderung für heute
]


async def _generiere_uebung(
    domina_profile: dict,
    training_typ: str,
    kontext: str = "",
    letzte_tasks: list = None,
) -> str:
    """Generiert eine Trainingsübung passend zum Typ und Level."""
    level = domina_profile.get("aktuelles_level", 1)
    interessen = domina_profile.get("interessen", [])
    ziele = domina_profile.get("ziele", "")
    erfahrungsstand = domina_profile.get("erfahrungsstand", "Anfänger")

    typ_prompts = {
        "mindset": f"""Erstelle eine kurze Mindset-Übung für eine Domina in Ausbildung.
Fokus: Selbstsicherheit und innere Haltung stärken.
Die Übung soll praktisch sein und heute umsetzbar.
Beispiele: Affirmationen, Körperhaltung, innere Visualisierung.""",

        "situation": f"""Erstelle eine Situationsübung für eine Domina.
Beschreibe eine realistische Situation mit ihrem Sklaven.
Frage dann: "Wie würdest du reagieren?"
Warte auf ihre Antwort und gib dann Feedback.
Die Situation soll zum Level {level} passen und diskret/kinderfrei sein.""",

        "reflexion": f"""Stelle der Domina eine tiefgehende Reflexionsfrage zu ihrer Rolle.
Die Frage soll zum Nachdenken anregen über:
- Ihre Gefühle in der Domina-Rolle
- Was ihr Energie gibt / nimmt
- Was sie noch lernen möchte
Nur EINE Frage stellen, keine Liste.""",

        "challenge": f"""Erstelle eine kleine mentale Challenge für heute.
Etwas das sie heute konkret ausprobieren kann.
Beispiele: "Gib heute eine Anweisung ohne Erklärung",
"Beobachte heute wie er auf deine Körpersprache reagiert"
Kurz, konkret, heute umsetzbar. Level {level} angepasst.""",
    }

    tasks_str = ""
    if letzte_tasks:
        tasks_liste = "\n".join(f"- {t}" for t in letzte_tasks)
        tasks_str = (
            f"\nZuletzt erledigte Aufgaben des Sklaven:\n{tasks_liste}\n"
            f"→ Beziehe dich auf diese Erfahrungen wenn es zum Training passt.\n"
        )

    from bot.prompts import coach_persona
    system = f"""Du gibst der Domina heute ein kleines Psycho-Training – wie eine vertraute Freundin, die ihr eine konkrete Übung mitgibt.

{coach_persona.fuer_coach_prompt()}

{typ_prompts.get(training_typ, typ_prompts['mindset'])}

Kompakt (max. 6 Sätze). Kein [AUFGABE: ...] Tag."""
    prompt = f"""Profil:
  Erfahrungsstand: {erfahrungsstand}
  {coach_persona.level_zeile(level)}
  Interessen: {', '.join(interessen) if interessen else 'nicht angegeben'}
  Ziele: {ziele}

{kontext}
{tasks_str}"""

    return await grok.simple(prompt, system=system, reasoning=True)


# ---------------------------------------------------------------------------
# /training Command
# ---------------------------------------------------------------------------

async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/training – startet eine Trainingseinheit."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    if not config.TRAINING_ENABLED:
        await update.message.reply_text(t("TRAINING_DEAKTIVIERT"))
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)

    # Bisherige Trainings laden für Kontext
    trainings = await qdrant.get_training_entries("domina", limit=3)
    kontext = ""
    if trainings:
        letzte = trainings[0]
        kontext = f"Letztes Training ({letzte.get('datum', '')[:10]}): {letzte.get('typ', '')} – {letzte.get('zusammenfassung', '')[:100]}"

    # Letzte erledigte Tasks laden
    # Serverseitig sortiert (Review D8/M4): sonst ab >100 erledigten Tasks
    # eine willkürliche Teilmenge statt der neuesten.
    erledigt_sorted = await qdrant.get_tasks_by_status(["erledigt"], limit=5, sort_by_datum=True)
    letzte_tasks = [task.get("aufgabe", "") for task in erledigt_sorted[:3] if task.get("aufgabe")]

    # Deterministischer Typ-Wechsel – letzten Typ aus Verlauf ermitteln
    letzter_typ = trainings[0].get("typ", "") if trainings else ""
    try:
        letzter_idx = TRAINING_TYPEN.index(letzter_typ)
        training_typ = TRAINING_TYPEN[(letzter_idx + 1) % len(TRAINING_TYPEN)]
    except ValueError:
        training_typ = TRAINING_TYPEN[0]

    await update.message.reply_text(
        t("TRAINING_WARTE", typ=training_typ.capitalize()), parse_mode="Markdown"
    )

    try:
        uebung = await _generiere_uebung(domina_profile, training_typ, kontext, letzte_tasks)
    except Exception as e:
        # Gezielte Meldung statt Generik-Fehler des error_handlers – wie im
        # daily_training-Job, der denselben Call absichert.
        logger.error("Fehler beim Generieren der Trainingsübung: %s", e)
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))
        return

    # State setzen für Antwort
    s = state.get(chat_id)
    s["training_typ"] = training_typ
    s["training_uebung"] = uebung
    state.set_mode(chat_id, "training_antwort")

    await update.message.reply_text(
        t("TRAINING_UEBUNG", typ=training_typ.capitalize(), uebung=uebung)
    )


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Antwort der Domina auf eine Trainingsübung."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()

    if text.lower() in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        s.pop("training_typ", None)
        s.pop("training_uebung", None)
        await update.message.reply_text(t("TRAINING_BEENDET"))
        return

    training_typ = s.get("training_typ", "reflexion")
    uebung = s.get("training_uebung", "")
    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)

    from bot.prompts import coach_persona, followup as fp
    feedback_system = f"""Die Domina hat gerade auf eine Trainingsübung geantwortet. Reagiere konkret darauf – wie eine vertraute Freundin.

{coach_persona.fuer_coach_prompt()}

2-4 Sätze, lass es fließen, keine nummerierte Liste. Geh konkret auf ihre Antwort ein – nicht generisch ermutigen. Bei Reflexion ein weiterführender Gedanke, bei Situation ein konkreter Hinweis, bei Challenge/Mindset eine knappe Beobachtung.
Kein [AUFGABE: ...] Tag."""
    feedback_prompt = f"""Trainingstyp: {training_typ}
Übung: {uebung}
{fp.nutzer_text('Ihre Antwort', text)}
Level: {level}"""

    try:
        feedback = await grok.simple(feedback_prompt, system=feedback_system, reasoning=True)

        # Training in Qdrant speichern
        await qdrant.save_training("domina", {
            "typ": training_typ,
            "uebung": uebung,        # vollständig speichern (kein Abschneiden)
            "antwort": text,         # vollständig speichern (kein Abschneiden)
            "feedback": feedback,    # vollständig speichern (kein Abschneiden)
            "zusammenfassung": f"{training_typ}: {text[:80]}",  # nur Kurz-Label/Embedding
            "level": level,
        })

        state.set_mode(chat_id, "chat")
        s.pop("training_typ", None)
        s.pop("training_uebung", None)

        await telegram_helper.reply_markdown_safe(update.message, t("TRAINING_FEEDBACK_PREFIX", feedback=feedback))
    except Exception as e:
        logger.error("Fehler beim Training-Feedback: %s", e)
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))


# ---------------------------------------------------------------------------
# Täglicher Training-Job (vom Scheduler aufgerufen)
# ---------------------------------------------------------------------------

async def daily_training(bot: Bot) -> None:
    """Sendet täglich eine kurze Mindset-Frage oder Challenge."""
    if not config.TRAINING_ENABLED:
        return

    domina_chat = paare.dom_chat_id()
    s = state.get(domina_chat)

    # Nicht senden wenn gerade ein anderer State aktiv
    if s.get("mode", "chat") != "chat":
        logger.info("Training übersprungen – Domina hat aktiven State: %s", s.get("mode"))
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    if not domina_profile:
        return

    # Letzte erledigte Tasks laden
    # Serverseitig sortiert (Review D8/M4): sonst ab >100 erledigten Tasks
    # eine willkürliche Teilmenge statt der neuesten.
    erledigt_sorted = await qdrant.get_tasks_by_status(["erledigt"], limit=5, sort_by_datum=True)
    letzte_tasks = [task.get("aufgabe", "") for task in erledigt_sorted[:3] if task.get("aufgabe")]

    # Täglich alternierend zwischen Challenge und Reflexion
    trainings_taeglich = await qdrant.get_training_entries("domina", limit=1)
    letzter_typ = trainings_taeglich[0].get("typ", "") if trainings_taeglich else ""
    training_typ = "reflexion" if letzter_typ == "challenge" else "challenge"

    try:
        uebung = await _generiere_uebung(domina_profile, training_typ, letzte_tasks=letzte_tasks)

        # State setzen
        s["training_typ"] = training_typ
        s["training_uebung"] = uebung
        state.set_mode(domina_chat, "training_antwort")

        await bot.send_message(
            chat_id=domina_chat,
            text=t("TRAINING_TAEGLICH", typ=training_typ.capitalize(), uebung=uebung),
        )
        logger.info("Tägliches Training gesendet: %s", training_typ)
    except Exception as e:
        logger.error("Fehler beim täglichen Training: %s", e)