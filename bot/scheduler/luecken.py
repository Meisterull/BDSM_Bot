"""
Lücken-Erkennung und Stille-Check-in (Dom-Seite inaktiv).

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
from datetime import datetime, timezone, timedelta
from telegram import Bot
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.prompts import followup as fp
from bot.scheduler import basis

logger = logging.getLogger(__name__)


async def _letzte_domina_aktivitaet() -> datetime | None:
    """Jüngster Zeitpunkt aus: neueste erteilte Aufgabe + neueste Domina-Conversation.
    Basis der Lücken-Erkennung (kein Treffer = nie etwas → None)."""
    stempel = []
    # Inkl. Zwischenzustände gefragt/gefuehl_pending (Review D8/M9): ein gestern
    # erteilter Task, der gerade auf die Sklaven-Antwort wartet, ist Aktivität –
    # ohne ihn kam der Lücken-Vorschlag trotz frischer Aufgabe.
    tasks = await qdrant.get_tasks_by_status(
        ["offen", "gefragt", "gefuehl_pending", "erledigt", "nicht_erledigt",
         "serie_wartend", "kette_wartend", "pausiert", "geplant"],
        limit=5, sort_by_datum=True,
    )
    # Auto-Content zählt NICHT als Domina-Aktivität (D9/N11, Owner-Entscheid
    # 15.08.): Blitz-/Advent-Tasks erteilt der Bot selbst – bei aktivem Blitz
    # oder Adventskalender käme der Lücken-Vorschlag sonst nie, obwohl die
    # Domina längst ausgestiegen ist. limit=5, damit nach dem Filter noch ein
    # echter Kandidat übrig ist.
    tasks = [t_ for t_ in tasks if t_.get("quelle") not in ("blitz", "advent")]
    if tasks:
        stempel.append(basis._parse_datum(tasks[0].get("erteilt_am", "")))
    jetzt = datetime.now(timezone.utc)
    conv = await qdrant.get_conversations_in_range(
        "domina", (jetzt - timedelta(days=30)).isoformat(), jetzt.isoformat(), limit=1,
    )
    if conv:
        stempel.append(basis._parse_datum(conv[-1].get("datum", "")))
    return max(stempel) if stempel else None


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def luecken_check_job(bot: Bot) -> None:
    """Schlägt der Domina nach LUECKEN_INTERVALL_TAGE Tagen ohne Aufgaben-/Szenen-
    Aktivität EINEN Task-Vorschlag vor – nur bei Opt-in (sie gibt jeden frei)."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    if not domina_profile.get("luecken_vorschlag_aktiv", False):
        return
    if basis._flow_aktiv(paare.dom_chat_id(), "Lücken-Check"):
        return

    intervall = timedelta(days=config.LUECKEN_INTERVALL_TAGE)
    jetzt = datetime.now(timezone.utc)

    # Throttle: nach einem Vorschlag erst nach dem Intervall erneut fragen (kein
    # tägliches Drängeln, auch wenn sie den Vorschlag ignoriert).
    # 1h-Toleranz (D9/N9): die Throttle-Marke wird Sekunden NACH der Cron-Zeit
    # geschrieben – ohne Toleranz war "jetzt − Marke" am Tag +INTERVALL immer
    # knapp unter dem Intervall und der Vorschlag kam systematisch einen Tag
    # später (Millisekunden-Kanten-Klasse, wie der Ketten-Sweep 30.07.).
    letzter_vorschlag = basis._parse_datum(domina_profile.get("luecke_letzter_vorschlag_am", ""))
    if jetzt - letzter_vorschlag < intervall - timedelta(hours=1):
        return

    letzte_aktivitaet = await _letzte_domina_aktivitaet()
    if letzte_aktivitaet and jetzt - letzte_aktivitaet < intervall - timedelta(hours=1):
        return

    tage = (jetzt - letzte_aktivitaet).days if letzte_aktivitaet else config.LUECKEN_INTERVALL_TAGE
    from bot.handlers import luecke  # lazy: zirkulären Import vermeiden
    await luecke.sende_vorschlag(bot, max(tage, config.LUECKEN_INTERVALL_TAGE))


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def stille_checkin_job(bot: Bot) -> None:
    """Stille-Check-in 🔕 (täglich STILLE_CHECKIN_TIME): fragt die Domina nach
    STILLE_CHECKIN_TAGE Tagen ohne jede Eingabe selbst, was gerade los ist.
    Phasen-Logik in handlers/stille_checkin (gleiche_phase): pro Stille-Phase
    max. 2 Fragen, die Antwort selbst zählt nicht als neue Aktivität."""
    if config.STILLE_CHECKIN_TAGE <= 0:
        return
    from bot.handlers import stille_checkin  # lazy: zirkulären Import vermeiden
    from bot.services import persona_config
    dom_chat = paare.dom_chat_id()
    if state.coach_ruhe():
        return  # sie hat schon geantwortet (Ruhe/Zuschauer) – nicht nachbohren
    if persona_config.abwesenheit():
        return  # angekündigte Abwesenheit ist keine Stille
    if basis._flow_aktiv(dom_chat, "Stille-Check-in"):
        return

    letzte = await stille_checkin.letzte_domina_eingabe()
    if letzte is None:
        return  # nie eine Eingabe (frisches Paar) – nichts, woran Stille messbar wäre
    jetzt = datetime.now(timezone.utc)
    still = jetzt - letzte
    # 1h-Toleranz (Millisekunden-Kanten-Klasse, s. luecken_check_job).
    if still < timedelta(days=config.STILLE_CHECKIN_TAGE) - timedelta(hours=1):
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    ci = domina_profile.get("stille_checkin") or {}
    anzahl = 0
    tage_seit_frage = 0
    if ci.get("gefragt_am") and stille_checkin.gleiche_phase(letzte, ci):
        if ci.get("antwort_am"):
            return  # beantwortet – in dieser Phase nie wieder fragen
        anzahl = int(ci.get("anzahl", 0) or 0)
        if anzahl >= 2:
            return  # zweimal gefragt, keine Antwort – Ruhe bis sie sich rührt
        seit_frage = jetzt - basis._parse_datum(ci.get("gefragt_am", ""))
        if seit_frage < timedelta(days=config.STILLE_ZWEITE_FRAGE_TAGE) - timedelta(hours=1):
            return
        tage_seit_frage = seit_frage.days

    tage = still.days
    if not await stille_checkin.frage_stellen(bot, tage, zweite=(anzahl == 1),
                                              tage_seit_frage=tage_seit_frage):
        return
    # Neue Phase ersetzt den Eintrag komplett (alte antwort_* fallen weg).
    await qdrant.patch_profile_fields("domina", {"stille_checkin": {
        "phase_anker": letzte.isoformat(),
        "gefragt_am": datetime.now(timezone.utc).isoformat(),
        "anzahl": anzahl + 1,
        "tage_still": tage,
    }})


