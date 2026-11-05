"""
Fassade: `bot.handlers.waehrung` bleibt der Zugriffspfad.

Der frühere Monolith (bot/handlers/waehrung.py) liegt jetzt in bot/handlers/waehrung/ (05.11.2026):
  kern               – Punkte-Nachbereitung: Rang-/Schwellen-Meldungen, Bot-Handle, Profil-Felder.
  abzuege            – Punkteabzug bei nicht erledigten Aufgaben.
  sparziel           – Wunschpreise und Sparziel.
  wette_annahme      – Herrin-Wette: Lage, Verlauf, Angebot, Annahme.
  wette_ablehnung    – Herrin-Wette: Ablehnung, Strafwahl, eigene Strafe, Auto-Abschluss.
  wette_urteil       – Herrin-Wette: Urteil, Einspruch, Abschluss.
  session            – Session-Privileg.

Jeder Name wird bei JEDEM Zugriff im definierenden Teilmodul nachgeschlagen
(PEP 562, __getattr__). Ein Patch auf das Teilmodul wirkt damit für alle Aufrufer.
NIE auf die Fassade patchen (x.name = …) – das überdeckt nur hier. Zum Patchen:
setattr(modul_von("name"), "name", fake).
"""
import importlib

# Geteilte Importe des früheren Monolithen – Alt-Code/Tests erreichen z. B.
# telegram_helper/qdrant über diese Fassade (dieselben Modulobjekte).
import asyncio  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
import uuid  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401
from zoneinfo import ZoneInfo  # noqa: F401
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: F401
from telegram.ext import ContextTypes  # noqa: F401
from bot import config, state  # noqa: F401
from bot.services import grok, inventar, limits_check, paare, qdrant, telegram_helper, tts, waehrung  # noqa: F401
from bot.services import sticker_reaktionen  # noqa: F401
from bot.prompts import rollen  # noqa: F401
from bot.messages import t  # noqa: F401

_MODUL_VON = {
    "FELD_FRAGE_AM": "kern",
    "FELD_RUHE": "kern",
    "FELD_SCHWELLE": "kern",
    "FELD_WETTE": "kern",
    "FELD_WETT_VERLAUF": "kern",
    "MODE_STRAFE_EIGEN": "wette_annahme",
    "WETT_VERLAUF_MAX": "kern",
    "_BOT": "kern",
    "_HINTERGRUND": "wette_annahme",
    "_OFFENE_STATUS": "wette_annahme",
    "_OFFEN_MAX": "kern",
    "_ablehnung_automatisch_abschliessen": "wette_ablehnung",
    "_ablehnungs_strafen_generieren": "wette_ablehnung",
    "_ablehnungs_strafen_roh": "wette_ablehnung",
    "_abmachung": "wette_annahme",
    "_annahme_buttons": "wette_annahme",
    "_datum_lokal": "wette_annahme",
    "_einsatz_text": "wette_annahme",
    "_idee_bereinigt": "wette_annahme",
    "_jetzt": "kern",
    "_nur_machbare": "wette_ablehnung",
    "_parse": "kern",
    "_preis_buttons": "sparziel",
    "_rang_melden": "kern",
    "_schwelle_melden": "kern",
    "_strafe_anordnen": "wette_ablehnung",
    "_strafe_buttons": "wette_ablehnung",
    "_strafe_wahl_text": "wette_ablehnung",
    "_strafen_zeigen": "wette_ablehnung",
    "_sub_gross": "wette_annahme",
    "_tagsueber": "wette_annahme",
    "_urteil_buttons": "wette_urteil",
    "_verlauf_eintragen": "wette_annahme",
    "_wette_abschliessen": "wette_urteil",
    "_wette_annehmen": "wette_annahme",
    "_ziel_buttons": "sparziel",
    "abzug_anbieten": "abzuege",
    "abzug_buttons": "abzuege",
    "abzug_nicht_erledigt": "abzuege",
    "callback_punkteabzug": "abzuege",
    "callback_wetteablehnung": "wette_ablehnung",
    "callback_wetteantwort": "wette_annahme",
    "callback_wetteeinspruch": "wette_urteil",
    "callback_wetteurteil": "wette_urteil",
    "callback_wunschpreis": "sparziel",
    "callback_wunschziel": "sparziel",
    "frage_wunschpreis": "sparziel",
    "handle_strafe_eigen": "wette_ablehnung",
    "im_hintergrund": "wette_annahme",
    "lage_text": "wette_annahme",
    "letzte_wetten": "wette_annahme",
    "nach_punkteaenderung": "kern",
    "rang_zeile": "kern",
    "session_wunsch_task": "session",
    "sparziel_pruefen": "sparziel",
    "sparziel_zeile": "kern",
    "start": "kern",
    "wette_anbieten": "wette_annahme",
    "wette_laeuft": "wette_annahme",
    "wette_offen": "wette_annahme",
    "wette_urteil_job": "wette_urteil",
}


def __getattr__(name: str):
    mod = _MODUL_VON.get(name)
    if mod is None:
        raise AttributeError(f"module 'bot.handlers.waehrung' has no attribute '{name}'")
    return getattr(importlib.import_module(f"bot.handlers.waehrung.{mod}"), name)


def __dir__():
    return sorted(_MODUL_VON)


def modul_von(name: str):
    """Das Teilmodul, das `name` definiert – zum Patchen in Tests."""
    return importlib.import_module(f"bot.handlers.waehrung.{_MODUL_VON[name]}")
