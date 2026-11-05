"""
Scheduler, Paar-Update-Prozessor, Zeit-Jobs pro Paar.

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
from telegram import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot import config, state
from bot.services import paare
from bot.handlers import wochenplanung, tiny_task_feedback, einstellungen, admin
from bot.scheduler.aufgaben import followup_job
from bot.scheduler.luecken import luecken_check_job
from bot.scheduler.tagesjobs import (
    stimmung_job,
    ziel_erinnerung_job,
    training_job,
    rollenspiel_vorschlag_job,
    wochenplanung_job,
)
from bot.scheduler.tiny_task import tiny_task_feedback_job, tiny_task_vorschlag_job
from bot.scheduler.vanilla import vanilla_impuls_job
from bot.scheduler.wartung import training_erinnerung_job

logger = logging.getLogger(__name__)



scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)


def _paar_update_prozessor():
    """Update-Processor für concurrent_updates (Multiuser Schritt 7): Updates
    verschiedener Paare laufen parallel (ein langsamer Grok-Call eines Paares
    blockiert die anderen nicht mehr), Updates DESSELBEN Paares strikt seriell
    – das schützt die Mode-Maschine (state.get_mode → Routing → Mutation) und
    die pro-Update-Task-lokale Paar-Kontext-ContextVar bleibt sauber.

    Als Factory statt Modul-Klasse: die lokalen Tests stubben telegram.ext mit
    MagicMock, von dem sich nicht erben lässt – Import bleibt so stub-sicher."""
    from telegram.ext import SimpleUpdateProcessor

    class PaarSerialisierterProzessor(SimpleUpdateProcessor):
        async def do_process_update(self, update: object, coroutine) -> None:
            chat = getattr(update, "effective_chat", None)
            ctx = paare.resolve(chat.id) if chat is not None else None
            if ctx is None:
                # Unautorisierte/chatlose Updates: keine Mode-Maschine betroffen
                await super().do_process_update(update, coroutine)
                return
            async with paare.lock(ctx[0].paar_id):
                await super().do_process_update(update, coroutine)

    return PaarSerialisierterProzessor(max(1, config.CONCURRENT_UPDATES))


def _fuer_paar(job, paar_id: str):
    """Wie _pro_paar, aber für genau EIN Paar – für die nutzer-konfigurierbaren
    Zeit-Jobs, die pro Paar zu DESSEN Zeit als eigener Cron laufen. Läuft leer,
    wenn das Paar nicht mehr registriert ist (bis zur nächsten Neu-Planung)."""
    import functools

    @functools.wraps(job)
    async def wrapper(bot):
        if paare.get_paar(paar_id) is None:
            return
        with paare.kontext(paar_id):
            try:
                await job(bot)
            except Exception:
                logger.exception("Zeit-Job %s für Paar %s fehlgeschlagen",
                                 getattr(job, "__name__", job), paar_id)
    return wrapper


def plane_zeit_jobs(bot, paar: "paare.Paar") -> None:
    """(Re-)Plant die Tages-Jobs eines Paares zu dessen Zeiten (persona_config,
    leer = globale Env-Defaults). Aufgerufen beim Start für alle Paare, nach
    erfolgreichem Pairing und nach Zeit-Änderung in /einstellungen –
    replace_existing macht den Aufruf idempotent."""
    from bot.services import persona_config, modi
    pid = paar.paar_id
    with paare.kontext(pid):
        z = {feld: persona_config.zeit(feld) for feld in persona_config.ZEIT_FELDER}
        vanilla = modi.ist_vanilla()

    hour, minute = config.hm(z["followup_time"])
    # Training Zeit = Follow-up + TRAINING_OFFSET_MINUTEN
    training_total = hour * 60 + minute + config.TRAINING_OFFSET_MINUTEN

    def _job(name, fn, zeit_feld, **cron):
        h, m = config.hm(z[zeit_feld])
        scheduler.add_job(_fuer_paar(fn, pid), "cron", hour=h, minute=m, args=[bot],
                          id=f"{name}_p{pid}", replace_existing=True, **cron)

    # Vanilla (Etappe 2): nur Stimmungsfrage + Impuls an beide; die übrigen
    # Crons bleiben registriert (ein späterer Moduswechsel braucht sie wieder),
    # der _job_guard überspringt sie im Vanilla-Modus. Der Impuls-Job ist für
    # alle Paare registriert und prüft selbst den Modus.
    _job("vanilla_impuls", vanilla_impuls_job, "tiny_task_time")
    scheduler.add_job(_fuer_paar(followup_job, pid), "cron", hour=hour, minute=minute,
                      args=[bot], id=f"daily_followup_p{pid}", replace_existing=True)
    if config.TRAINING_ENABLED:
        scheduler.add_job(_fuer_paar(training_job, pid), "cron", day_of_week="tue,thu",
                          hour=(training_total // 60) % 24, minute=training_total % 60,
                          args=[bot], id=f"daily_training_p{pid}", replace_existing=True)
    # Eigenes Gate (D9/N12): prüft Tasks, nicht Training – siehe config.py.
    if config.TRAINING_ERINNERUNG_ENABLED:
        _job("training_erinnerung", training_erinnerung_job, "training_erinnerung_time")
    if config.STIMMUNG_ENABLED:
        _job("stimmung_check", stimmung_job, "stimmung_time")
    _job("tiny_task_vorschlag", tiny_task_vorschlag_job, "tiny_task_time")
    _job("ziel_erinnerung", ziel_erinnerung_job, "ziel_erinnerung_time", day_of_week="mon")
    _job("rollenspiel_vorschlag", rollenspiel_vorschlag_job, "rollenspiel_vorschlag_time", day_of_week="fri,sat")
    _job("wochenplanung", wochenplanung_job, "wochenplanung_time", day_of_week="sun")
    _job("tiny_task_feedback", tiny_task_feedback_job, "tiny_task_feedback_time")
    _job("luecken_check", luecken_check_job, "luecken_check_time")
    logger.info("Zeit-Jobs für Paar %s geplant (Follow-up %s%s)", pid, z["followup_time"],
                ", Vanilla: nur Stimmung + Impuls aktiv" if vanilla else "")


def entferne_zeit_jobs(paar_id: str) -> None:
    """Entfernt die pro-Paar-Crons eines Paares (Paar-Löschung, handlers/admin.py)."""
    suffix = f"_p{paar_id}"
    for job in scheduler.get_jobs():
        if job.id.endswith(suffix):
            job.remove()
