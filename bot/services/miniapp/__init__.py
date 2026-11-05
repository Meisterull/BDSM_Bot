"""
Fassade: `bot.services.miniapp` bleibt der Zugriffspfad.

Der frühere Monolith (bot/services/miniapp.py) liegt jetzt in bot/services/miniapp/ (05.11.2026):
  kern               – initData-Prüfung, Event-Loop-Brücke, Konstanten.
  uebersicht         – Cockpit-Daten (Übersicht, Rang, Sparziel, Wette).
  werkstatt          – Werkstatt: Aufgabe, Wette, Inspiration, Serie, Kette.
  training           – Training und Psycho-Übungen.
  aktionen           – Anruf, Profil-Listen, Abwesenheit, Wunsch.
  server             – HTTP-Handler, TLS-Server, Start.

Jeder Name wird bei JEDEM Zugriff im definierenden Teilmodul nachgeschlagen
(PEP 562, __getattr__). Ein Patch auf das Teilmodul wirkt damit für alle Aufrufer.
NIE auf die Fassade patchen (x.name = …) – das überdeckt nur hier. Zum Patchen:
setattr(modul_von("name"), "name", fake).
"""
import importlib

# Geteilte Importe des früheren Monolithen – Alt-Code/Tests erreichen z. B.
# telegram_helper/qdrant über diese Fassade (dieselben Modulobjekte).
import asyncio  # noqa: F401
import hashlib  # noqa: F401
import hmac  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import ssl  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: F401
from urllib.parse import parse_qsl, urlparse  # noqa: F401
from bot import config  # noqa: F401

_MODUL_VON = {
    "_BOT": "kern",
    "_Handler": "server",
    "_INDEX_PFAD": "kern",
    "_LOOP": "kern",
    "_MAX_BODY": "kern",
    "_PROFIL_LISTEN": "uebersicht",
    "_TLSServer": "server",
    "_abwesenheit_setzen": "aktionen",
    "_anruf": "aktionen",
    "_await": "kern",
    "_herrin_wette_payload": "uebersicht",
    "_profil_liste_setzen": "aktionen",
    "_psycho_antwort": "training",
    "_psycho_uebung": "training",
    "_rang_payload": "uebersicht",
    "_sende_an_sklaven": "werkstatt",
    "_sparziel_payload": "uebersicht",
    "_training_erstellen": "training",
    "_trainings_liste": "training",
    "_uebersicht_daten": "uebersicht",
    "_vorschau_ogg": "werkstatt",
    "_werkstatt_aufgabe": "werkstatt",
    "_werkstatt_inspiration": "werkstatt",
    "_werkstatt_kette": "werkstatt",
    "_werkstatt_loeschen": "werkstatt",
    "_werkstatt_serie": "werkstatt",
    "_werkstatt_wette": "werkstatt",
    "_wunsch_einreichen": "aktionen",
    "pruefe_init_data": "kern",
    "start": "server",
}


def __getattr__(name: str):
    mod = _MODUL_VON.get(name)
    if mod is None:
        raise AttributeError(f"module 'bot.services.miniapp' has no attribute '{name}'")
    return getattr(importlib.import_module(f"bot.services.miniapp.{mod}"), name)


def __dir__():
    return sorted(_MODUL_VON)


def modul_von(name: str):
    """Das Teilmodul, das `name` definiert – zum Patchen in Tests."""
    return importlib.import_module(f"bot.services.miniapp.{_MODUL_VON[name]}")
