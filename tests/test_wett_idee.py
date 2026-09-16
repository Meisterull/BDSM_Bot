"""
Regressions-Tests Wettvorschlag 🎲 (16.09.2026, handlers/coach_quiz + scheduler):

Live 15.09.2026: Die Dom-Seite hatte „noch nie einen Wettvorschlag bekommen,
nur Quiz" – der Coach-Impuls würfelte Quiz/Quiz/Wette, die Wett-Idee kam als
reine Text-Idee ohne Flow, und Grok lieferte statt 2–4 Sätzen an sie eine
1100-Zeichen-Herrin-Nachricht an den Sub in Anführungszeichen.

  - _idee_verstoesse: Länge, Zitat-Block, Vokativ-Anrede des Subs
  - _wett_idee_generieren: Drift → ein Retry; zweimal Drift → None
  - sende_wett_idee: Vorschlag mit Buttons, Idee im State geparkt
  - callback senden: Herrin-Ansage (Prompt wette_an_sklaven) als Text ohne
    Sprech-Tags + Voice an den Sub, Buttons weg, State leer; Limits-Treffer /
    LLM-Fehler → Hinweis, State bleibt; fremde Kennung → veraltet
  - callback neu: neue Idee mit Buttons, Zähler, Deckel WETT_IDEE_MAX_NEU
  - _impuls_reihenfolge: strikte Abwechslung Quiz/Wette

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_wett_idee.py
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

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

from unittest.mock import AsyncMock  # noqa: E402

from bot import config as _config  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"
_config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.handlers import coach_quiz as cq  # noqa: E402
from bot.scheduler import followup as sched  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.services import persona_config  # noqa: E402
from bot.messages import t  # noqa: E402

DOM = "111"
GUT = "Wer von euch bis Freitag öfter pünktlich ist, gewinnt. Gewinnst du, bringt er dir drei Abende Kaffee ans Bett; gewinnt er, darf er einmal die Filmwahl bestimmen."
DRIFT = ("„Kleine Maus, wir machen eine kleine Wette für die nächsten zwei Tage. Du trägst ab sofort "
         "bis übermorgen Abend durchgehend den Plug, auch bei der Arbeit. Wenn du ihn auch nur eine "
         "Minute rausnimmst, gewinne ich. Dann muss er morgen Abend eine Stunde knien und danach "
         "bekomme ich Kaffee ans Bett. Hält er durch, gewinnt er und darf die Filmwahl bestimmen. "
         "Schick mir jetzt einfach nur ‚Wette angenommen‘ oder ‚Wette abgelehnt‘.“")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self):
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.replies.append((text, reply_markup))


class _Query:
    def __init__(self, data: str):
        self.data = data
        self.message = _Msg()
        self.markup_entfernt = False

    async def answer(self):
        pass

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_entfernt = True


class _Welt:
    def __init__(self, ideen: list, ansage: str = "<soft>Wir wetten.</soft> Nimmst du an?"):
        self.ideen = list(ideen)          # Antworten des Ideen-Generators (der Reihe nach)
        self.ansage = ansage
        self.ansage_fehler = False
        self.retry_calls: list[str] = []
        self.simple_calls: list = []
        self.dom_sends: list[tuple[str, object]] = []
        self.sub_sends: list[tuple[str, str | None]] = []
        self.limits_treffer: list = []

    async def fake_retry(self, p, sklave_hard_limits=None, domina_grenzen=None, system="", **kw):
        self.retry_calls.append(p)
        return self.ideen.pop(0) if self.ideen else None

    async def fake_simple(self, prompt, **kw):
        self.simple_calls.append(prompt)
        if self.ansage_fehler:
            raise RuntimeError("LLM down")
        return self.ansage

    async def send_domina(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.dom_sends.append((text, reply_markup))

    async def send_sklave(self, bot, text, parse_mode=None, reply_markup=None, voice_text=None, **kw):
        self.sub_sends.append((text, voice_text))

    async def verletzungen(self, text, *a, **kw):
        return list(self.limits_treffer)

    def install(self):
        cq.limits_check.generate_mit_limit_retry = self.fake_retry
        cq.limits_check.verletzungen = self.verletzungen
        cq.grok.simple = self.fake_simple
        cq.grok.clean_text = lambda x: (x or "").strip()
        cq.qdrant.get_user_profile = AsyncMock(return_value={
            "vorlieben": ["Kaffee ans Bett"], "hard_limits": [], "interessen": ["Lesen"], "grenzen": []})
        cq.qdrant.get_recent_tiny_tasks = AsyncMock(return_value=([], [], []))
        cq.telegram_helper.send_domina = self.send_domina
        cq.telegram_helper.send_sklave = self.send_sklave
        cq.state.is_paused = lambda *a, **k: False
        persona_config.sklave_anrede = lambda: "Kleine Maus"
        # Währung ⭐: keine laufende Herrin-Wette, Start der Wette nur mitschreiben
        from bot.handlers import waehrung as waehrung_h
        waehrung_h.wette_laeuft = lambda profil: False
        self.wetten: list = []

        async def _start(idee, ansage, kennung):
            self.wetten.append((idee, ansage, kennung))
            return 2
        waehrung_h.wette_starten = _start
        state.set_mode(DOM, "chat")
        for k in cq._WETT_IDEE_KEYS:
            state.get(DOM).pop(k, None)
        return self


def _press(data: str):
    q = _Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(DOM)))
    _run(cq.callback_wett_idee(u, SimpleNamespace(bot=object())))
    return q


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_idee_verstoesse():
    _Welt([]).install()
    assert cq._idee_verstoesse(GUT) == []
    funde = cq._idee_verstoesse(DRIFT)
    assert any("Anführungszeichen" in f for f in funde), funde
    assert any("Kleine Maus" in f for f in funde), funde
    assert any("zu lang" in f for f in cq._idee_verstoesse("x" * 601))
    # Anrede mitten im Satz (kein Vokativ) ist okay
    assert cq._idee_verstoesse("Die Kleine Maus schafft bis Freitag zehn Liegestütze, sonst gewinnst du.") == []
    # Vokativ am Zeilenanfang ohne Anführungszeichen wird trotzdem erkannt
    assert any("Kleine Maus" in f for f in cq._idee_verstoesse("Kleine Maus, du trägst heute den Plug bis 22 Uhr."))


def test_generieren_retry_und_abbruch():
    w = _Welt([DRIFT, GUT]).install()
    assert _run(cq._wett_idee_generieren()) == GUT
    assert len(w.retry_calls) == 2 and "Mängel" in w.retry_calls[1]
    assert "keine Anführungszeichen" in w.retry_calls[1]
    w = _Welt([DRIFT, DRIFT]).install()
    assert _run(cq._wett_idee_generieren()) is None
    w = _Welt([None]).install()
    assert _run(cq._wett_idee_generieren()) is None
    # Prompt-Härtung: Idee an sie, keine fertige Nachricht, Anrede als Negativ-Beispiel
    system, _ = fp.wett_idee(["Kaffee ans Bett"], [], [], [])
    assert "KEINE fertige Nachricht" in system and "Kleine Maus, wir machen" in system
    assert "höchstens 500 Zeichen" in system


def test_sende_wett_idee_mit_buttons():
    w = _Welt([GUT]).install()
    assert _run(cq.sende_wett_idee(None)) is True
    assert len(w.dom_sends) == 1
    text, markup = w.dom_sends[0]
    assert text.startswith("🎲") and "Wettvorschlag" in text and "pünktlich" in text
    assert markup is not None
    s = state.get(DOM)
    assert s["wett_idee_text"] == GUT and s["wett_idee_id"] and s["wett_idee_neu"] == 0
    # Drift ohne Rettung → kein Versand (Scheduler fällt auf Quiz zurück)
    w = _Welt([DRIFT, DRIFT]).install()
    assert _run(cq.sende_wett_idee(None)) is False and w.dom_sends == []


def test_callback_senden():
    w = _Welt([GUT]).install()
    _run(cq.sende_wett_idee(None))
    kennung = state.get(DOM)["wett_idee_id"]
    # LLM-Fehler → Hinweis, Buttons + State bleiben
    w.ansage_fehler = True
    q = _press(f"wettidee:senden:{kennung}")
    assert not q.markup_entfernt and w.sub_sends == [] and state.get(DOM).get("wett_idee_id") == kennung
    assert any("nicht möglich" in r[0] for r in q.message.replies)
    # Limits-Treffer → Hinweis, State bleibt
    w.ansage_fehler = False
    w.limits_treffer = [{"limit": "X", "quelle": "sklave", "matched_via": "X"}]
    q = _press(f"wettidee:senden:{kennung}")
    assert w.sub_sends == [] and state.get(DOM).get("wett_idee_id") == kennung
    assert any("Limits" in r[0] for r in q.message.replies)
    # Erfolg → Herrin-Ansage als Text ohne Tags + Voice mit Tags, Buttons weg, State leer
    w.limits_treffer = []
    q = _press(f"wettidee:senden:{kennung}")
    assert q.markup_entfernt
    assert w.sub_sends == [("Wir wetten. Nimmst du an?", "<soft>Wir wetten.</soft> Nimmst du an?")]
    assert "wett_idee_id" not in state.get(DOM)
    assert any("Ist raus" in r[0] and "50 Punkte" in r[0] for r in q.message.replies)
    assert w.wetten and w.wetten[0][0] == GUT and w.wetten[0][1].startswith("<soft>")
    # Prompt an die Herrin enthält die Idee und die Rollen-Drehung
    system, user = w.simple_calls[-1]
    assert GUT in user and "dritter Person" in system and "annimmt" in system
    assert "50 Punkte obendrauf" in system and "Beigabe" in system
    # Doppel-Tap → veraltet
    q2 = _press(f"wettidee:senden:{kennung}")
    assert len(w.sub_sends) == 1 and any("nicht mehr aktuell" in r[0] for r in q2.message.replies)


def test_callback_neu_mit_deckel():
    w = _Welt([GUT, GUT + " (2)", GUT + " (3)", GUT + " (4)", GUT + " (5)"]).install()
    _run(cq.sende_wett_idee(None))
    k1 = state.get(DOM)["wett_idee_id"]
    q = _press(f"wettidee:neu:{k1}")
    assert q.markup_entfernt and len(w.dom_sends) == 2 and w.dom_sends[1][1] is not None
    assert "(2)" in w.dom_sends[1][0]
    k2 = state.get(DOM)["wett_idee_id"]
    assert k2 != k1 and state.get(DOM)["wett_idee_neu"] == 1
    # alter Button → veraltet, nichts Neues
    q = _press(f"wettidee:neu:{k1}")
    assert len(w.dom_sends) == 2 and any("nicht mehr aktuell" in r[0] for r in q.message.replies)
    _press(f"wettidee:neu:{k2}")
    k3 = state.get(DOM)["wett_idee_id"]
    _press(f"wettidee:neu:{k3}")
    k4 = state.get(DOM)["wett_idee_id"]
    assert state.get(DOM)["wett_idee_neu"] == 3 and len(w.dom_sends) == 4
    q = _press(f"wettidee:neu:{k4}")
    assert len(w.dom_sends) == 4 and any("reichen" in r[0] for r in q.message.replies)
    # nach dem Deckel geht Senden weiterhin
    q = _press(f"wettidee:senden:{k4}")
    assert len(w.sub_sends) == 1 and "wett_idee_id" not in state.get(DOM)


def test_impuls_reihenfolge():
    k = [("coach_quiz", 1), ("wett_idee", 2)]
    assert [n for n, _ in sched._impuls_reihenfolge(k, "coach_quiz")] == ["wett_idee", "coach_quiz"]
    assert [n for n, _ in sched._impuls_reihenfolge(k, "wett_idee")] == ["coach_quiz", "wett_idee"]
    assert sorted(n for n, _ in sched._impuls_reihenfolge(k, "")) == ["coach_quiz", "wett_idee"]


def test_locale_keys():
    for key in ("COACH_IMPULS_WETTE", "BUTTON_WETTIDEE_SENDEN", "BUTTON_WETTIDEE_NEU",
                "COACH_WETTIDEE_GESENDET", "COACH_WETTIDEE_VERALTET", "COACH_WETTIDEE_FEHLER",
                "COACH_WETTIDEE_NEU_LIMIT"):
        assert t(key)
    assert "X" in t("COACH_WETTIDEE_LIMIT", begriffe="X")


def _run_alle():
    test_idee_verstoesse()
    test_generieren_retry_und_abbruch()
    test_sende_wett_idee_mit_buttons()
    test_callback_senden()
    test_callback_neu_mit_deckel()
    test_impuls_reihenfolge()
    test_locale_keys()
    print("✅ Alle Wettvorschlag-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
