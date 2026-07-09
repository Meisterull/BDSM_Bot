"""
Regressions-Tests für bot/prompts/rollen.py – Rollen-Labels, Pronomen und die
generierte Anatomie-GRUNDIERUNG über alle vier Geschlechter-Kombinationen
(Veröffentlichungs-Schritt 1, 2026-07-02).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_rollen.py
    # oder, falls pytest installiert:
    pytest tests/test_rollen.py
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Falls die echten Telegram/Qdrant-Deps fehlen (lokal), durch Stubs ersetzen.
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

from bot.prompts import persona, rollen  # noqa: E402
from bot.services import persona_config  # noqa: E402


@contextlib.contextmanager
def _rollen(dom: str, sub: str, **extra):
    """Setzt die Rollen-Konstellation (+ optionale persona_config-Felder) im
    Cache und stellt danach den vorherigen Stand wieder her."""
    alt = dict(persona_config._cache)
    persona_config._cache["dom_geschlecht"] = dom
    persona_config._cache["sub_geschlecht"] = sub
    persona_config._cache.update(extra)
    try:
        yield
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)


def test_default_ist_bestandsverhalten():
    """Leere Config = Frau-Herrin/Mann-Sklave (wie vor dem Umbau)."""
    with _rollen("", ""):
        assert rollen.dom()["label"] == "Herrin"
        assert rollen.sub()["label"] == "Sklave"
        g = rollen.grundierung()
        assert "Du bist eine FRAU" in g
        assert "kein Sperma und keinen Penis" in g
        assert "ER spritzt in DICH" in g


def test_unbekannte_werte_fallen_auf_default():
    with _rollen("divers", "xyz"):
        assert rollen.dom_geschlecht() == "frau"
        assert rollen.sub_geschlecht() == "mann"


def test_mann_dom_frau_sub():
    with _rollen("mann", "frau"):
        assert rollen.dom()["anrede"] == "dein Herr"
        assert rollen.sub()["label"] == "Sklavin"
        assert rollen.dom_poss_aus_sub_sicht() == "ihres Herrn"  # Genitiv: "aus der Ich-Form ihres Herrn"
        g = rollen.grundierung()
        assert "Du bist ein MANN" in g
        assert "DU spritzt in SIE" in g
        assert "was sie sagt" in g  # Sub-Pronomen in den Verstehens-Regeln


def test_frau_frau_keine_sperma_quelle():
    with _rollen("frau", "frau"):
        g = rollen.grundierung()
        assert "beide FRAUEN" in g
        assert "KEINE reale Sperma-Quelle" in g


def test_mann_mann_beide_quellen():
    with _rollen("mann", "mann"):
        assert rollen.dom_poss_aus_sub_sicht() == "seines Herrn"
        g = rollen.grundierung()
        assert "beide MÄNNER" in g
        assert "WESSEN Sperma" in g


def test_persona_prompt_nutzt_rollen():
    """fuer_sklaven_prompt rendert Identität/Anrede/Grundierung aus der Konstellation."""
    with _rollen("mann", "frau", bot_name="", sklave_anrede="Kleine Maus"):
        p = persona.fuer_sklaven_prompt()
        assert '"dein Herr"' in p
        assert 'Er spricht sie an als "Kleine Maus"' in p
        assert "Du bist ein MANN" in p
    with _rollen("", "", bot_name="", sklave_anrede="Kleine Maus"):
        p = persona.fuer_sklaven_prompt()
        assert '"deine Herrin"' in p
        assert 'Sie spricht ihn an als "Kleine Maus"' in p
    # Mit Bot-Name: Possessiv passt zum Dom-Geschlecht
    with _rollen("mann", "frau", bot_name="Viktor", sklave_anrede=""):
        p = persona.fuer_sklaven_prompt()
        assert "Sein Name ist Viktor" in p
    with _rollen("", "", bot_name="Alexa", sklave_anrede=""):
        p = persona.fuer_sklaven_prompt()
        assert "Ihr Name ist Alexa" in p


def _run():
    test_default_ist_bestandsverhalten()
    test_unbekannte_werte_fallen_auf_default()
    test_mann_dom_frau_sub()
    test_frau_frau_keine_sperma_quelle()
    test_mann_mann_beide_quellen()
    test_persona_prompt_nutzt_rollen()
    print("✅ Alle Rollen-Tests bestanden")


if __name__ == "__main__":
    _run()
