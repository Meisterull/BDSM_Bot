"""
Regressions-Tests zur Log-Durchsicht vom 25. und 26.11.2026. Drei Befunde
(der vierte – Fessel-Belegung in der Machbarkeits-Prüfung – steht in
test_review_d15.py bei den übrigen Prüfpunkten):

  1. Stimmungs-Hinweis an die Dom-Seite sprach von ihr in der dritten Person
     („… während sie …“), obwohl die Du-Form im Prompt steht. Jetzt erkennt
     ein Netz das und lässt einmal neu schreiben.
  2. Ein Wunsch nach Ruhe galt nicht als schlechte Stimmung – der Tagesvorschlag
     danach war hart. Jetzt zählt er für die Intensität wie eine schlechte
     Stimmung (Tages- und Wochenendvorschlag, /inspiration).
  3. Das Kurzlabel eines Vorschlags (NICHT-wiederholen-Liste) verband, was der
     Text trennte, und fiel ab und zu auf den bloßen ersten Satz zurück, weil
     das Modell einen zweiten Block anhängte.
  4. (27.11.) Zwei Tagesvorschläge hintereinander begründeten mit demselben
     früheren Gespräch („vor drei/vier Tagen …“). Stand ein Rückblick in einem
     der letzten beiden Vorschläge, setzt er aus: Auftrag, Kandidaten-Wahl und
     ein Retry.
  5. (27.11.) „den Paddle“ (16× gesendet) – feste Korrektur beim Versand.

Alle Beispiele sind erfunden (Muster, keine Live-Inhalte).

    python3 tests/test_review_d18.py
"""
import asyncio
import inspect
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402

config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot.app import routing  # noqa: E402
from bot.handlers import inspiration, stimmung as st  # noqa: E402
from bot.scheduler import schablonen, tiny_task  # noqa: E402
from bot.services import sprachpflege, telegram_helper  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.services import grok, labels  # noqa: E402


# --------------------------------------------------------------------------
# 1 – Hinweis an die Dom-Seite in der Du-Form
# --------------------------------------------------------------------------

def test_netz_dritte_person():
    schlecht = [
        "Er will es heute ruhig. Vielleicht lässt sie ihn einfach neben sich sitzen.",
        "Gute Laune bei ihm – das kann sie heute ruhig ausnutzen.",
        "Er mag es heute langsam, vielleicht zu ihren Füßen.",
        "Er ist müde, die Herrin sollte es heute sanft angehen.",
        "Er ist müde, deine Domina weiß ja, was dann hilft.",
    ]
    gut = [
        "Er will es heute ruhig. Lass ihn einfach neben dir sitzen.",
        "Nutzt das doch für einen ruhigen Abend, während ihr zusammen etwas schaut.",
        "Leg die Kerzen bereit und zünde sie an, wenn er kniet.",
        "Er ist gut drauf – die Nippelklemmen liegen ja bereit, nimm sie dazu.",
    ]
    for t in schlecht:
        assert st._spricht_ueber_dom(t), t
    for t in gut:
        assert not st._spricht_ueber_dom(t), t


async def test_hinweis_wird_neu_geschrieben():
    dritte = "Er will Ruhe. Vielleicht lässt sie ihn still liegen."
    du = "Er will Ruhe. Lass ihn heute einfach still neben dir liegen."
    mock = AsyncMock(side_effect=[dritte, du])
    with patch.object(st.grok, "simple", mock):
        aus = await st._hinweis_an_dom("DATEN", "SYSTEM")
    assert aus == du, aus
    assert mock.await_count == 2
    zweites_system = mock.await_args_list[1].kwargs["system"]
    assert zweites_system.startswith("SYSTEM") and "dritten Person" in zweites_system
    # stimmt der erste Entwurf, bleibt es bei einem Aufruf
    mock = AsyncMock(return_value=du)
    with patch.object(st.grok, "simple", mock):
        assert await st._hinweis_an_dom("DATEN", "SYSTEM") == du
    assert mock.await_count == 1
    # schlägt der zweite Versuch fehl, gilt der erste
    mock = AsyncMock(side_effect=[dritte, RuntimeError("weg")])
    with patch.object(st.grok, "simple", mock):
        assert await st._hinweis_an_dom("DATEN", "SYSTEM") == dritte


