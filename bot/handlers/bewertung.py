"""
Bewertungs-Handler – Domina bewertet eine erledigte Aufgabe mit 1-5.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes
from bot import state
from bot.services import qdrant, telegram_helper, grok
from bot.messages import t

logger = logging.getLogger(__name__)

# Referenzen auf Fire-and-forget-Tasks halten, sonst GC sie evtl. vor Abschluss.
_BG_TASKS: set = set()


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    task_id = s.get("bewertung_task_id")

    if text not in ("1", "2", "3", "4", "5"):
        await update.message.reply_text(t("BEWERTUNG_1_5"))
        return

    bewertung = int(text)
    if task_id:
        await qdrant.update_task(task_id, {"domina_bewertung": bewertung})

    s.pop("bewertung_task_id", None)

    # Schwierigkeitsgrad-Anpassung prüfen
    try:
        await _pruefe_schwierigkeit(context, bewertung)
    except Exception as e:
        logger.error("Fehler bei Schwierigkeitsgrad-Prüfung: %s", e)

    # Ebene 2 (implizit lernen): bei Top-Bewertung Vorlieben-Notiz vorschlagen.
    if bewertung >= 5 and task_id:
        import asyncio
        _bg = asyncio.create_task(_vorlieben_notiz_vorschlagen(context.bot, task_id))
        _BG_TASKS.add(_bg)
        _bg.add_done_callback(_BG_TASKS.discard)

    # Optionalen Kommentar anfragen
    if task_id:
        s["kommentar_task_id"] = task_id
        state.set_mode(chat_id, "aufgabe_kommentar")
        await update.message.reply_text(t("BEWERTUNG_KOMMENTAR_FRAGE"))
    else:
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("BEWERTUNG_NOTIERT"))


async def _pruefe_schwierigkeit(context, bewertung: int) -> None:
    """Passt die Aufgaben-Schwierigkeit automatisch an Bewertungs-Durchschnitt an."""
    # sort_by_datum: ohne serverseitige Sortierung liefert scroll bei >100
    # erledigten Tasks eine willkürliche Teilmenge statt der neuesten.
    results = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
    letzte = sorted(
        [r for r in results if r.get("domina_bewertung")],
        key=lambda x: x.get("erteilt_am", ""), reverse=True
    )[:5]

    if len(letzte) < 5:
        return

    bewertungen = [t["domina_bewertung"] for t in letzte if "domina_bewertung" in t]
    avg = sum(bewertungen) / len(bewertungen)

    domina_profile = await qdrant.get_user_profile("domina") or {}
    schwierigkeit = domina_profile.get("aufgaben_schwierigkeit", "normal")

    neue_schwierigkeit = schwierigkeit
    hinweis = ""

    if avg >= 4.5 and schwierigkeit != "hoch":
        neue_schwierigkeit = "hoch"
        hinweis = t("BEWERTUNG_KOMPLEX_HOCH")
    elif avg <= 2.0 and schwierigkeit != "niedrig":
        neue_schwierigkeit = "niedrig"
        hinweis = t("BEWERTUNG_KOMPLEX_NIEDRIG")
    elif 2.5 <= avg <= 3.5 and schwierigkeit != "normal":
        neue_schwierigkeit = "normal"
        hinweis = t("BEWERTUNG_KOMPLEX_NORMAL")

    if neue_schwierigkeit != schwierigkeit:
        await qdrant.patch_profile_fields("domina", {"aufgaben_schwierigkeit": neue_schwierigkeit})
        await telegram_helper.send_domina(context.bot, hinweis)
        try:
            from bot.prompts import coach_persona
            tipp_system = (
                f"{coach_persona.fuer_strukturierten_output()}\n\n"
                f"Gib der Domina in 1-2 Sätzen einen konkreten Tipp, wie sie Aufgaben formulieren kann, "
                f"die besser zu ihr und ihrem Sklaven passen. Kein Markdown."
            )
            tipp = await grok.simple(
                f"Die Domina hat ihre letzten 5 Aufgaben im Schnitt mit {avg:.1f}/5 Sternen "
                f"bewertet. Die Aufgaben-Komplexität wurde auf '{neue_schwierigkeit}' angepasst.",
                system=tipp_system,
            )
            await telegram_helper.send_domina(context.bot, t("BEWERTUNG_TIPP_PREFIX", tipp=tipp))
        except Exception as e:
            logger.error("Fehler bei Bewertungs-Coaching-Tipp: %s", e)


async def _vorlieben_notiz_vorschlagen(bot, task_id: str) -> None:
    """Bei 5-Sterne-Bewertung: Grok formuliert eine Vorlieben-Notiz, die der Coach
    sich fuer zukuenftige Vorschlaege merken kann. Wird als 'pending' gespeichert
    und mit Ja/Nein-Buttons an die Domina geschickt."""
    from bot.handlers import coach_regeln as _cr
    try:
        task = await qdrant.get_task(task_id)
        if not task:
            return
        aufgabe = task.get("aufgabe", "")
        kategorie = task.get("kategorie", "")
        if not aufgabe:
            return

        system = """Die Domina hat eine Aufgabe mit 5/5 Sternen bewertet.

Formuliere daraus eine VERALLGEMEINERTE Vorlieben-Notiz, die der Coach sich
fuer zukuenftige Aufgaben-Vorschlaege merken kann. Maximal EIN Satz, konkret,
in der 2./3. Person ueber die Domina (z.B. "Sie mag Aufgaben mit X").

Wenn nichts Verallgemeinerbares ableitbar ist (z.B. zu spezifisch), antworte NUR mit:
KEINE_REGEL

Sonst nur die Notiz als reinen Text, ohne Anfuehrungszeichen."""
        prompt = f"Aufgabe (Kategorie: {kategorie or '?'}):\n{aufgabe[:600]}"

        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))  # Notiz-Ableitung: deterministisch
        if not antwort or antwort.upper().startswith("KEINE_REGEL"):
            return

        point_id = await qdrant.save_coach_regel(
            user_id="domina",
            text=antwort,
            typ="notiz",
            status="pending",
            quelle="abgeleitet_bewertung",
            kontext=f"5-Sterne-Aufgabe (Kategorie {kategorie}): {aufgabe[:200]}",
        )
        await _cr.sende_vorschlag(
            bot, point_id, antwort,
            kontext=f"5-Sterne-Bewertung der Aufgabe „{aufgabe[:80]}…“",
        )
    except Exception as e:
        logger.error("Fehler beim Vorlieben-Notiz-Vorschlag: %s", e)
