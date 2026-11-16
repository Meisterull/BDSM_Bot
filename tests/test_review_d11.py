"""
Regressions-Tests zum Prompt-Review Durchgang 11 (16.11.2026):

  - Gefühlsfrage: nur offene Fragen nach dem Erleben; die gestellte Frage geht
    in Bericht und Reaktion mit
  - Abschluss an die Dom-Seite: EIN Bericht mit Gedanke, ohne Schlussfrage
  - Nachfrage am selben Tag: kein Umrechnungs-Satz
  - Aufgabe an die Sub-Seite: Vollständigkeits-Regel, Ton eines laufenden
    Rollenspiels wird zentral nachgeschlagen; Herrin-Chat kennt das Rollenspiel
  - Quiz über die Sub-Seite: nur Wissen, das die Dom-Seite ohnehin hat
  - Limit-Retry: keine Groß-/Klein-Doppel

    python3 tests/test_review_d11.py
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import state  # noqa: E402
from bot.handlers import coach_quiz  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.prompts import sklave as sklave_prompt  # noqa: E402
from bot.services import limits_check  # noqa: E402


def test_gefuehlsfragen_offen():
    fragen = " | ".join(fp._GEFUEHL_FRAGEN).lower()
    for unbrauchbar in ("skala", "1-5", "nochmal", "stattdessen"):
        assert unbrauchbar not in fragen, fragen
    assert len(fp._GEFUEHL_FRAGEN) >= 4


def test_bericht_eine_nachricht_mit_frage():
    system, user = fp.bericht_erledigt(
        "Den Tisch decken", "Alles", vorherige_gefuehle=["War anstrengend"],
        gefuehl_frage="Was war das Beste daran?", level=3, erledigte=12)
    assert "EINE" in system and "Keine Frage am Schluss" in system and "Level 3" in system, system
    assert "Was war das Beste daran?" in user and user.index("Was war das Beste daran?") < user.index("Alles"), user
    assert "zu anderen Aufgaben" in user and "ähnliche Aufgaben" not in user, user
    assert "erledigte Aufgaben gesamt: 12" in user, user
    # Rückwärtskompatibel ohne Zusatz-Angaben
    system2, user2 = fp.bericht_erledigt("Aufgabe", "gut")
    assert "Gestellte Frage" not in user2 and "Level" not in system2


def test_reaktion_kennt_die_frage():
    _, user = fp.reaktion_auf_gefuehl("Aufgabe", "Alles", "Was war das Beste daran?")
    assert "Was war das Beste daran?" in user
    _, ohne = fp.reaktion_auf_gefuehl("Aufgabe", "Alles")
    assert "Deine Frage" not in ohne


def test_nachfrage_am_selben_tag():
    def zeit(tage):
        system, user = fp.followup_frage("Den Tisch decken", tage_her=tage)
        return system + user
    heute = zeit(0)
    assert "am selben Tag" in heute and "rechne sie um" not in heute, heute
    assert "Frag niemals im Präsens 'heute', wenn es heute war" not in heute
    gestern = zeit(1)
    assert "RÜCKBLICKEND" in gestern and "gestern Abend" in gestern, gestern


def test_rollenspiel_erreicht_die_sub_seite():
    dom = os.environ["DOMINA_CHAT_ID"]
    s = state.get(dom)
    for k in ("szenario_name", "szenario_ton", "rollenspiel_intensitaet"):
        s.pop(k, None)
    assert state.aktives_rollenspiel(dom) is None
    system, _ = fp.aufgabe_an_sklaven("Um acht die Kerzen anzünden")
    assert "Ton-Modus (Rollenspiel)" not in system
    assert "bleiben vollständig erhalten" in system, system
    s.update({"szenario_name": "Probe", "szenario_ton": "kühl und knapp", "rollenspiel_intensitaet": "mittel"})
    try:
        rs = state.aktives_rollenspiel(dom)
        assert rs == {"name": "Probe", "ton": "kühl und knapp", "intensitaet": "mittel"}, rs
        system, _ = fp.aufgabe_an_sklaven("Um acht die Kerzen anzünden")
        assert "Ton-Modus (Rollenspiel): kühl und knapp (Intensität: mittel)" in system, system
        # ausdrücklich übergebener Ton gewinnt
        system, _ = fp.aufgabe_an_sklaven("Um acht die Kerzen anzünden", rollenspiel_ton="verspielt")
        assert "Ton-Modus (Rollenspiel): verspielt" in system and "kühl" not in system
        chat = sklave_prompt.get(hard_limits=[], vorlieben=[], rollenspiel=rs)
        assert "LAUFENDES ROLLENSPIEL" in chat and "kühl und knapp" in chat and "Probe" not in chat, chat[:400]
        assert "LAUFENDES ROLLENSPIEL" not in sklave_prompt.get(hard_limits=[], vorlieben=[])
    finally:
        for k in ("szenario_name", "szenario_ton", "rollenspiel_intensitaet"):
            s.pop(k, None)


async def test_quiz_ueber_sub_ohne_verdecktes_wissen():
    orig = (coach_quiz.qdrant.get_user_profile, coach_quiz.qdrant.get_tasks_by_status)
    coach_quiz.qdrant.get_user_profile = AsyncMock(return_value={
        "vorlieben": ["GEHEIMVORLIEBE"], "persoenlichkeit_tags": ["GEHEIMTAG"],
        "offene_faeden": ["GEHEIMFADEN"], "dossier": "GEHEIMDOSSIER",
        "hard_limits": ["Nadeln"], "streak": 4, "streak_max": 9, "punkte": 120})
    coach_quiz.qdrant.get_tasks_by_status = AsyncMock(return_value=[
        {"aufgabe": "Abends das Schlafzimmer vorbereiten", "erteilt_am": "", "domina_bewertung": 5},
        {"aufgabe": "kurz", "erteilt_am": ""}])
    try:
        k = await coach_quiz._sklave_kontext()
    finally:
        coach_quiz.qdrant.get_user_profile, coach_quiz.qdrant.get_tasks_by_status = orig
    assert "GEHEIM" not in k, k
    assert "Nadeln" in k and "Schlafzimmer vorbereiten" in k and "5★" in k and "120 Punkte" in k, k
    assert "kurz" not in k.split("erledigte Aufgaben")[-1].split("Sein Stand")[0], k


def test_verbotsbegriffe_ohne_doppel():
    begriffe = limits_check.begriffe_zum_verbieten([
        {"limit": "Blut", "matched_via": "blut"}, {"limit": "Nadeln", "matched_via": "Nadelspiel"}])
    assert begriffe == ["Blut", "Nadeln", "Nadelspiel"], begriffe


if __name__ == "__main__":
    test_gefuehlsfragen_offen()
    test_bericht_eine_nachricht_mit_frage()
    test_reaktion_kennt_die_frage()
    test_nachfrage_am_selben_tag()
    test_rollenspiel_erreicht_die_sub_seite()
    asyncio.run(test_quiz_ueber_sub_ohne_verdecktes_wissen())
    test_verbotsbegriffe_ohne_doppel()
    print("✅ Alle Review-D11-Tests bestanden")
