"""
Regressions-Tests zur Log-Durchsicht vom 20.11.2026. Vier Befunde:

  1. Wett-Ergebnis: die Meldung an die Dom-Seite richtet sich nach der Ansage,
     die der Sub gehört hat – nicht nach einer eigenen Deutung der Idee
  2. Werktags-Vorschlag: die Komplexitäts-Zeile nennt keine Dauer (die gibt der
     Tiny-Rahmen vor)
  3. Stimmungsfrage: Ersatz-Anreden werden entfernt, nicht nur das angehängte „du"
  4. Coach-Impuls startet nicht, solange der Tages-Vorschlag entsteht; ein
     gewonnener Wurf wird beim nächsten Tick nachgeholt

    python3 tests/test_review_d14.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402

config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot.handlers import coach_quiz as cq  # noqa: E402
from bot.handlers import stimmung as st  # noqa: E402
from bot.handlers.waehrung import wette_annahme as wa  # noqa: E402
from bot.prompts import coach_persona, domina_coach  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.scheduler import basis, impulse  # noqa: E402
from bot.scheduler import followup as sched  # noqa: E402
from bot.services import paare, persona_config  # noqa: E402
from bot.services.db import profile as db_profile, vorschlaege as db_vorschlaege  # noqa: E402

IDEE = "Er wettet, dass er bis morgen Abend kein Widerwort gibt. Schafft er es, bekommt er Tee ans Bett. Sonst macht er den Abwasch."
ANSAGE = ("Ich wette, du schaffst keinen Abend ohne Widerwort. [pause] Schaffst du es doch, hast du gewonnen "
          "<soft>und bekommst Tee ans Bett</soft>. Sonst machst du den Abwasch. Nimmst du an?")


class _Flicken:
    """Setzt Attribute für die Dauer eines Tests und stellt sie wieder her."""

    def __init__(self, *eintraege):
        self.eintraege = eintraege
        self.alt = []

    def __enter__(self):
        for obj, name, wert in self.eintraege:
            self.alt.append((obj, name, getattr(obj, name)))
            setattr(obj, name, wert)
        return self

    def __exit__(self, *exc):
        for obj, name, wert in reversed(self.alt):
            setattr(obj, name, wert)


# --------------------------------------------------------------------------
# 1. Wett-Ergebnis folgt der Ansage
# --------------------------------------------------------------------------

def test_ergebnis_prompt_kennt_die_ansage():
    system, user = fp.wett_ergebnis_an_dom(IDEE, True, ansage="Schaffst du es, hast du gewonnen.")
    assert "MASSGEBLICH" in system and "„gewonnen“ gemeldet" in system
    assert "Abmachung der Wette" in user and "Schaffst du es, hast du gewonnen." in user
    assert user.index("Abmachung der Wette") < user.index("Ansage an")
    system, _ = fp.wett_ergebnis_an_dom(IDEE, False, ansage="Schaffst du es, hast du gewonnen.")
    assert "„verloren“ gemeldet" in system
    # Ohne gespeicherte Ansage (alte Wetten): Prompt wie bisher.
    system, user = fp.wett_ergebnis_an_dom(IDEE, True)
    assert "MASSGEBLICH" not in system and "Ansage an" not in user
    assert fp.wett_ergebnis_an_dom(IDEE, True, ansage="   ")[1] == user


async def test_einsatz_text_reicht_die_ansage_durch():
    prompts = []

    async def simple(prompt, **kw):
        prompts.append(prompt)
        return "Er hat gewonnen, du bringst ihm den Tee ans Bett."

    w = {"kennung": "abc", "idee": IDEE, "ansage": ANSAGE}
    with _Flicken((wa.grok, "simple", simple),
                  (wa.grok, "clean_text", lambda x: (x or "").strip()),
                  (db_profile, "get_user_profile", AsyncMock(return_value={})),
                  (wa.limits_check, "verletzungen", AsyncMock(return_value=[]))):
        text = await wa._einsatz_text(w, "gewonnen")
        assert "Tee ans Bett" in text
        system, user = prompts[0]
        assert "MASSGEBLICH" in system
        # Die Ansage kommt ohne Sprech-Tags an.
        assert "Schaffst du es doch, hast du gewonnen und bekommst Tee ans Bett." in user
        assert "[pause]" not in user and "<soft>" not in user
        # Wette ohne gespeicherte Ansage: weiter wie bisher.
        await wa._einsatz_text({"kennung": "abc", "idee": IDEE}, "verloren")
        assert "MASSGEBLICH" not in prompts[1][0]


# --------------------------------------------------------------------------
# 2. Komplexität ohne Dauer im Werktags-Vorschlag
# --------------------------------------------------------------------------

def test_komplexitaet_ohne_dauer_im_tiny_prompt():
    for stufe in ("niedrig", "normal", "hoch"):
        mit, ohne = coach_persona.schwierigkeit_zeile(stufe), coach_persona.schwierigkeit_zeile(stufe, mit_dauer=False)
        assert "Min." in mit and "Min" not in ohne and ohne.startswith(f"Aufgaben-Komplexität: {stufe} → ")
        assert mit.startswith(ohne)
    basis_kw = dict(erfahrungsstand="x", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[])
    system, user = fp.tiny_task_vorschlag(schwierigkeit="normal", **basis_kw)
    assert "Tiny: 5-15 Min" in system
    assert "Aufgaben-Komplexität: normal → Standard-Aufgaben (die Dauer gibt der Tiny-Rahmen vor)" in user
    assert "15-30 Min" not in user and "30-60 Min" not in fp.tiny_task_vorschlag(schwierigkeit="hoch", **basis_kw)[1]
    # Der Wochenend-Vorschlag und der Coach-Chat nennen die Dauer weiter.
    _, lang = fp.ausfuehrlicher_task_vorschlag(schwierigkeit="normal", **basis_kw)
    assert "Standard-Aufgaben (15-30 Min.)" in lang and "gilt je Schritt" in lang
    assert "(15-30 Min.)" in domina_coach.get(erfahrungsstand="x", level=2, interessen=[], grenzen=[], ziele="",
                                              conversation_context="", schwierigkeit="normal")


# --------------------------------------------------------------------------
# 3. Ersatz-Anreden in der Stimmungsfrage
# --------------------------------------------------------------------------

def test_ersatz_anrede_filter():
    eigene = "Kleine Maus"
    weg = {
        "Wie läuft dein Tag bisher, mein Lieber?": "Wie läuft dein Tag bisher?",
        "Wie geht es dir gerade, Süßer?": "Wie geht es dir gerade?",
        "Was treibt dich um, mein Kleiner?": "Was treibt dich um?",
        "Schatz, wie war dein Tag?": "Wie war dein Tag?",
        "Mein Lieber, wie steht es um deine Laune?": "Wie steht es um deine Laune?",
        "Sag mal, mein Lieber, wie geht es dir?": "Sag mal, wie geht es dir?",
        "Wie fühlst du dich, kleiner Mann?": "Wie fühlst du dich?",
        "Na, mein Schatz, alles gut bei dir?": "Na, alles gut bei dir?",
    }
    for ein, aus in weg.items():
        assert st._ohne_ersatz_anrede(ein, eigene) == aus, ein
    bleibt = [
        "Wie viel Energie hast du noch, Kleine Maus?",          # die eingestellte Anrede
        "Kleine Maus, wie geht es dir?",
        "Bist du heute eher aufgedreht, oder lieber ruhig?",     # echte Satzteile
        "Was wäre dir jetzt lieber, Ruhe oder Trubel?",
        "Wie bist du drauf, guter Dinge oder eher matt?",
        "Lieber heute als morgen, oder wie siehst du das?",
        "Fühlst du dich klein, müde oder beides?",
        "Wie geht es dir, ganz ehrlich?",
        "",
    ]
    for text in bleibt:
        assert st._ohne_ersatz_anrede(text, eigene) == text, text
    # Ohne eingestellte Anrede fällt jeder Kosename.
    assert st._ohne_ersatz_anrede("Wie geht es dir, Kleine Maus?", "") == "Wie geht es dir?"


async def test_stimmungsfrage_ohne_ersatz_anrede():
    antworten = ["Was macht deine Laune heute, mein Süßer?"]

    async def simple(prompt, **kw):
        return antworten[0]

    with _Flicken((db_vorschlaege, "get_recent_stimmung_eintraege", AsyncMock(return_value=[])),
                  (st.grok, "simple", simple),
                  (persona_config, "sklave_anrede", lambda: "Kleine Maus")):
        frage, _ = await st._frage_text()
        assert frage == "Was macht deine Laune heute?", frage
        antworten[0] = "Was macht deine Laune heute, Kleine Maus?"
        frage, _ = await st._frage_text()
        assert frage.endswith(", Kleine Maus?"), frage
        antworten[0] = "Sag schon, wie geht es dir, du?"
        frage, _ = await st._frage_text()
        assert frage == "Sag schon, wie geht es dir?", frage


# --------------------------------------------------------------------------
# 4. Coach-Impuls wartet auf den Tages-Vorschlag
# --------------------------------------------------------------------------

def test_in_arbeit_marker():
    assert not basis.ist_in_arbeit(basis.TAGES_VORSCHLAG)
    with basis.in_arbeit(basis.TAGES_VORSCHLAG):
        assert basis.ist_in_arbeit(basis.TAGES_VORSCHLAG) and not basis.ist_in_arbeit("anderes")
        # Ein hängender Lauf sperrt nicht ewig.
        schluessel = (paare.aktueller_kontext(), basis.TAGES_VORSCHLAG)
        basis._in_arbeit[schluessel] = datetime.now(timezone.utc) - basis._IN_ARBEIT_MAX - timedelta(seconds=5)
        assert not basis.ist_in_arbeit(basis.TAGES_VORSCHLAG)
    assert not basis._in_arbeit
    # Auch bei einem Fehler im Block wird die Marke entfernt.
    try:
        with basis.in_arbeit(basis.TAGES_VORSCHLAG):
            raise RuntimeError("kaputt")
    except RuntimeError:
        pass
    assert not basis.ist_in_arbeit(basis.TAGES_VORSCHLAG)


async def test_vorschlag_setzt_die_marke():
    from bot.scheduler import tiny_task
    gesehen = []

    async def kern(bot):
        gesehen.append(basis.ist_in_arbeit(basis.TAGES_VORSCHLAG))

    with _Flicken((tiny_task, "_tiny_task_vorschlag_senden", kern)):
        await tiny_task._send_tiny_task_vorschlag(None)
    assert gesehen == [True] and not basis.ist_in_arbeit(basis.TAGES_VORSCHLAG)


async def test_coach_impuls_wartet_auf_den_tages_vorschlag():
    gesendet, wuerfe = [], []
    profil: dict = {"coach_impuls_letzter_typ": "coach_quiz"}

    async def fake_wette(bot, schwerpunkt=""):
        gesendet.append(schwerpunkt)
        return True

    def wurf():
        wuerfe.append(1)
        return 0.0          # der Würfel sagt immer ja

    async def patch(uid, felder, **kw):
        profil.update(felder)

    impulse._impuls_claim = None
    impulse._coach_vorgemerkt.clear()
    with _Flicken((sched.config, "COACH_IMPULS", True), (sched.config, "COACH_IMPULS_QUIZ", False),
                  (sched.config, "COACH_IMPULS_CHANCE", 0.5),
                  (basis, "_flow_aktiv", lambda *a, **k: False),
                  (sched.zeiten, "ist_im_fenster", lambda *a, **k: True),
                  (db_profile, "get_user_profile", AsyncMock(side_effect=lambda rolle: profil)),
                  (db_profile, "patch_profile_fields", patch),
                  (cq, "sende_wett_idee", fake_wette),
                  (impulse.random, "random", wurf)):
        # Der Tages-Vorschlag entsteht gerade: Wurf gewonnen, aber nichts geht raus.
        with basis.in_arbeit(basis.TAGES_VORSCHLAG):
            await impulse.coach_impuls_job(None)
            assert gesendet == [] and len(wuerfe) == 1
            assert paare.aktueller_kontext() in impulse._coach_vorgemerkt
            assert "coach_impuls_letzte_am" not in profil
            # noch ein Tick währenddessen: bleibt vorgemerkt, kein neuer Wurf
            await impulse.coach_impuls_job(None)
            assert gesendet == [] and len(wuerfe) == 1
        # Nächster Tick, der Vorschlag ist fertig: ohne neuen Wurf nachgeholt.
        impulse._impuls_claim = None
        await impulse.coach_impuls_job(None)
        assert gesendet == [""] and len(wuerfe) == 1
        assert not impulse._coach_vorgemerkt and profil["coach_impuls_letzter_typ"] == "wett_idee"

        # Eine alte Vormerkung verfällt: dann entscheidet wieder der Würfel.
        gesendet.clear(); wuerfe.clear(); profil.pop("coach_impuls_letzte_am", None)
        impulse._impuls_claim = None
        impulse._coach_vorgemerkt[paare.aktueller_kontext()] = (
            datetime.now(timezone.utc) - impulse._COACH_VORGEMERKT_MAX - timedelta(minutes=1))
        await impulse.coach_impuls_job(None)
        assert len(wuerfe) == 1 and gesendet == [""]

        # Wunsch-Wettvorschlag für heute wartet ebenfalls – und bleibt im Profil stehen.
        gesendet.clear(); impulse._impuls_claim = None
        from zoneinfo import ZoneInfo
        heute = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
        profil["coach_wette_wunsch"] = {"datum": heute, "schwerpunkt": "Filmabend"}
        with basis.in_arbeit(basis.TAGES_VORSCHLAG):
            await impulse.coach_impuls_job(None)
        assert gesendet == [] and profil["coach_wette_wunsch"]["schwerpunkt"] == "Filmabend"
        await impulse.coach_impuls_job(None)
        assert gesendet == ["Filmabend"] and profil["coach_wette_wunsch"] is None
    impulse._impuls_claim = None
    impulse._coach_vorgemerkt.clear()


if __name__ == "__main__":
    test_ergebnis_prompt_kennt_die_ansage()
    asyncio.run(test_einsatz_text_reicht_die_ansage_durch())
    test_komplexitaet_ohne_dauer_im_tiny_prompt()
    test_ersatz_anrede_filter()
    asyncio.run(test_stimmungsfrage_ohne_ersatz_anrede())
    test_in_arbeit_marker()
    asyncio.run(test_vorschlag_setzt_die_marke())
    asyncio.run(test_coach_impuls_wartet_auf_den_tages_vorschlag())
    print("✅ Alle Review-D14-Tests bestanden")
