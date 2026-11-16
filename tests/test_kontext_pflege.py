"""
Tests zur Daten-Pflege vor dem Prompt (Review D10, bot/services/kontext_pflege):
Alter/Altersangabe, Steuer-Tags, brauchbare Rückmeldungen, bereinigter Verlauf.

    python3 tests/test_kontext_pflege.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services import kontext_pflege as kp  # noqa: E402


def _vor(tage: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


def test_alter():
    assert kp.alter_tage(_vor(3)) == 3
    assert kp.alter_tage("") is None and kp.alter_tage("kaputt") is None
    assert kp.alters_hinweis(_vor(3)) == ""
    assert kp.alters_hinweis(_vor(21)) == ", vor 3 Wochen"
    assert kp.alters_hinweis(_vor(150)) == ", vor 5 Monaten"


def test_steuer_tags():
    roh = "Mach ich.\n\n[SPRACHNACHRICHT: Heute Abend räumst du auf.]  \n\n[AUFGABE: Aufräumen]\n**Fett** bleibt Text."
    sauber = kp.ohne_steuer_tags(roh)
    assert "[" not in sauber and "**" not in sauber, sauber
    assert sauber.startswith("Mach ich.") and sauber.endswith("Fett bleibt Text."), sauber


def test_gefuehl_brauchbar():
    for leer in ("1", "2", "Alles", "Wie bitte?", "ok", "", None, "   ", "👍"):
        assert not kp.gefuehl_brauchbar(leer), leer
    for gut in ("Das Warten war das Schwerste daran", "Sehr gerne", "2 das können wir öfter machen",
                "Langweilig", "Anstrengend"):
        assert kp.gefuehl_brauchbar(gut), gut
    assert kp.brauchbare_gefuehle(["1", "Sehr gerne", "Alles", "War mir zu lang"], limit=1) == ["Sehr gerne"]


def test_verlauf_bereinigt():
    e = [
        {"datum": _vor(2), "zusammenfassung": "Sub: Guten Abend\nDom: Na?"},
        {"datum": _vor(90), "zusammenfassung": "Sub: uralt"},
        {"datum": _vor(10), "zusammenfassung": "Sub: Guten Abend\nDom: Na?"},   # Doppel
        {"datum": _vor(5), "zusammenfassung": "Sub: mittendrin"},
        {"datum": "", "zusammenfassung": "Sub: ohne Datum"},
    ]
    out = kp.verlauf_bereinigt(e, max_alter_tage=30)
    texte = [x["zusammenfassung"] for x in out]
    assert "Sub: uralt" not in texte and texte.count("Sub: Guten Abend\nDom: Na?") == 1, texte
    datiert = [x["datum"] for x in out if x["datum"]]
    assert datiert == sorted(datiert), datiert          # ältester zuerst
    assert len(kp.verlauf_bereinigt(e)) == 4            # ohne Altersgrenze nur Doppel raus


def test_kommando_und_profil_behauptung():
    assert kp.ist_kommando(" /wuensche loeschen 3") and not kp.ist_kommando("heute war viel los")
    assert kp.behauptet_profil_aenderung("Das ist notiert und fest verankert.")
    assert kp.behauptet_profil_aenderung("Ab sofort gehört das dazu.")
    assert not kp.behauptet_profil_aenderung("Dann zeig mal, was du kannst.")


if __name__ == "__main__":
    test_alter()
    test_steuer_tags()
    test_gefuehl_brauchbar()
    test_verlauf_bereinigt()
    test_kommando_und_profil_behauptung()
    print("✅ Alle Kontext-Pflege-Tests bestanden")
