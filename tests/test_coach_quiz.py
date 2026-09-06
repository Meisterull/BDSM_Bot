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
config.DOMINA_CHAT_ID = config.DOMINA_CHAT_ID or "111"
config.SKLAVE_CHAT_ID = config.SKLAVE_CHAT_ID or "222"
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


# --------------------------------------------------------------------------
# 06.09.: Quiz-Verfall, Impuls-Kollision, Prompt-Härtung
# --------------------------------------------------------------------------

def test_stale_parkt_verfallenes_wissensquiz():
    """Stale-Reset eines Fachwissen-Quiz parkt die Auflösung für den
    Nachreich-Send und räumt die coach_quiz_*-Keys (fehlten in FLOW_STATE_KEYS)."""
    import time
    from bot import state
    cid = "999001"
    s = state.get(cid)
    coach_quiz._scharf_schalten(cid, "wissen", "F?", "M", "A", "Thema X")
    s["mode_since"] = time.time() - 99999
    assert state.clear_if_stale(cid) is True
    verf = s.get("coach_quiz_verfallen")
    assert verf and verf["aufloesung"] == "A" and verf["thema"] == "Thema X", verf
    assert not s.get("coach_quiz_frage"), "coach_quiz_* muss clear_flow_keys räumen"
    # Sklaven-Wissen-Quiz (ohne Auflösung) parkt NICHTS
    s.pop("coach_quiz_verfallen", None)
    coach_quiz._scharf_schalten(cid, "sklave", "F?", "M", "", "")
    s["mode_since"] = time.time() - 99999
    assert state.clear_if_stale(cid) is True
    assert not s.get("coach_quiz_verfallen")


def test_impuls_slot_frei():
    """Gegenseitige Impuls-Sperre: persistenter Anker < 2h ODER frischer
    In-Prozess-Claim blockieren den Slot."""
    from datetime import datetime, timezone, timedelta
    from bot.scheduler import followup as sched
    alt = sched._impuls_claim
    try:
        sched._impuls_claim = None
        assert sched._impuls_slot_frei("") is True
        frisch = datetime.now(timezone.utc).isoformat()
        assert sched._impuls_slot_frei(frisch) is False
        vor3h = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        assert sched._impuls_slot_frei(vor3h) is True
        sched._impuls_claim = datetime.now(timezone.utc)
        assert sched._impuls_slot_frei(vor3h) is False
    finally:
        sched._impuls_claim = alt


def test_quiz_verfall_nachreichen():
    """Geparktes Quiz wird nachgereicht (Send + status=offen-Eintrag, Key weg);
    bei aktivem Flow passiert nichts und der Key bleibt für den nächsten Tick."""
    from unittest.mock import AsyncMock, MagicMock
    from bot import state
    from bot.scheduler import followup as sched
    from bot.services import paare, telegram_helper
    cid = paare.dom_chat_id()
    s = state.get(cid)
    daten = {"thema": "T", "frage": "F", "muster": "M", "aufloesung": "A"}
    gesendet, gespeichert = [], []
    orig_send, orig_save = telegram_helper.send_domina, sched.qdrant.save_quiz_wissen
    telegram_helper.send_domina = AsyncMock(
        side_effect=lambda b, txt, **k: gesendet.append(txt))
    sched.qdrant.save_quiz_wissen = AsyncMock(
        side_effect=lambda uid, d: gespeichert.append(d))
    try:
        # aktiver Flow → vertagt
        s["coach_quiz_verfallen"] = dict(daten)
        state.set_mode(cid, "stimmung")
        asyncio.run(sched._quiz_verfall_nachreichen(MagicMock()))
        assert not gesendet and s.get("coach_quiz_verfallen")
        # freier Chat → Send + Wissens-Eintrag, Park-Key weg
        state.set_mode(cid, "chat")
        asyncio.run(sched._quiz_verfall_nachreichen(MagicMock()))
        assert gesendet and "A" in gesendet[0], gesendet
        assert gespeichert and gespeichert[0]["status"] == "offen"
        assert gespeichert[0]["urteil"] == "UNBEANTWORTET"
        assert not s.get("coach_quiz_verfallen")
    finally:
        telegram_helper.send_domina = orig_send
        sched.qdrant.save_quiz_wissen = orig_save
        state.set_mode(cid, "chat")


def test_frage_prompts_gehaertet():
    """Alle drei Frage-Generatoren verbieten Doppelfragen und das Vorwegnehmen
    der Antwort in der Frage (Live-Befund 05./06.09.)."""
    from unittest.mock import AsyncMock
    from bot.handlers import quiz as sub_quiz
    captured = {}

    async def fake_simple(prompt, system="", **kw):
        captured["system"] = system
        return '{"frage": "F?", "antwort": "M"}'

    async def fake_retry(prompt, sklave_hard_limits=None, domina_grenzen=None,
                         system="", **kw):
        captured["wsystem"] = system
        return '{"frage": "F?", "musterantwort": "M", "aufloesung": "A B C."}'

    orig_simple = coach_quiz.grok.simple
    orig_retry = coach_quiz.limits_check.generate_mit_limit_retry
    orig_profil = coach_quiz.qdrant.get_user_profile
    coach_quiz.grok.simple = fake_simple
    coach_quiz.limits_check.generate_mit_limit_retry = fake_retry
    coach_quiz.qdrant.get_user_profile = AsyncMock(return_value={})
    try:
        assert asyncio.run(coach_quiz._generiere_sklavenfrage("999002", "Daten: X"))
        sk_system = captured.pop("system")
        assert "Doppelfrage" in sk_system and "vorwegnehmen" in sk_system
        assert asyncio.run(sub_quiz._generiere_frage("999003", "Daten: Y"))
        sub_system = captured.pop("system")
        assert "Doppelfrage" in sub_system and "vorwegnehmen" in sub_system
        assert asyncio.run(coach_quiz._generiere_wissensfrage(
            {"name": "T", "fakten": ["f1", "f2", "f3"], "kategorien": []}))
        assert "Doppelfrage" in captured["wsystem"]
        assert "nicht enthalten oder nahelegen" in captured["wsystem"]
    finally:
        coach_quiz.grok.simple = orig_simple
        coach_quiz.limits_check.generate_mit_limit_retry = orig_retry
        coach_quiz.qdrant.get_user_profile = orig_profil


def _run():
    test_katalog_integritaet()
    test_thema_wahl_limits_und_vorlieben()
    test_thema_wahl_anti_wiederholung_und_offene()
    test_typ_wahl_argumente()
    test_format_quiz_wissen()
    test_stale_parkt_verfallenes_wissensquiz()
    test_impuls_slot_frei()
    test_quiz_verfall_nachreichen()
    test_frage_prompts_gehaertet()
    print("✅ Alle Coach-Quiz-Tests bestanden")


if __name__ == "__main__":
    _run()
