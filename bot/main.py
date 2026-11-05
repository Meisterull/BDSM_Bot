"""
Fassade: `bot.main` bleibt der Zugriffspfad.

Der frühere Monolith (bot/main.py) liegt jetzt in bot/app/ (05.11.2026):
  logging_setup      – Logging-Setup (0600-Logdatei, Formate).
  gates              – Rollen-Auflösung, Paar-Kontext, Eingangs-Log, Callback-Gate, Pause-Guard, Fehler-Handler.
  zustand            – State-Restore beim Start (pro Paar).
  routing            – Nachrichten-Routing: Text, Voice, Medien, falsche Rolle, Ausgangs-Logging.
  lebenszyklus       – post_init / post_shutdown / main().
  registrierung      – Handler-Registrierung.
  zeitjobs           – Scheduler, Paar-Update-Prozessor, Zeit-Jobs pro Paar.

Jeder Name wird bei JEDEM Zugriff im definierenden Teilmodul nachgeschlagen
(PEP 562, __getattr__). Ein Patch auf das Teilmodul wirkt damit für alle Aufrufer.
NIE auf die Fassade patchen (x.name = …) – das überdeckt nur hier. Zum Patchen:
setattr(modul_von("name"), "name", fake).
"""
import importlib

# Geteilte Importe des früheren Monolithen – Alt-Code/Tests erreichen z. B.
# telegram_helper/qdrant über diese Fassade (dieselben Modulobjekte).
import logging  # noqa: F401
import os  # noqa: F401
from datetime import datetime, timedelta  # noqa: F401
from zoneinfo import ZoneInfo  # noqa: F401
from logging.handlers import RotatingFileHandler  # noqa: F401
from telegram import Update  # noqa: F401
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes, Application, TypeHandler,
    ApplicationHandlerStop,
)  # noqa: F401
from telegram.error import NetworkError, TimedOut, RetryAfter, BadRequest  # noqa: F401
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: F401
from bot import commands_katalog as ck  # noqa: F401
from bot import config, state  # noqa: F401
from bot.services import paare, qdrant, telegram_helper  # noqa: F401
from bot.handlers import (
    safeword, domina, sklave, followup_response,
    reaktion, gefuehl, profil, aufgaben, vorlagen,
    stimmung, inspiration, ziele, rueckblick, training,
    stats, bewertung, rollenspiel, wochenplanung,
    wunsch, kommentar, geheimnis, strafen_protokoll, tinytask,
    wuerfel, wunschkategorien, privileg, wette, blitz, arc, event_arc, roulette, dauer, quiz, coach_quiz, advent, tiny_task_feedback, hilfe, resurface, stille_checkin,
    lerntagebuch, coach_regeln, skill, kette_adaptiv, dossier, namen, meine_aufgaben,
    einstellungen, luecke, pairing, admin, abwesenheit, inventar, herrin_versaeumnis,
    waehrung as waehrung_h,
)  # noqa: F401
from bot.handlers import serie_handler  # noqa: F401
from bot.scheduler.aufgaben import followup_job  # noqa: F401
from bot.scheduler.basis import nachhol_tick_job  # noqa: F401
from bot.scheduler.impulse import (
    blitz_check_job, blitz_ablauf_job, event_check_job, dauer_job, kalender_job,
    spiel_impuls_job, coach_impuls_job,
)  # noqa: F401
from bot.scheduler.luecken import luecken_check_job, luecken_zustellung_job, stille_checkin_job  # noqa: F401
from bot.scheduler.reflexion import lerntagebuch_job, coach_reflexion_job, profil_pflege_job  # noqa: F401
from bot.scheduler.tagesjobs import (
    stimmung_job, ziel_erinnerung_job, training_job, lernkurve_job, rollenspiel_vorschlag_job,
    wochenplanung_job, geheimnis_job, kommentar_analyse_job, resurface_job,
)  # noqa: F401
from bot.scheduler.tiny_task import tiny_task_feedback_job, tiny_task_vorschlag_job  # noqa: F401
from bot.scheduler.wartung import (
    training_erinnerung_job, backup_job, sklave_dossier_job, offene_faeden_job,
)  # noqa: F401
from bot.handlers.profil import abbrechen_command  # noqa: F401
from bot.messages import t  # noqa: F401

# Eager: Logging-Setup läuft beim Import von bot.main (wie bisher), der Rest wird
# über __getattr__ nachgeladen. main() ist der Einstieg (Dockerfile: python -m bot.main).
from bot.app import logging_setup  # noqa: F401
from bot.app.lebenszyklus import main  # noqa: F401

_MODUL_VON = {
    "TRANSIENTE_NETZFEHLER": "gates",
    "_CALLBACK_ROLLEN": "gates",
    "_Private0600FileHandler": "logging_setup",
    "_callback_gate": "gates",
    "_fuer_paar": "zeitjobs",
    "_paar_update_prozessor": "zeitjobs",
    "_pro_paar": "gates",
    "_restore_paar_state": "zustand",
    "_rolle": "gates",
    "_setup_logging": "logging_setup",
    "_wrap_outgoing_logging": "routing",
    "app_cmd": "routing",
    "entferne_zeit_jobs": "zeitjobs",
    "error_handler": "gates",
    "falsche_rolle_hinweis": "routing",
    "handle_media": "routing",
    "handle_message": "routing",
    "handle_voice": "routing",
    "log_incoming": "gates",
    "main": "lebenszyklus",
    "paar_kontext_setzen": "gates",
    "pause_guard": "gates",
    "plane_zeit_jobs": "zeitjobs",
    "post_init": "lebenszyklus",
    "post_shutdown": "lebenszyklus",
    "register_handlers": "registrierung",
    "restore_state": "zustand",
    "scheduler": "zeitjobs",
}


def __getattr__(name: str):
    mod = _MODUL_VON.get(name)
    if mod is None:
        raise AttributeError(f"module 'bot.main' has no attribute '{name}'")
    return getattr(importlib.import_module(f"bot.app.{mod}"), name)


def __dir__():
    return sorted(_MODUL_VON)


def modul_von(name: str):
    """Das Teilmodul, das `name` definiert – zum Patchen in Tests."""
    return importlib.import_module(f"bot.app.{_MODUL_VON[name]}")

if __name__ == "__main__":
    main()