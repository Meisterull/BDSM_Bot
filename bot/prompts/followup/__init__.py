"""
Fassade: `bot.prompts.followup` bleibt der Zugriffspfad.

Der frühere Monolith (bot/prompts/followup.py) liegt jetzt in bot/prompts/followup/ (05.11.2026):
  bausteine          – Gemeinsame Prompt-Bausteine: Nutzer-Text-Delimiter, Rollen-Formen.
  sub_nachrichten    – Prompts für Nachrichten an den Sub: Follow-up, Aufgabe, Wette, Strafe, Reaktionen, Serie/Kette.
  stimmung_fragen    – Stimmungs- und Gefühlsfragen, Berichte erledigt/nicht erledigt.
  versaeumnis        – Versäumnis der dominanten Rolle: Beteiligungs-Check, Reaktion, Coach-Text.
  aufgaben_vorschlag – Aufgaben-Vorschlag: Kontext-Rendering, Formel-Verbot, Machbarkeits-Prüfung, Tiny-/ausführlicher Vorschlag.
  wette_vorschlag    – Wettvorschlag des Coachs.

Jeder Name wird bei JEDEM Zugriff im definierenden Teilmodul nachgeschlagen
(PEP 562, __getattr__). Ein Patch auf das Teilmodul wirkt damit für alle Aufrufer.
NIE auf die Fassade patchen (x.name = …) – das überdeckt nur hier. Zum Patchen:
setattr(modul_von("name"), "name", fake).
"""
import importlib

# Geteilte Importe des früheren Monolithen – Alt-Code/Tests erreichen z. B.
# telegram_helper/qdrant über diese Fassade (dieselben Modulobjekte).
import re  # noqa: F401
from bot import config  # noqa: F401
from bot.prompts import persona, rollen  # noqa: F401

_MODUL_VON = {
    "_GEFUEHL_FRAGEN": "stimmung_fragen",
    "_PRUEF_ART": "aufgaben_vorschlag",
    "_STIMMUNG_RICHTUNGEN": "stimmung_fragen",
    "_STIMMUNG_TONLAGEN": "stimmung_fragen",
    "_WETT_AUSGANG": "wette_vorschlag",
    "_aufgaben_kontext": "aufgaben_vorschlag",
    "_du_bist_dom": "bausteine",
    "_formel_verbot": "aufgaben_vorschlag",
    "_gross": "bausteine",
    "_satzanfaenge": "stimmung_fragen",
    "_sub_mit_poss": "bausteine",
    "_versaeumnis_stufe": "versaeumnis",
    "_wie_lange_her": "wette_vorschlag",
    "aufgabe_an_sklaven": "sub_nachrichten",
    "ausfuehrlicher_task_vorschlag": "aufgaben_vorschlag",
    "bericht_erledigt": "stimmung_fragen",
    "bericht_nicht_erledigt": "stimmung_fragen",
    "coach_versaeumnis": "versaeumnis",
    "followup_frage": "sub_nachrichten",
    "gefuehl_abfragen": "stimmung_fragen",
    "herrin_beteiligung_check": "versaeumnis",
    "kette_anpassung": "sub_nachrichten",
    "machbarkeits_pruefung": "aufgaben_vorschlag",
    "nachricht_an_sklaven": "sub_nachrichten",
    "nutzer_text": "bausteine",
    "reaktion_auf_gefuehl": "sub_nachrichten",
    "reaktion_auf_nicht_erledigt": "sub_nachrichten",
    "reaktion_auf_stimmung": "sub_nachrichten",
    "reaktion_herrin_versaeumt": "versaeumnis",
    "serie_variationen": "sub_nachrichten",
    "stimmung_abfragen": "stimmung_fragen",
    "stimmung_richtungen": "stimmung_fragen",
    "strafe_fuer_ablehnung": "sub_nachrichten",
    "tiny_task_vorschlag": "aufgaben_vorschlag",
    "wett_ergebnis_an_dom": "sub_nachrichten",
    "wett_idee": "wette_vorschlag",
    "wette_an_sklaven": "sub_nachrichten",
}


def __getattr__(name: str):
    mod = _MODUL_VON.get(name)
    if mod is None:
        raise AttributeError(f"module 'bot.prompts.followup' has no attribute '{name}'")
    return getattr(importlib.import_module(f"bot.prompts.followup.{mod}"), name)


def __dir__():
    return sorted(_MODUL_VON)


def modul_von(name: str):
    """Das Teilmodul, das `name` definiert – zum Patchen in Tests."""
    return importlib.import_module(f"bot.prompts.followup.{_MODUL_VON[name]}")