@basis._job_guard
async def luecken_zustellung_job(bot: Bot) -> None:
    """Stellt geplante Aufgaben (status='geplant') zu, sobald ihr `zustellung_ab`
    erreicht ist: Lücken-'heute Abend' UND Termin-Aufgaben ("Aufgabe für Tag X",
    quelle='termin'). Restart-sicher (Qdrant-persistiert)."""
    geplant = await qdrant.get_tasks_by_status(["geplant"], limit=20)
    if not geplant:
        return
    # Nicht in einen aktiven Sklaven-Flow platzen – nächster Lauf (Intervall) holt's nach.
    if basis._flow_aktiv(paare.sub_chat_id(), "Lücken-Zustellung"):
        return

    jetzt = datetime.now(timezone.utc)
    for task in geplant:
        if basis._parse_datum(task.get("zustellung_ab", "")) > jetzt:
            continue
        point_id = task.get("qdrant_point_id")
        if not point_id:
            continue
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(task.get("aufgabe", "")), max_tokens=250)
        except Exception:
            logger.exception("aufgabe_an_sklaven (Zustellung) fehlgeschlagen – Rohtext")
            anweisung = task.get("aufgabe", "")
        # Re-Check NACH dem LLM-Await (Review D8/M8, TOCTOU wie _process_serie_tasks):
        # ein im LLM-Fenster gesetztes Safeword/ein gestarteter Flow darf die
        # Aufgabe nicht mehr erhalten – nächster 15-Min-Lauf holt sie nach.
        if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
            logger.info("Zustellung nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
            break
        # Erst Status flippen (verhindert Doppel-Zustellung beim nächsten Lauf), dann
        # senden – mit Rollback bei Sendefehler, sonst gilt eine nie zugestellte
        # Aufgabe als offen und das Followup fragt danach (Trace 06.07., Lücke 5).
        # Kein set_followup_task – Followup läuft später über follow_up_datum (wie /wuerfel).
        await qdrant.update_task(point_id, {"status": "offen"})
        try:
            await telegram_helper.send_sklave(bot, anweisung, voice_text=anweisung)
        except Exception:
            await qdrant.update_task(point_id, {"status": "geplant"})
            raise
        logger.info("Geplante Aufgabe zugestellt (quelle=%s, point_id=%s).",
                    task.get("quelle", "?"), point_id)
