"""
Dauer-Anweisungen 🕰 – Aufgaben über Stunden mit unangekündigten Zwischen-Checks.

/dauer <Stunden 1-48> <Anweisung> (Domina): die Anweisung geht sofort an den
Sklaven (Herrin-Stimme + Endzeit), läuft als Task quelle='dauer' mit
`dauer_bis`. Der dauer_job (15-Min-Intervall):
  1. fragt bei abgelaufener Zeit nach („durchgehalten?") und hängt die Antwort
     in den NORMALEN Followup-Flow (set_followup_task → ja/nein → Punkte/
     Bestrafung wie immer; Status vorher auf 'gefragt', wie followup_job),
  2. schickt zwischendurch zufällige Kontroll-Nachrichten der Herrin („Noch
     dran?") – gedrosselt (DAUER_CHECK_ABSTAND_MIN), nur im Blitz-Fenster ∩
     kinderfreie Zeiten, nie in den ersten 30/letzten 15 Minuten.
"""
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, kategorie_logik
from bot.prompts import followup as fp
from bot.prompts import persona
from bot.messages import t

logger = logging.getLogger(__name__)

MIN_STUNDEN, MAX_STUNDEN = 1, 48


async def starten(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dauer <Stunden> <Anweisung> – Dauer-Aufgabe erteilen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    args = list(context.args or [])
    if not args or not args[0].isdigit() or not (MIN_STUNDEN <= int(args[0]) <= MAX_STUNDEN):
        await update.message.reply_text(
            t("DAUER_HILFE", min=MIN_STUNDEN, max=MAX_STUNDEN), parse_mode="Markdown")
        return
    stunden = int(args.pop(0))
    anweisung_text = " ".join(args).strip()
    if not anweisung_text:
        await update.message.reply_text(
            t("DAUER_HILFE", min=MIN_STUNDEN, max=MAX_STUNDEN), parse_mode="Markdown")
        return

    try:
        kategorie = await kategorie_logik.klassifiziere(anweisung_text)
    except Exception:
        logger.exception("Dauer-Klassifikation fehlgeschlagen – 'allgemein'")
        kategorie = "allgemein"

    domina_profil = await qdrant.get_user_profile("domina") or {}
    level = domina_profil.get("aktuelles_level", 3)
    ende = datetime.now(timezone.utc) + timedelta(hours=stunden)
    ende_lokal = _lokal(ende)
    # follow_up_datum hinter das Ende schieben – die echte Nachfrage macht der
    # dauer_job zur Endzeit, der tägliche followup_job soll nicht mittendrin fragen.
    await qdrant.erstelle_task(
        anweisung_text, kategorie, level, quelle="dauer",
        followup_in_tagen=(stunden // 24) + 2,
        extra={"dauer_bis": ende.isoformat(), "dauer_stunden": stunden, "dauer_letzter_check": ""},
    )

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(anweisung_text), max_tokens=250)
    except Exception:
        logger.exception("aufgabe_an_sklaven (Dauer) fehlgeschlagen – Rohtext")
        anweisung = anweisung_text
    await telegram_helper.send_sklave(
        context.bot,
        t("DAUER_AN_SKLAVEN", anweisung=anweisung, stunden=stunden, bis=ende_lokal),
        parse_mode="Markdown", voice_text=anweisung,
    )
    await update.message.reply_text(
        t("DAUER_ERTEILT", stunden=stunden, bis=ende_lokal), parse_mode="Markdown")
    logger.info("Dauer-Aufgabe erteilt (%dh, Kategorie %s)", stunden, kategorie)


def _lokal(dt: datetime) -> str:
    from zoneinfo import ZoneInfo
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Scheduler-Integration (dauer_job, 15-Min-Intervall)
# ---------------------------------------------------------------------------

async def pruefe_laufende(bot) -> None:
    """Enden nachfragen + zufällige Zwischen-Checks (vom dauer_job gerufen)."""
    import random
    from zoneinfo import ZoneInfo
    from bot.services import zeiten

    offene = [task for task in await qdrant.get_tasks_by_status(["offen"], limit=50)
              if task.get("quelle") == "dauer" and task.get("dauer_bis")]
    if not offene:
        return

    jetzt = datetime.now(timezone.utc)
    jetzt_iso = jetzt.isoformat()

    for task in offene:
        point_id = task.get("qdrant_point_id")
        if not point_id:
            continue

        # --- Ende erreicht → in den normalen Followup-Flow übergeben ---
        if task.get("dauer_bis", "") <= jetzt_iso:
            # Reihenfolge wie followup_job: prüfen → generieren → re-checken →
            # senden → erst DANACH Status/State. Vorher stand set_followup_task
            # (geschützter Mode!) + status=gefragt VOR dem Send – ein Sendefehler
            # sperrte den Sklaven in ein Geister-Followup und der Task wurde nie
            # nachgefragt (Trace 06.07., Lücke 4).
            sklave_chat = paare.sub_chat_id()
            if state.get_mode(sklave_chat) not in ("chat", None):
                continue  # aktiver Flow – nächster Lauf (15 min) holt es nach
            frage = t("DAUER_ENDE_FALLBACK", aufgabe=task.get("aufgabe", ""))
            try:
                system = (
                    "Du sprichst direkt mit ihm. Seine Dauer-Anweisung ist gerade "
                    "abgelaufen – die Zeit ist um. Frag ihn in ein bis zwei Sätzen, "
                    "ob er die ganze Zeit durchgehalten hat (ja/nein erwartet) – "
                    "neugierig-lauernd, als würdest du beides genießen. Kein Markdown.\n\n"
                    + persona.fuer_sklaven_prompt()
                )
                llm = grok.clean_text(await grok.simple(
                    f"Die Dauer-Anweisung war: {task.get('aufgabe', '')}", system=system, max_tokens=150))
                if llm:
                    frage = llm
            except Exception:
                logger.exception("Dauer-Ende-Frage fehlgeschlagen – Fallback")
            # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann ein Flow
            # begonnen oder ein Safeword gesendet worden sein.
            if state.is_paused() or state.get_mode(sklave_chat) not in ("chat", None):
                continue
            await telegram_helper.send_sklave(bot, frage, voice_text=frage)
            await qdrant.update_task(point_id, {"status": "gefragt"})
            state.set_followup_task(sklave_chat, point_id)
            logger.info("Dauer-Aufgabe abgelaufen → Followup gestellt (point_id=%s)", point_id)
            continue

        # --- Zwischen-Check (zufällig, gedrosselt, nur im erlaubten Fenster) ---
        erteilt = task.get("erteilt_am", "")
        start_puffer = (jetzt - timedelta(minutes=30)).isoformat()
        ende_puffer = (jetzt + timedelta(minutes=15)).isoformat()
        if not erteilt or erteilt > start_puffer:      # erste 30 Min: Ruhe
            continue
        if task.get("dauer_bis", "") <= ende_puffer:   # letzte 15 Min: Ende-Frage abwarten
            continue
        letzter = task.get("dauer_letzter_check") or erteilt
        if letzter > (jetzt - timedelta(minutes=config.DAUER_CHECK_ABSTAND_MIN)).isoformat():
            continue
        jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
        domina_profile = await qdrant.get_user_profile("domina") or {}
        if not zeiten.ist_im_fenster(jetzt_lokal, [config.BLITZ_FENSTER]):
            continue
        if not zeiten.ist_im_fenster(jetzt_lokal, domina_profile.get("kinderfreie_zeiten", []) or []):
            continue
        if random.random() >= config.DAUER_CHECK_CHANCE:
            continue

        check = t("DAUER_CHECK_FALLBACK")
        try:
            system = (
                "Du sprichst direkt mit ihm. Er hat gerade eine laufende Dauer-Anweisung "
                "von dir. Kontrolliere unangekündigt in EINEM kurzen Satz, ob er noch "
                "dabei ist – beiläufig, mit spürbarer Präsenz ('ich sehe dich'-Gefühl). "
                "Keine Frage nach Erledigung, nur Präsenz zeigen. Kein Markdown.\n\n"
                + persona.fuer_sklaven_prompt()
            )
            llm = grok.clean_text(await grok.simple(
                f"Seine laufende Anweisung: {task.get('aufgabe', '')}", system=system, max_tokens=100))
            if llm:
                check = llm
        except Exception:
            logger.exception("Dauer-Zwischen-Check fehlgeschlagen – Fallback")
        # Erst Timestamp patchen (kein Doppel-Check beim nächsten Lauf), dann senden.
        await qdrant.update_task(point_id, {"dauer_letzter_check": jetzt_iso})
        await telegram_helper.send_sklave(bot, check, voice_text=check)
        logger.info("Dauer-Zwischen-Check gesendet (point_id=%s)", point_id)
