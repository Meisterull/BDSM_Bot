"""
Tests für den lokalen Notbetrieb bei Grok-Ausfall (2026-07-04):
bot/services/lokal_llm.py (History-Stutzung, aktiv-Gate) und
bot/prompts/sklave.get_kurz (abgespeckter Fallback-System-Prompt).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_lokal_llm.py
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

from bot import config  # noqa: E402
from bot.prompts import sklave  # noqa: E402
from bot.services import lokal_llm, persona_config  # noqa: E402


@contextlib.contextmanager
def _persona(**felder):
    alt = dict(persona_config._cache)
    persona_config._cache.update(felder)
    try:
        yield
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)


def test_get_kurz_kern():
    """Grenzen beider Seiten und die Rollen-Kopfzeile sind drin, das teure
    Wissen/Dossier-Zeug (Marker des vollen Prompts) nicht."""
    p = sklave.get_kurz(["Blut", "Nadeln"], ["Fußanbetung"])
    assert "Blut, Nadeln" in p
    assert "Fußanbetung" in p
    assert "Ich-Form" in p
    assert "NIEMALS" in p
    # Kein Voll-Prompt-Ballast:
    assert "WAS DU ÜBER IHN WEISST" not in p
    assert "OFFENE FÄDEN" not in p
    assert "offene/gefragte Aufgaben" not in p
    # Notbetriebs-Regel: nichts versprechen, keine neuen Aufgaben
    assert "KEINE neuen Aufgaben" in p


def test_get_kurz_leere_listen():
    p = sklave.get_kurz([], None)
    assert "keine angegeben" in p


def test_get_kurz_persona_felder():
    """Name/Anrede/Setup/Sprache erscheinen nur, wenn konfiguriert."""
    ohne = sklave.get_kurz([], [])
    assert "SETUP/KONTEXT" not in ohne or persona_config.setup_kontext()
    with _persona(bot_name="Lilith", sklave_anrede="Spielzeug",
                  setup_kontext="Kinder im Haus.", sprache="Englisch"):
        mit = sklave.get_kurz([], [])
        assert "Lilith" in mit
        assert '"Spielzeug"' in mit
        assert "Kinder im Haus." in mit
        assert "Englisch" in mit


def test_get_kurz_bleibt_kurz():
    """Der ganze Sinn des Builders: auch mit realistischen Daten klein genug
    für CPU-Prompt-Verarbeitung (~12 tok/s) bleiben."""
    p = sklave.get_kurz(["Limit " + str(i) for i in range(8)],
                        ["Grenze " + str(i) for i in range(5)])
    assert len(p) < 2000, f"Kurz-Prompt zu lang: {len(p)} Zeichen"


def test_kuerze_history():
    history = [{"role": "user", "content": "x" * 1000} for _ in range(10)]
    kurz = lokal_llm.kuerze_history(history, anzahl=4, max_zeichen=300)
    assert len(kurz) == 4
    assert all(len(m["content"]) == 300 for m in kurz)
    # Original bleibt unangetastet
    assert len(history[0]["content"]) == 1000


def test_aktiv_gate():
    alt = config.LOKAL_LLM_MODEL
    try:
        config.LOKAL_LLM_MODEL = ""
        assert not lokal_llm.aktiv()
        config.LOKAL_LLM_MODEL = "irgendein-modell"
        assert lokal_llm.aktiv()
    finally:
        config.LOKAL_LLM_MODEL = alt


def _run():
    test_get_kurz_kern()
    test_get_kurz_leere_listen()
    test_get_kurz_persona_felder()
    test_get_kurz_bleibt_kurz()
    test_kuerze_history()
    test_aktiv_gate()
    print("✅ Alle Lokal-LLM-Tests bestanden")


if __name__ == "__main__":
    _run()