def test_hinweis_nutzt_das_netz():
    quelle = inspect.getsource(st._antwort_verarbeiten)
    assert "await _hinweis_an_dom(" in quelle


# --------------------------------------------------------------------------
# 2 – Ein Wunsch nach Ruhe macht den Vorschlag sanft
# --------------------------------------------------------------------------

def test_ruhe_wunsch_bremst():
    regel = fp.STIMMUNG_INTENSITAET
    assert "Wunsch nach Ruhe" in regel and "heute ruhig und sanft" in regel
    assert "keine Schläge" in regel and "keine strenge Fesselung" in regel
    assert "anspruchsvoller sein" in regel
    basis = dict(erfahrungsstand="etwas", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
                 stimmung="Wunsch: Ruhe")
    for bauer in (fp.tiny_task_vorschlag, fp.ausfuehrlicher_task_vorschlag):
        _, prompt = bauer(**basis)
        assert regel in prompt and "Wunsch: Ruhe" in prompt
        assert "Bei schlechter Stimmung eher sanftere" not in prompt
    quelle = inspect.getsource(inspiration)
    assert "fp.STIMMUNG_INTENSITAET" in quelle and "Bei schlechter Stimmung eher sanftere" not in quelle


# --------------------------------------------------------------------------
# 3 – Kurzlabel: Elemente wie im Text, nur die erste Zeile
# --------------------------------------------------------------------------

async def test_kurzlabel():
    text = "Erst die Augenbinde. Dann legst du die Kerze weit weg vom Kopf ab und tropfst auf die Schultern."
    mock = AsyncMock(return_value="Wachs auf die Schultern mit Augenbinde\n\n**Kern-Handlung:** Wachs-Spiel")
    with patch.object(grok, "simple", mock):
        aus = await labels.kurzlabel(text)
    assert aus == "Wachs auf die Schultern mit Augenbinde", aus
    system = mock.await_args.kwargs["system"]
    assert "Verknüpfe nichts, was er trennt" in system and "Haupthandlung" in system
    # Markdown in der ersten Zeile fällt weg
    with patch.object(grok, "simple", AsyncMock(return_value="**Wachs auf die Schultern**")):
        assert await labels.kurzlabel(text) == "Wachs auf die Schultern"
    # leer → Heuristik (erster Satz)
    with patch.object(grok, "simple", AsyncMock(return_value="\n\n")):
        assert await labels.kurzlabel(text) == "Erst die Augenbinde."


# --------------------------------------------------------------------------
# 4 – Rückblick auf ein früheres Gespräch setzt nach einmal aus
# --------------------------------------------------------------------------

def test_rueckblick_erkennen():
    ja = [
        "Vor drei Tagen war das Tuch dran. Heute das Seil.",
        "Nach dem Kerzen-Ding von vor fünf Tagen darf es heute ruhiger sein.",
        "Neulich hast du ihn warten lassen – heute nicht.",
        "Du hast letztens selbst gesagt, dass dir das gefällt.",
        "Wie lief das letzte Woche eigentlich?",
        "After the blindfold two days ago, keep it simple.",
    ]
    nein = [
        "Leg ihm das Tuch um und zähl bis zehn.",
        "Ihr hattet das Seil schon länger nicht mehr – heute ist es dran.",
        "Drei Tage lang trägt er das Halsband, wenn du heimkommst.",
        "Heute Abend hast du mehr Zeit, also zwei Runden.",
    ]
    for t in ja:
        assert schablonen.hat_rueckblick(t), t
    for t in nein:
        assert not schablonen.hat_rueckblick(t), t
    assert schablonen.rueckblick_setzt_aus([nein[0], ja[0]])          # vorletzter zählt
    assert not schablonen.rueckblick_setzt_aus([nein[0], nein[1], ja[0]])  # drittletzter nicht
    assert not schablonen.rueckblick_setzt_aus([])


