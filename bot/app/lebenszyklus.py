"""
post_init / post_shutdown / main().

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ApplicationBuilder, Application
from bot import config, state
from bot.services import paare, qdrant
from bot.handlers import (
    dauer,
    hilfe,
    resurface,
    stille_checkin,
    lerntagebuch,
    einstellungen,
    inventar,
    waehrung as waehrung_h,
)
from bot.scheduler.basis import nachhol_tick_job
from bot.scheduler.impulse import (
    blitz_check_job,
    blitz_ablauf_job,
    event_check_job,
    dauer_job,
    kalender_job,
    spiel_impuls_job,
    coach_impuls_job,
)
from bot.scheduler.luecken import luecken_zustellung_job, stille_checkin_job
from bot.scheduler.reflexion import lerntagebuch_job, coach_reflexion_job, profil_pflege_job
from bot.scheduler.tagesjobs import (
    lernkurve_job,
    geheimnis_job,
    kommentar_analyse_job,
    resurface_job,
)
from bot.scheduler.wartung import backup_job, sklave_dossier_job, offene_faeden_job
from bot.app import gates, registrierung, routing, zeitjobs, zustand

logger = logging.getLogger(__name__)



async def post_init(application: Application) -> None:
    from telegram import BotCommand, BotCommandScopeChat
    from bot import commands_katalog
    from bot.services import logserver

    routing._wrap_outgoing_logging(application)
    logserver.start()
    # Mini-App-Server (no-op wenn MINIAPP_PORT=0). Braucht den laufenden Loop
    # für run_coroutine_threadsafe aus dem Server-Thread. import hier nötig:
    # weiter unten in DIESER Funktion gibt es ein lokales `import asyncio`,
    # damit ist der Name funktionsweit lokal (UnboundLocalError sonst).
    import asyncio
    from bot.services import miniapp
    miniapp.start(asyncio.get_running_loop(), application.bot)
    waehrung_h.start(application.bot)   # Währung ⭐: Bot-Handle + Neuer-Wunsch-Hook

    _max_versuche = 3
    _pause = 5
    for versuch in range(1, _max_versuche + 1):
        try:
            qdrant.ensure_collections()
            break
        except Exception as e:
            logger.error(
                "Qdrant ensure_collections fehlgeschlagen (Versuch %d/%d): %s",
                versuch, _max_versuche, e,
            )
            if versuch == _max_versuche:
                logger.critical(
                    "Qdrant nicht erreichbar nach %d Versuchen – Bot-Start wird abgebrochen.",
                    _max_versuche,
                )
                raise SystemExit(1) from e
            import asyncio
            await asyncio.sleep(_pause)

    try:
        from bot.services import persona_config
        await persona_config.load()    # Persona-Felder ALLER Paare in die Caches
        from bot.services import inventar as inventar_service
        await inventar_service.load()  # Inventar-Listen ALLER Paare (gleiches Cache-Muster)
        state.load_persisted()   # message_history + Pause-Flag aus STATE_FILE
        await zustand.restore_state()          # Modi/Tasks aus Qdrant
    except Exception as e:
        logger.error("State-Wiederherstellung Fehler: %s", e)

    # Rollenspezifische Menüs pro Paar in DESSEN UI-Locale (gemeinsame
    # Datenquelle mit /hilfe: bot/commands_katalog.py). Bewusst NACH
    # persona_config.load() – vorher wären die Locale-Caches noch leer.
    try:
        for paar in paare.alle_paare():
            with paare.kontext(paar.paar_id):
                domina_commands = [BotCommand(c, b) for c, b in commands_katalog.domina_menue()]
                sklave_commands = [BotCommand(c, b) for c, b in commands_katalog.sklave_menue()]
            await application.bot.set_my_commands(
                domina_commands,
                scope=BotCommandScopeChat(chat_id=int(paar.dom_chat_id)),
            )
            await application.bot.set_my_commands(
                sklave_commands,
                scope=BotCommandScopeChat(chat_id=int(paar.sub_chat_id)),
            )
        # Fallback (für unbekannte Chats): /hilfe, bei aktivem Pairing auch /start
        await application.bot.set_my_commands([
            BotCommand(c, b) for c, b in commands_katalog.fallback_menue()
        ])
    except Exception as e:
        logger.error("Fehler beim Setzen der Bot-Commands: %s", e)

    # Nutzer-konfigurierbare Tages-Jobs: pro Paar als eigener Cron zu DESSEN
    # Zeiten (persona_config, /einstellungen Feld 7; leer = Env-Default).
    for paar in paare.alle_paare():
        try:
            zeitjobs.plane_zeit_jobs(application.bot, paar)
        except Exception:
            logger.exception("Zeit-Jobs für Paar %s nicht planbar", paar.paar_id)

    # 2-Wochen-Jobs: cron (wöchentlich) + ISO-Wochen-Parität IM Job (_zweiwochen_takt).
    # IntervalTrigger verlor bei jedem Deploy seine Phase (Anker = nächster Wochentag
    # → bei häufigen Deploys faktisch wöchentlich) und drifte nach DST-Umstellung um 1h.
    zeitjobs.scheduler.add_job(gates._pro_paar(lernkurve_job), "cron", day_of_week="mon", hour=20, minute=30,
                      args=[application.bot], id="lernkurve", replace_existing=True)

    zeitjobs.scheduler.add_job(gates._pro_paar(geheimnis_job), "interval", minutes=30,
                      args=[application.bot], id="geheimnis_check", replace_existing=True)

    zeitjobs.scheduler.add_job(gates._pro_paar(kommentar_analyse_job), "cron", day_of_week="sat", hour=19, minute=30,
                      args=[application.bot], id="kommentar_analyse", replace_existing=True)

    zeitjobs.scheduler.add_job(gates._pro_paar(resurface_job), "cron", day_of_week="thu", hour=20, minute=0,
                      args=[application.bot], id="resurface", replace_existing=True)

    # Lerntagebuch: jeden Sonntag 23:00 Wochen-Verdichtung der Domina-Coach-Gespräche
    zeitjobs.scheduler.add_job(gates._pro_paar(lerntagebuch_job), "cron", day_of_week="sun", hour=23, minute=0,
                      args=[application.bot], id="lerntagebuch", replace_existing=True)

    # Coach-Reflexion: alle 2 Wochen Sonntag 22:00 – schlägt neue Coach-Regeln vor
    # (Parität siehe lernkurve-Kommentar oben)
    zeitjobs.scheduler.add_job(gates._pro_paar(coach_reflexion_job), "cron", day_of_week="sun", hour=22, minute=0,
                      args=[application.bot], id="coach_reflexion", replace_existing=True)

    # Auto-Profil-Pflege: alle 2 Wochen Sonntag 21:00 – schlägt Profil-Updates vor
    # (Parität siehe lernkurve-Kommentar oben)
    zeitjobs.scheduler.add_job(gates._pro_paar(profil_pflege_job), "cron", day_of_week="sun", hour=21, minute=0,
                      args=[application.bot], id="profil_pflege", replace_existing=True)

    # Tägliches Qdrant-Backup 03:00 (JSON-Export + Snapshots)
    zeitjobs.scheduler.add_job(backup_job, "cron", hour=3, minute=0,
                      args=[application.bot], id="qdrant_backup", replace_existing=True)

    # Sklaven-Dossier wöchentlich (So 23:30) – verdichtet das Wissen über ihn
    zeitjobs.scheduler.add_job(gates._pro_paar(sklave_dossier_job), "cron", day_of_week="sun", hour=23, minute=30,
                      args=[application.bot], id="sklave_dossier", replace_existing=True)

    # Offene Fäden täglich (16:00) – woran die Herrin von sich aus anknüpfen kann
    zeitjobs.scheduler.add_job(gates._pro_paar(offene_faeden_job), "cron", hour=16, minute=0,
                      args=[application.bot], id="offene_faeden", replace_existing=True)

    # Lücken-Füller: alle 15 Min Zustellung freigegebener 'heute Abend'-Aufgaben
    # (der tägliche Check läuft pro Paar in plane_zeit_jobs).
    # Währung ⭐: Herrin-Wette – Frist um → Urteils-Frage, 3 Tage ohne Meldung → verloren
    zeitjobs.scheduler.add_job(gates._pro_paar(waehrung_h.wette_urteil_job), "interval", minutes=15,
                      args=[application.bot], id="wette_urteil", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(luecken_zustellung_job), "interval", minutes=15,
                      args=[application.bot], id="luecken_zustellung", replace_existing=True)
    # Stille-Check-in 🔕: täglich – fragt die Domina nach STILLE_CHECKIN_TAGE
    # Tagen ohne jede Eingabe selbst nach dem Grund (Env-Zeit, nicht pro Paar).
    _sc_h, _sc_m = config.hm(config.STILLE_CHECKIN_TIME)
    zeitjobs.scheduler.add_job(gates._pro_paar(stille_checkin_job), "cron", hour=_sc_h, minute=_sc_m,
                      args=[application.bot], id="stille_checkin", replace_existing=True)
    # Nachhol-Tick für blockierte Einmal-Jobs – bewusst :05/:35, nie in derselben
    # Minute wie die regulären Cron-Jobs (:00/:30).
    zeitjobs.scheduler.add_job(gates._pro_paar(nachhol_tick_job), "cron", minute="5,35",
                      args=[application.bot], id="nachhol_tick", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(blitz_check_job), "interval", minutes=30,
                      args=[application.bot], id="blitz_check", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(blitz_ablauf_job), "interval", minutes=5,
                      args=[application.bot], id="blitz_ablauf", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(spiel_impuls_job), "interval", minutes=30,
                      args=[application.bot], id="spiel_impuls", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(coach_impuls_job), "interval", minutes=30,
                      args=[application.bot], id="coach_impuls", replace_existing=True)
    # Ein Lauf kurz nach dem Start: ein vorgemerkter Wunsch-Wettvorschlag soll nach
    # einem Deploy nicht bis zum ersten Intervall-Tick (30 Min) warten. Würfel,
    # Abstand und Fenster gelten wie immer.
    zeitjobs.scheduler.add_job(gates._pro_paar(coach_impuls_job), "date",
                      run_date=datetime.now(ZoneInfo(config.TIMEZONE)) + timedelta(seconds=90),
                      args=[application.bot], id="coach_impuls_start", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(event_check_job), "cron", hour=8, minute=30,
                      args=[application.bot], id="event_check", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(dauer_job), "interval", minutes=15,
                      args=[application.bot], id="dauer", replace_existing=True)
    zeitjobs.scheduler.add_job(gates._pro_paar(kalender_job), "cron", hour=8, minute=0,
                      args=[application.bot], id="kalender", replace_existing=True)

    zeitjobs.scheduler.start()
    logger.info("Scheduler gestartet: %d Jobs (%d Paare mit eigenen Zeit-Jobs)",
                len(zeitjobs.scheduler.get_jobs()), len(paare.alle_paare()))


async def post_shutdown(application: Application) -> None:
    """Letzten State-Stand synchron rausschreiben (Review D8/M14): der
    Persist-Debounce (2 s call_later) feuert nach dem Stop nie mehr – ohne
    Flush gingen die letzten Sekunden message_history bei jedem Deploy verloren."""
    state._persist(immediate=True)


def main() -> None:
    config.validate()  # Fail-fast: Pflicht-Env-Vars prüfen, bevor irgendwas startet
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(zeitjobs._paar_update_prozessor())
        .build()
    )

    registrierung.register_handlers(app)

    logger.info("Bot gestartet.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
