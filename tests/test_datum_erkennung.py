"""
Regressions-Tests für bot/services/datum_erkennung.py (Termin-Aufgaben, 2026-07-22).

Sichert: Termin-Erkennung im Freitext (Wochentag/morgen/Datum), keine
Fehltreffer bei Dezimalzahlen, Uhrzeiten, Tageszeit-"Morgen" und
wiederkehrenden Angaben ("jeden Samstag"), sowie die Wann-Antwort-Auswertung.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_datum_erkennung.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DOMINA_CHAT_ID", "111")
os.environ.setdefault("SKLAVE_CHAT_ID", "222")

try:  # pragma: no cover
    import dotenv  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock
    _m = MagicMock(name="dotenv")
    _m.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = _m

from bot.services import datum_erkennung as de  # noqa: E402

# Fixes "Heute" für deterministische Erwartungen: Mittwoch, 22.07.2026
HEUTE = date(2026, 7, 22)
de._heute = lambda: HEUTE


def test_wochentage():
    # Mittwoch → Samstag = 25.07.
    assert de.finde_termin("Er soll am Samstag das Bad putzen")[0] == date(2026, 7, 25)
    assert de.finde_termin("bis Freitag bitte")[0] == date(2026, 7, 24)
    # Wochentag == heute → sofort (None)
    assert de.finde_termin("am Mittwoch") is None
    # "nächsten Mittwoch" AM Mittwoch = in einer Woche
    assert de.finde_termin("nächsten Mittwoch")[0] == date(2026, 7, 29)
    # Englische Locale
    assert de.finde_termin("clean the bathroom on Saturday")[0] == date(2026, 7, 25)


def test_morgen_uebermorgen():
    assert de.finde_termin("Morgen soll er früh aufstehen")[0] == date(2026, 7, 23)
    assert de.finde_termin("übermorgen ist es soweit")[0] == date(2026, 7, 24)
    assert de.finde_termin("do it tomorrow")[0] == date(2026, 7, 23)
    # Tageszeit-"Morgen" ist KEIN Termin
    assert de.finde_termin("Er soll jeden Morgen seine Übungen machen") is None
    assert de.finde_termin("Guten Morgen!") is None
    assert de.finde_termin("am Morgen soll er das tun") is None
    # "morgens" (Adverb) ebenfalls nicht
    assert de.finde_termin("morgens soll er das immer machen") is None


def test_explizites_datum():
    assert de.finde_termin("Am 26.07. wäscht er das Auto")[0] == date(2026, 7, 26)
    assert de.finde_termin("am 24.12.2026 gibt es was Besonderes")[0] == date(2026, 12, 24)
    # Ohne Jahr, Datum schon vorbei → nächstes Jahr
    assert de.finde_termin("am 01.01. geht es los")[0] == date(2027, 1, 1)
    # Explizit vergangenes Datum → kein Termin
    assert de.finde_termin("am 01.01.2020 war das") is None
    # Datum heute → kein Termin (= sofort)
    assert de.finde_termin("am 22.07. also heute") is None


def test_keine_fehltreffer():
    # Dezimalzahlen und Uhrzeiten sind keine Daten
    assert de.finde_termin("Er trinkt 1.5 Liter Wasser") is None
    assert de.finde_termin("um 18.30 Uhr ist Schluss") is None
    # Wiederkehrend = Serie, kein Einzeltermin
    assert de.finde_termin("jeden Samstag putzt er das Bad") is None
    assert de.finde_termin("er soll das immer samstags machen") is None
    assert de.finde_termin("") is None
    assert de.finde_termin("Er soll heute brav sein") is None


def test_prioritaet():
    # Explizites Datum schlägt Wochentag im selben Satz
    assert de.finde_termin("Samstag, also am 01.08., macht er das")[0] == date(2026, 8, 1)


def test_wann_antwort():
    assert de.parse_termin_antwort("sofort") == "sofort"
    assert de.parse_termin_antwort("Jetzt!") == "sofort"
    assert de.parse_termin_antwort("heute") == "sofort"
    assert de.parse_termin_antwort("now") == "sofort"
    assert de.parse_termin_antwort("morgen") == date(2026, 7, 23)
    assert de.parse_termin_antwort("Samstag") == date(2026, 7, 25)
    assert de.parse_termin_antwort("am 26.07.") == date(2026, 7, 26)
    assert de.parse_termin_antwort("keine Ahnung") is None
    assert de.parse_termin_antwort("") is None


def test_format_termin():
    # persona_config ist lokal ggf. nicht ladbar → Fallback auf BOT_LOCALE (de)
    text = de.format_termin(date(2026, 7, 25))
    assert "25.07.2026" in text
    assert text.split(",")[0] in ("Samstag", "Saturday")


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