def test_auftrag_setzt_rueckblick_aus():
    basis = dict(erfahrungsstand="etwas", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
                 conversation_context="- (23.11.) Er hat sie absichtlich herausgefordert.")
    for bauer in (fp.tiny_task_vorschlag, fp.ausfuehrlicher_task_vorschlag):
        system, prompt = bauer(**basis)
        assert "(wie er zuletzt reagiert hat, Stimmung, was ansteht)" in system
        assert "erwähne sie nicht" not in prompt
        system, prompt = bauer(**basis, rueckblick_nicht_nennen=True)
        assert "heute NICHT ein früheres Gespräch oder Erlebnis" in system and "kein „vor X Tagen …“" in system
        assert "(was ansteht, was ihr länger nicht hattet, Stimmung – heute NICHT ein früheres Gespräch" in system
        assert "Heute höchstens als Hintergrund – erwähne sie nicht" in prompt
        # beides zugleich: beide Verbote, D17-Zusatz bleibt
        system, _ = bauer(**basis, rueckblick_nicht_nennen=True, stimmung_nicht_nennen=True)
        assert "heute NICHT seine Stimmung und nicht ein früheres Gespräch" in system
        assert "nicht wieder als letzten Satz" in system
        # nur Stimmung aus: Wortlaut wie in D17
        system, _ = bauer(**basis, stimmung_nicht_nennen=True)
        assert ("(wie er zuletzt reagiert hat, was ansteht, was ihr länger nicht hattet – heute NICHT seine "
                "Stimmung; und stell die Begründung nicht wieder als letzten Satz hin)") in system


def test_rueckblick_verdrahtet():
    quelle = inspect.getsource(tiny_task)
    assert "rueckblick_nicht_nennen = schablonen.rueckblick_setzt_aus(letzte_tiny_volltexte)" in quelle
    assert "rueckblick_nicht_nennen=rueckblick_nicht_nennen" in quelle
    senden = inspect.getsource(tiny_task._tiny_task_vorschlag_senden)
    # Kandidaten-Wahl vor dem Retry, Retry vor Dislike-/Machbarkeits-Netz
    i_wahl = senden.index("not schablonen.hat_rueckblick(k)")
    i_retry = senden.index("generiere einmal neu.\")")
    i_dislike = senden.index("vorschlag = await _dislike_sichern(")
    i_mach = senden.index("await _machbarkeit_sichern(")
    assert i_wahl < i_retry < i_dislike < i_mach


# --------------------------------------------------------------------------
# 5 – „den Paddle“ → „das Paddle“
# --------------------------------------------------------------------------

def test_paddle_artikel():
    k = sprachpflege.korrigieren
    assert k("Jedes Zögern kostet ihn den Paddle.") == "Jedes Zögern kostet ihn das Paddle."
    assert k("Den Paddle legst du hin, nimm einen Paddle oder deinen Paddle.") == \
        "Das Paddle legst du hin, nimm ein Paddle oder dein Paddle."
    for richtig in ("auf dem Paddle", "das Paddle", "mit den Paddles", "mit den Paddle-Schlägen", "Plug und Seil"):
        assert k(richtig) == richtig, richtig
    assert k(None) is None and k(123) == 123


async def test_paddle_beim_versand():
    gesendet = {}

    class _Bot:
        async def send_message(self, *args, **kwargs):
            gesendet["args"], gesendet["kwargs"] = args, kwargs

        async def copy_message(self, *args, **kwargs):
            pass

        def _unfrozen(self):               # PTB-Bot ist eingefroren; der Wrapper taut ihn kurz auf
            import contextlib
            return contextlib.nullcontext()

    class _App:
        bot = _Bot()

    app = _App()
    routing._wrap_outgoing_logging(app)
    await app.bot.send_message(chat_id="111", text="Nimm den Paddle.")
    assert gesendet["kwargs"]["text"] == "Nimm das Paddle."
    await app.bot.send_message("111", "Nimm den Paddle.", parse_mode="Markdown")
    assert gesendet["args"] == ("111", "Nimm das Paddle.") and gesendet["kwargs"] == {"parse_mode": "Markdown"}
    # Sprachausgabe bekommt denselben Text
    assert "sprachpflege.korrigieren(text)" in inspect.getsource(telegram_helper.voice_an)


def main():
    for fn in (test_netz_dritte_person, test_hinweis_nutzt_das_netz, test_ruhe_wunsch_bremst,
               test_rueckblick_erkennen, test_auftrag_setzt_rueckblick_aus, test_rueckblick_verdrahtet,
               test_paddle_artikel):
        fn()
        print(f"✅ {fn.__name__}")
    for coro in (test_hinweis_wird_neu_geschrieben, test_kurzlabel, test_paddle_beim_versand):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    print("\nAlle Tests zur Log-Durchsicht vom 25.–27.11.2026 grün.")


if __name__ == "__main__":
    main()
