"""
Regressions-Tests für die Abwesenheit (/abwesend, 2026-07-25):
Zeitraum-Erkennung (datum_erkennung.finde_zeitraum), Zustand + Auto-Ablauf
(persona_config.abwesenheit/ist_abwesend), Grund-Extraktion und Prompt-Baustein.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_abwesenheit.py
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DOMINA_CHAT_ID"] = "111"
os.environ["SKLAVE_CHAT_ID"] = "222"

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

from bot.services import datum_erkennung as de  # noqa: E402
from bot.services import persona_config as pc  # noqa: E402
from bot.handlers import abwesenheit  # noqa: E402

# Fixes "Heute" für deterministische Erwartungen: Samstag, 25.07.2026
HEUTE = date(2026, 7, 25)
de._heute = lambda: HEUTE
pc._heute_lokal = lambda: HEUTE


def test_zeitraum_zwei_daten():
    assert de.finde_zeitraum("27.07.-02.08. Dienstreise") == (date(2026, 7, 27), date(2026, 8, 2))
    assert de.finde_zeitraum("vom 27.07. bis 02.08.") == (date(2026, 7, 27), date(2026, 8, 2))
    # Jahreswechsel: bis löst sich relativ zu von auf
    assert de.finde_zeitraum("28.12.-03.01.") == (date(2026, 12, 28), date(2027, 1, 3))


def test_zeitraum_ein_datum():
    # EIN künftiges Datum = bis, ab heute
    assert de.finde_zeitraum("bis 02.08.") == (HEUTE, date(2026, 8, 2))
    # heutiges Datum allein ist kein Zeitraum
    assert de.finde_zeitraum("25.07.") is None


def test_zeitraum_dauer():
    assert de.finde_zeitraum("2 wochen Dienstreise") == (HEUTE, date(2026, 8, 8))
    assert de.finde_zeitraum("10 Tage weg") == (HEUTE, date(2026, 8, 4))
    # Startdatum + Dauer
    assert de.finde_zeitraum("ab 27.07. 2 wochen") == (date(2026, 7, 27), date(2026, 8, 10))


def test_zeitraum_termin_formen():
    # Wochentag/übermorgen über finde_termin (Sa 25.07. → So 26.07.)
    assert de.finde_zeitraum("bis Sonntag") == (HEUTE, date(2026, 7, 26))
    assert de.finde_zeitraum("bis übermorgen") == (HEUTE, date(2026, 7, 27))


def test_zeitraum_unverstanden():
    assert de.finde_zeitraum("") is None
    assert de.finde_zeitraum("keine Ahnung") is None
    # Dezimalzahl/Uhrzeit sind kein Datum
    assert de.finde_zeitraum("1.5 Liter trinken") is None


def test_grund_extraktion():
    assert abwesenheit._grund_aus("27.07.-02.08. Dienstreise") == "Dienstreise"
    assert abwesenheit._grund_aus("ab 27.07. für 2 wochen auf Dienstreise") == "auf Dienstreise"
    assert abwesenheit._grund_aus("bis Sonntag") == ""


def test_zustand_und_ablauf():
    cache = pc._aktueller_cache()
    # aktiv: heute liegt im Zeitraum
    cache.update({"abwesend_von": "2026-07-20", "abwesend_bis": "2026-08-02",
                  "abwesend_grund": "Dienstreise"})
    assert pc.abwesenheit() == (date(2026, 7, 20), date(2026, 8, 2), "Dienstreise")
    assert pc.ist_abwesend() is True
    # geplant: sichtbar (Prompts), aber pausiert noch nichts
    cache.update({"abwesend_von": "2026-07-28", "abwesend_bis": "2026-08-02"})
    assert pc.abwesenheit() is not None
    assert pc.ist_abwesend() is False
    # abgelaufen: läuft von selbst aus
    cache.update({"abwesend_von": "2026-07-01", "abwesend_bis": "2026-07-10"})
    assert pc.abwesenheit() is None
    assert pc.ist_abwesend() is False
    # nichts gesetzt
    cache.update({"abwesend_von": "", "abwesend_bis": "", "abwesend_grund": ""})
    assert pc.abwesenheit() is None
    assert pc.ist_abwesend() is False


def test_prompt_hinweis():
    cache = pc._aktueller_cache()
    cache.update({"abwesend_von": "2026-07-20", "abwesend_bis": "2026-08-02",
                  "abwesend_grund": "Dienstreise"})
    hinweis = abwesenheit.prompt_hinweis()
    assert "ABWESENHEIT" in hinweis
    assert "20.07.2026" in hinweis and "02.08.2026" in hinweis
    assert "Dienstreise" in hinweis
    assert "03.08.2026" in hinweis  # Rückkehr-Datum = bis + 1
    # geplant → Zukunftsformulierung
    cache.update({"abwesend_von": "2026-07-28"})
    assert "wird" in abwesenheit.prompt_hinweis()
    # keine Abwesenheit → leer
    cache.update({"abwesend_von": "", "abwesend_bis": "", "abwesend_grund": ""})
    assert abwesenheit.prompt_hinweis() == ""


def test_set_abwesenheit_persistiert():
    async def _run():
        gepatcht = {}

        async def fake_patch(user_id, felder):
            gepatcht.update(felder)

        original = pc.qdrant.patch_profile_fields
        pc.qdrant.patch_profile_fields = fake_patch
        try:
            await pc.set_abwesenheit(date(2026, 7, 27), date(2026, 8, 2), "Dienstreise")
            assert gepatcht == {"abwesend_von": "2026-07-27", "abwesend_bis": "2026-08-02",
                                "abwesend_grund": "Dienstreise"}
            assert pc.ist_abwesend() is False  # beginnt erst am 27.07.
            await pc.set_abwesenheit(None, None)
            assert gepatcht["abwesend_von"] == "" and gepatcht["abwesend_grund"] == ""
            assert pc.abwesenheit() is None
        finally:
            pc.qdrant.patch_profile_fields = original

    asyncio.run(_run())


if __name__ == "__main__":
    fehler = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✅ {name}")
            except AssertionError as e:
                fehler += 1
                print(f"  ❌ {name}: {e}")
    sys.exit(1 if fehler else 0)
