"""
Machbarkeits-Prüfung – zweiter LLM-Durchlauf nach einer Generierung.

Live 17.+18.09.2026: die Regel KÖRPERLICH MACHBAR im Generier-Prompt verlor
(Gerät doppelt belegt, falsche Lage zur Benutzungsangabe im Inventar, Schritt
doppelt, nur um ein Element unterzubringen). Bekanntes Lernmuster: Prompt-Regel
< Detektor + Retry. Hier prüft das Reasoning-Modell den FERTIGEN Text nur auf
Mechanik – Belegung, Haltung/Inventar, Reihenfolge, allein/zu zweit, Anatomie.

Genutzt von: Aufgaben-Vorschlägen (scheduler), Wett-Ideen (handlers/coach_quiz)
und den Strafvorschlägen nach einer abgelehnten Wette (handlers/waehrung).
Fail-open: ist die Prüfung nicht möglich, gilt der Text als stimmig.
"""
import logging

from bot.services import grok, inventar
from bot.prompts import followup as fp

logger = logging.getLogger(__name__)

ARTEN = ("aufgabe", "wette", "strafe")


async def maengel(text: str, art: str = "aufgabe") -> list[str]:
    """Höchstens drei konkrete Mängel; leer = stimmig ODER Prüfung nicht möglich."""
    if not (text or "").strip():
        return []
    try:
        roh = await grok.simple(fp.machbarkeits_pruefung(text, inventar.vorhanden(), art=art),
                                reasoning=True, temperature=0, max_tokens=500)
        daten = grok.parse_json(roh)
        if not isinstance(daten, dict) or daten.get("ok", True):
            return []
        return [str(m).strip()[:240] for m in (daten.get("maengel") or []) if str(m).strip()][:3]
    except Exception:
        logger.exception("Machbarkeits-Prüfung fehlgeschlagen – Text gilt als stimmig")
        return []
