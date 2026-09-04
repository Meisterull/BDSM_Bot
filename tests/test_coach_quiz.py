"""
Tests für das Coach-Quiz (Domina-Seite, 2026-09-04): Themenkatalog-Integrität,
Themen-Wahl (Vorlieben-Andockung, Limits-Ausschluss, Anti-Wiederholung,
offene Themen), Typ-Wahl per Argument, Prompt-Formatierung.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_coach_quiz.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # pragma: no cover
    import telegram  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock
    for _name in [
        "telegram", "telegram.ext", "telegram.constants", "telegram.error",
        "qdrant_client", "qdrant_client.models", "qdrant_client.http",
        "qdrant_client.http.models", "qdrant_client.http.exceptions",
        "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.asyncio",
        "apscheduler.triggers", "apscheduler.triggers.cron",
        "apscheduler.triggers.interval", "httpx", "dotenv",
    ]:
        _m = MagicMock(name=_name)
        _m.__name__ = _name
        _m.__path__ = []
        sys.modules[_name] = _m
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

from bot import config  # noqa: E402
from bot.handlers import coach_quiz  # noqa: E402
from bot.prompts import domina_coach  # noqa: E402
from bot.prompts.presets.coach_quiz_themen import THEMEN  # noqa: E402
from bot.services import qdrant  # noqa: E402


# --------------------------------------------------------------------------
# Themenkatalog-Integrität
# --------------------------------------------------------------------------

def test_katalog_integritaet():
    namen = [t["name"] for t in THEMEN]
    assert len(namen) == len(set(namen)), "Themen-Namen müssen eindeutig sein"
    katalog = set(config.AUFGABEN_KATEGORIEN)
    for thema in THEMEN:
        assert len(thema["fakten"]) >= 3, f"{thema['name']}: zu wenige Fakten"
        for k in thema["kategorien"]:
            assert k in katalog, f"{thema['name']}: unbekannte Kategorie {k!r}"
    basis = [t for t in THEMEN if not t["kategorien"]]
    assert len(basis) >= 5, "Es braucht einen soliden Basiswissen-Grundstock"


# --------------------------------------------------------------------------
# Themen-Wahl
# --------------------------------------------------------------------------

def _mit_stubs(vorlieben, hard_limits, grenzen, juengste, offene, wuerfel=0.99):
    """_thema_waehlen mit gestubbten Qdrant-/Random-Abhängigkeiten ausführen."""
    async def fake_profile(user_id):
        if user_id == "sklave":
            return {"vorlieben": vorlieben, "hard_limits": hard_limits}
        return {"grenzen": grenzen}

    async def fake_recent(user_id, limit=15):
        return [{"thema": n} for n in juengste]

    async def fake_offene(user_id):
        return list(offene)

    orig = (qdrant.get_user_profile, qdrant.get_recent_quiz_wissen,
            qdrant.get_offene_quiz_themen, coach_quiz.random.random)
    qdrant.get_user_profile = fake_profile
    qdrant.get_recent_quiz_wissen = fake_recent
    qdrant.get_offene_quiz_themen = fake_offene
    coach_quiz.random.random = lambda: wuerfel
    try:
        return asyncio.run(coach_quiz._thema_waehlen())
    finally:
        (qdrant.get_user_profile, qdrant.get_recent_quiz_wissen,
         qdrant.get_offene_quiz_themen, coach_quiz.random.random) = orig


def test_thema_wahl_limits_und_vorlieben():
    # Ohne Vorlieben: nur Basiswissen im Pool
    for _ in range(10):
        thema = _mit_stubs([], [], [], [], [])
        assert not thema["kategorien"], "Ohne Vorlieben darf kein Vorlieben-Thema kommen"
    # Vorliebe Spanking dockt das Impact-Thema an
    gesehen = {_mit_stubs(["Spanking mit der Hand"], [], [], [], [])["name"]
               for _ in range(60)}
    assert "Impact: Zonen und Aufwärmen" in gesehen
    # Dieselbe Kategorie als Hard Limit schließt das Thema aus
    gesehen = {_mit_stubs(["Spanking mit der Hand"], ["kein Spanking mehr"], [], [], [])["name"]
               for _ in range(60)}
    assert "Impact: Zonen und Aufwärmen" not in gesehen


def test_thema_wahl_anti_wiederholung_und_offene():
    basis_namen = [t["name"] for t in THEMEN if not t["kategorien"]]
    # Verbrauchte Themen kommen nicht wieder …
    verbraucht = basis_namen[:-1]
    for _ in range(10):
        assert _mit_stubs([], [], [], verbraucht, [])["name"] == basis_namen[-1]
    # … außer ALLES ist verbraucht – dann lieber wiederholen als verstummen
    assert _mit_stubs([], [], [], basis_namen, []) is not None
    # Offene (falsch beantwortete) Themen werden bei niedrigem Würfel bevorzugt
    thema = _mit_stubs([], [], [], [], {basis_namen[0]}, wuerfel=0.0)
    assert thema["name"] == basis_namen[0]


# --------------------------------------------------------------------------
# Typ-Wahl + Formatierung
# --------------------------------------------------------------------------

def test_typ_wahl_argumente():
    assert coach_quiz._typ_waehlen(["wissen"]) == "wissen"
    assert coach_quiz._typ_waehlen(["LERNEN"]) == "wissen"
    assert coach_quiz._typ_waehlen(["sklave"]) == "sklave"
    assert coach_quiz._typ_waehlen(["sub"]) == "sklave"
    assert coach_quiz._typ_waehlen(["quatsch"]) in ("wissen", "sklave")
    assert coach_quiz._typ_waehlen([]) in ("wissen", "sklave")


def test_format_quiz_wissen():
    assert domina_coach.format_quiz_wissen([]) == ""
    text = domina_coach.format_quiz_wissen([
        {"thema": "Sub-Drop und Top-Drop", "inhalt": "Drop kann verzögert kommen.",
         "status": "gelernt"},
        {"thema": "Enema sicher gestalten", "inhalt": "Nur körperwarmes Wasser.",
         "status": "offen"},
    ])
    assert "Sub-Drop und Top-Drop" in text
    assert "falsch beantwortet" in text and text.count("falsch beantwortet") == 1
    assert "Nur körperwarmes Wasser." in text


def _run():
    test_katalog_integritaet()
    test_thema_wahl_limits_und_vorlieben()
    test_thema_wahl_anti_wiederholung_und_offene()
    test_typ_wahl_argumente()
    test_format_quiz_wissen()
    print("✅ Alle Coach-Quiz-Tests bestanden")


if __name__ == "__main__":
    _run()
