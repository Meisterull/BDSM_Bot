"""
Regressions-Tests Wette annehmen / ablehnen + Wettvorschlag auf Abruf
(Bauplan 16.09.2026 abends, handlers/waehrung + handlers/coach_quiz):

  - wette_anbieten: „angeboten" mit ✅/❌, Rollback bei Sendefehler
  - ✅ Annehmen: Frist ab Annahme, Zeile an die Dom-Seite; Doppel-Tap still,
    fremde Kennung → veraltet
  - Job: Erinnerung nach 4 h (einmal, tagsüber), nach 24 h angenommen
  - ❌ Ablehnen: Dom-Seite wählt −25/−50/−100, dann 3 Strafvorschläge
    (1/2/3, 🎲 max. 3, ✍️ eigene, 🚫 nur Punkte); gewählte Strafe → Anordnung
    in ihrer Stimme + Aufgabe + Strafen-Protokoll; Limits-Treffer → Hinweis
  - Job: Ablehnung ohne Wahl → nach 24 h −50 ohne Strafe (kein Doppel-Abzug)
  - /wette der Dom-Seite: offen → Lage, sonst Hintergrund-Generierung;
    wett_vorschlag_auf_abruf stellt zu oder meldet den Fehlschlag
  - Anzeige-Helfer, Prompt-Bausteine, Callback-Rollen in main

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_wette_annahme.py
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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
from bot.services import waehrung as ws  # noqa: E402
from bot.handlers import waehrung as wh  # noqa: E402
from bot.handlers import coach_quiz as cq  # noqa: E402
from bot.prompts import bestrafung, followup as fp  # noqa: E402
from bot.messages import t  # noqa: E402

DOM, SUB = "111", "222"
IDEE = "Er trägt bis Freitag jeden Abend die Schürze. Schafft er es, wählt er den Film; sonst kocht er zwei Abende."
STRAFEN = ["Den ganzen Abend ohne Kissen auf dem Boden sitzen",
           "Morgen früh das Bad putzen, bevor alle wach sind",
           "Eine Woche lang nur Wasser zum Abendessen trinken"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class _Msg:
    message_id = 4711

    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.replies.append(text)


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = _Msg()
        self.toast = "—"
        self.markup_entfernt = False
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text=None, **kw):
        self.toast = text

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_entfernt = True

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))


class _Welt:
    def __init__(self, punkte=200):
        self.profile = {"sklave": {"punkte": punkte, "hard_limits": ["Blut"], "vorlieben": ["Schürze tragen"]},
                        "domina": {"grenzen": [], "aktuelles_level": 2}}
        self.sub_sends: list[tuple[str, object, object]] = []
        self.dom_sends: list[tuple[str, object]] = []
        self.tasks: list[dict] = []
        self.geloescht: list[str] = []
        self.strafen_protokoll: list[dict] = []
        self.hintergrund: list = []
        self.limits: list = []
        self.sub_fehler = False
        self.strafen_json = json.dumps({"strafen": STRAFEN})
        self.llm_prompts: list = []

    async def get_profile(self, uid):
        return dict(self.profile.get(uid, {}))

    async def patch(self, uid, fields, **kw):
        self.profile.setdefault(uid, {}).update(fields)
        return "ok"

    async def send_sklave(self, bot, text, parse_mode=None, reply_markup=None, voice_text=None, **kw):
        if self.sub_fehler:
            raise RuntimeError("Telegram down")
        self.sub_sends.append((text, reply_markup, voice_text))

    async def send_domina(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.dom_sends.append((text, reply_markup))

    async def erstelle_task(self, aufgabe, kategorie, level, **kw):
        self.tasks.append({"aufgabe": aufgabe, "kategorie": kategorie, "level": level, **kw})
        return f"task-{len(self.tasks)}"

    async def loesche_task(self, point_id):
        self.geloescht.append(point_id)

    async def save_strafe(self, data):
        self.strafen_protokoll.append(data)
        return "strafe-1"

    async def verletzungen(self, text, *a, **kw):
        return [{"limit": l} for l in self.limits if l.lower() in (text or "").lower()]

    async def generate(self, prompt, **kw):
        self.llm_prompts.append(prompt)
        return self.strafen_json

    async def simple(self, prompt, **kw):
        return "<soft>Das ist die Quittung.</soft> " + prompt[1].split(": ", 1)[1]

    def install(self):
        q = wh.qdrant
        q.get_user_profile = self.get_profile
        q.patch_profile_fields = self.patch
        q.erstelle_task = self.erstelle_task
        q.loesche_task = self.loesche_task
        q.save_strafe = self.save_strafe
        q.get_strafen = AsyncMock(return_value=[])
        wh.telegram_helper.send_sklave = self.send_sklave
        wh.telegram_helper.send_domina = self.send_domina
        wh.sticker_reaktionen.sende_sklave = AsyncMock(return_value=True)
        wh.limits_check.verletzungen = self.verletzungen
        wh.limits_check.generate_mit_limit_retry = self.generate
        wh.grok.simple = self.simple
        wh.grok.clean_text = lambda x: (x or "").strip()
        wh.nach_punkteaenderung = AsyncMock(return_value=None)
        wh._tagsueber = lambda: True
        wh.im_hintergrund = self.hintergrund.append
        ws.frist_iso = lambda tage: (datetime.now(timezone.utc) + timedelta(days=tage)).isoformat()
        state.is_paused = lambda *a, **k: False
        for cid in (DOM, SUB):
            state.set_mode(cid, "chat")
        return self

    @property
    def hw(self) -> dict:
        return self.profile["sklave"].get(wh.FELD_WETTE) or {}

    @property
    def punkte(self) -> int:
        return self.profile["sklave"]["punkte"]

    def hintergrund_laufen(self):
        while self.hintergrund:
            _run(self.hintergrund.pop(0))


def _press(fn, data, chat):
    q = _Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(chat)))
    _run(fn(u, SimpleNamespace(bot=object())))
    return q


def _angeboten(w, vor_stunden=0.0, kennung="k1"):
    w.profile["sklave"][wh.FELD_WETTE] = {
        "kennung": kennung, "idee": IDEE, "ansage": "Ansage", "einsatz": 50, "status": "angeboten",
        "angeboten_am": (datetime.now(timezone.utc) - timedelta(hours=vor_stunden)).isoformat()}


def _abgelehnt(w, kennung="k1"):
    _angeboten(w, kennung=kennung)
    _press(wh.callback_wetteantwort, f"wetteantwort:ablehnen:{kennung}", SUB)


# --------------------------------------------------------------------------
# Angebot + Annahme
# --------------------------------------------------------------------------

def test_anbieten_und_rollback():
    w = _Welt().install()
    alt = {"kennung": "alt", "status": "entschieden"}
    w.profile["sklave"][wh.FELD_WETTE] = dict(alt)
    _run(wh.wette_anbieten(None, IDEE, "<soft>Wir wetten.</soft> Nimmst du an?", "k1"))
    assert w.hw["status"] == "angeboten" and w.hw["idee"] == IDEE and "frist" not in w.hw
    text, markup, voice = w.sub_sends[0]
    assert text == "Wir wetten. Nimmst du an?" and voice.startswith("<soft>") and markup is not None
    assert wh.wette_offen(w.profile["sklave"]) and not wh.wette_laeuft(w.profile["sklave"])
    # Sendefehler → vorheriger Eintrag zurück, Fehler an den Aufrufer
    w.profile["sklave"][wh.FELD_WETTE] = dict(alt)
    w.sub_fehler = True
    try:
        _run(wh.wette_anbieten(None, IDEE, "Ansage", "k2"))
        raise AssertionError("Sendefehler muss durchschlagen")
    except RuntimeError:
        pass
    assert w.hw == alt


def test_annehmen_per_tipp():
    w = _Welt().install()
    _angeboten(w)
    q = _press(wh.callback_wetteantwort, "wetteantwort:annehmen:k1", SUB)
    assert q.markup_entfernt and w.hw["status"] == "laeuft" and w.hw["angenommen"] == "tipp"
    assert w.hw["frist"] and wh.wette_laeuft(w.profile["sklave"])
    assert any("Angenommen" in s[0] for s in w.sub_sends)
    assert w.dom_sends and "angenommen" in w.dom_sends[-1][0] and "Urteil am" in w.dom_sends[-1][0]
    assert "automatisch" not in w.dom_sends[-1][0]
    # Doppel-Tap → still; fremde Kennung → veraltet
    n = len(w.sub_sends) + len(w.dom_sends)
    q = _press(wh.callback_wetteantwort, "wetteantwort:ablehnen:k1", SUB)
    assert q.message.replies == [] and len(w.sub_sends) + len(w.dom_sends) == n and w.hw["status"] == "laeuft"
    q = _press(wh.callback_wetteantwort, "wetteantwort:annehmen:zzz", SUB)
    assert any("nicht mehr offen" in r for r in q.message.replies)


def test_job_erinnerung_und_auto_annahme():
    w = _Welt().install()
    _angeboten(w, vor_stunden=1)
    _run(wh.wette_urteil_job(None))
    assert w.sub_sends == [] and w.hw["status"] == "angeboten"
    _angeboten(w, vor_stunden=5)
    _run(wh.wette_urteil_job(None))
    assert len(w.sub_sends) == 1 and "wartet" in w.sub_sends[0][0] and w.sub_sends[0][1] is not None
    assert w.hw["erinnert"] is True
    _run(wh.wette_urteil_job(None))
    assert len(w.sub_sends) == 1, "Erinnerung nur einmal"
    # nachts nichts
    w.hw["angeboten_am"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    wh._tagsueber = lambda: False
    _run(wh.wette_urteil_job(None))
    assert w.hw["status"] == "angeboten"
    wh._tagsueber = lambda: True
    _run(wh.wette_urteil_job(None))
    assert w.hw["status"] == "laeuft" and w.hw["angenommen"] == "automatisch"
    assert any("gilt sie als angenommen" in s[0] for s in w.sub_sends)
    assert "automatisch" in w.dom_sends[-1][0]


# --------------------------------------------------------------------------
# Ablehnung: Abzug + Strafwahl
# --------------------------------------------------------------------------

def test_ablehnen_und_abzug():
    w = _Welt(punkte=200).install()
    _abgelehnt(w)
    assert w.hw["status"] == "abgelehnt" and w.hw["abzug"] is None
    assert any("Abgelehnt" in s[0] for s in w.sub_sends)
    text, markup = w.dom_sends[-1]
    assert "abgelehnt" in text and markup is not None
    assert wh.wette_offen(w.profile["sklave"]), "offene Ablehnung blockiert neue Wetten"
    # Strafwahl vor dem Abzug geht nicht
    _press(wh.callback_wetteablehnung, "wetteablehnung:keine:k1", DOM)
    assert w.hw["status"] == "abgelehnt"
    # Abzug −100 (nicht erlaubter Betrag wird ignoriert)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:70:k1", DOM)
    assert w.punkte == 200 and w.hw["abzug"] is None
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:100:k1", DOM)
    assert w.punkte == 100 and w.hw["abzug"] == 100
    assert q.toast == t("WETTE_STRAFE_SUCHT_TOAST") and "Moment" in q.edits[-1][0] and q.edits[-1][1] is None
    assert any("100 Punkte fürs Ablehnen" in s[0] for s in w.sub_sends)
    # Doppel-Tap bucht nicht nochmal
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:50:k1", DOM)
    assert w.punkte == 100
    # Hintergrund: drei Vorschläge, Nachricht umgebaut
    assert len(w.hintergrund) == 1
    w.hintergrund_laufen()
    assert w.hw["strafe_optionen"] == STRAFEN
    text, markup = q.edits[-1]
    assert "1. " + STRAFEN[0] in text and "3. " + STRAFEN[2] in text and markup is not None
    system, user = w.llm_prompts[0]
    assert "DREI" in system and "JSON" in system and IDEE in user and "Schürze tragen" in user


def test_strafe_waehlen_wird_aufgabe():
    w = _Welt().install()
    _abgelehnt(w)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:50:k1", DOM)
    w.hintergrund_laufen()
    n_sub = len(w.sub_sends)
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:strafe:1:k1", DOM)
    assert w.hw["status"] == "abgelehnt_erledigt" and w.hw["strafe"] == STRAFEN[1]
    assert w.tasks and w.tasks[0]["aufgabe"] == STRAFEN[1] and w.tasks[0]["quelle"] == "wette_ablehnung"
    assert w.tasks[0]["level"] == 2 and w.hw["strafe_task_id"] == "task-1"
    text, _, voice = w.sub_sends[n_sub]
    assert "<soft>" not in text and voice.startswith("<soft>") and STRAFEN[1] in text
    assert w.strafen_protokoll and w.strafen_protokoll[0]["grund"] == "wette_abgelehnt"
    assert "Angeordnet" in q.edits[-1][0] and q.edits[-1][1] is None
    assert not wh.wette_offen(w.profile["sklave"])
    # Doppel-Tap nach Abschluss → still, keine zweite Aufgabe
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:strafe:0:k1", DOM)
    assert len(w.tasks) == 1 and q.message.replies == []


def test_strafe_limit_und_zustellfehler():
    w = _Welt().install()
    _abgelehnt(w)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:25:k1", DOM)
    w.hintergrund_laufen()
    w.limits = ["Bad"]
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:strafe:1:k1", DOM)
    assert w.hw["status"] == "abgelehnt" and w.tasks == []
    assert any("Grenzen" in r and "Bad" in r for r in q.message.replies)
    w.limits = []
    w.sub_fehler = True
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:strafe:0:k1", DOM)
    assert w.hw["status"] == "abgelehnt" and w.geloescht == ["task-1"], "Aufgabe zurückgerollt"
    assert any("nicht geklappt" in r for r in q.message.replies)


def test_neu_deckel_und_nur_punkte():
    w = _Welt().install()
    _abgelehnt(w)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:50:k1", DOM)
    w.hintergrund_laufen()
    for i in range(3):
        _press(wh.callback_wetteablehnung, "wetteablehnung:neu:k1", DOM)
        w.hintergrund_laufen()
    assert w.hw["strafe_neu"] == 3 and len(w.llm_prompts) == 4
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:neu:k1", DOM)
    assert len(w.llm_prompts) == 4 and any("reichen" in r for r in q.message.replies)
    # leere Vorschläge → Hinweis statt Liste, Knöpfe ohne Nummern
    w.strafen_json = "kaputt"
    w.hw["strafe_neu"] = 0
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:neu:k1", DOM)
    w.hintergrund_laufen()
    assert w.hw["strafe_optionen"] == [] and "nichts Passendes" in q.edits[-1][0]
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:keine:k1", DOM)
    assert w.hw["status"] == "abgelehnt_erledigt" and w.hw["strafe"] is None and w.tasks == []
    assert "Nur die Punkte" in q.edits[-1][0] and w.punkte == 150


def test_eigene_strafe():
    w = _Welt().install()
    _abgelehnt(w)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:50:k1", DOM)
    w.hintergrund_laufen()
    q = _press(wh.callback_wetteablehnung, "wetteablehnung:eigene:k1", DOM)
    assert state.get_mode(DOM) == wh.MODE_STRAFE_EIGEN and state.get(DOM)["wette_strafe_kennung"] == "k1"
    assert any("Schreib mir die Strafe" in r for r in q.message.replies)
    bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(return_value=None))

    def _nachricht(text):
        msg = _Msg()
        msg.text = text
        _run(wh.handle_strafe_eigen(SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=int(DOM))),
                                    SimpleNamespace(bot=bot)))
        return msg
    # Limits-Treffer → Hinweis, Modus bleibt für den nächsten Versuch
    w.limits = ["Blut"]
    msg = _nachricht("Etwas mit Blut")
    assert any("Grenzen" in r for r in msg.replies) and state.get_mode(DOM) == wh.MODE_STRAFE_EIGEN
    w.limits = []
    msg = _nachricht("Zehn Minuten still in der Ecke stehen")
    assert state.get_mode(DOM) == "chat" and "wette_strafe_kennung" not in state.get(DOM)
    assert w.tasks[0]["aufgabe"] == "Zehn Minuten still in der Ecke stehen"
    assert w.hw["status"] == "abgelehnt_erledigt" and any("Angeordnet" in r for r in msg.replies)
    bot.edit_message_reply_markup.assert_awaited()
    # veralteter Modus (Ablehnung schon erledigt) → Hinweis, nichts angelegt
    state.set_mode(DOM, wh.MODE_STRAFE_EIGEN)
    state.get(DOM)["wette_strafe_kennung"] = "k1"
    msg = _nachricht("Noch eine")
    assert len(w.tasks) == 1 and any("schon erledigt" in r for r in msg.replies)
    assert "wette_strafe_kennung" in state.FLOW_STATE_KEYS


def test_ablehnung_automatisch_nach_24h():
    w = _Welt(punkte=200).install()
    _abgelehnt(w)
    _run(wh.wette_urteil_job(None))
    assert w.hw["status"] == "abgelehnt"
    w.hw["letzte_aktion_am"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    n_dom = len(w.dom_sends)
    _run(wh.wette_urteil_job(None))
    assert w.hw["status"] == "abgelehnt_erledigt" and w.hw["automatisch"] and w.punkte == 150
    assert any("50 Punkte fürs Ablehnen der Wette" in s[0] for s in w.sub_sends)
    assert len(w.dom_sends) == n_dom, "keine Extra-Nachricht an die Dom-Seite"
    # schon gewählter Abzug → kein zweiter
    w = _Welt(punkte=200).install()
    _abgelehnt(w)
    _press(wh.callback_wetteablehnung, "wetteablehnung:punkte:25:k1", DOM)
    w.hintergrund.pop().close()  # Strafvorschläge hier egal
    w.hw["letzte_aktion_am"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    _run(wh.wette_urteil_job(None))
    assert w.punkte == 175 and w.hw["status"] == "abgelehnt_erledigt" and w.hw["strafe"] is None


# --------------------------------------------------------------------------
# /wette der Dom-Seite + Anzeige + Verdrahtung
# --------------------------------------------------------------------------

def test_wette_abruf():
    w = _Welt().install()
    cq.qdrant.get_user_profile = w.get_profile
    cq.telegram_helper.send_domina = w.send_domina
    zugestellt: list = []

    async def fake_generieren(schwerpunkt=""):
        return w.idee

    async def fake_zustellen(bot, idee, neu, schwerpunkt=""):
        zugestellt.append((idee, schwerpunkt))
        return "k9"
    alt = (cq._wett_idee_generieren, cq._wett_idee_zustellen, cq.state.is_paused)
    cq._wett_idee_generieren, cq._wett_idee_zustellen = fake_generieren, fake_zustellen
    try:
        msg = _Msg()
        upd = SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=int(DOM)))
        # offene Wette → Lage statt Generierung
        _angeboten(w)
        _run(cq.wette_router(upd, SimpleNamespace(bot=object(), args=[])))
        assert any("noch eine Wette offen" in r and "Antwort" in r for r in msg.replies) and not w.hintergrund
        # frei → „Moment" mit Thema, Generierung im Hintergrund
        w.profile["sklave"].pop(wh.FELD_WETTE)
        w.idee = IDEE
        _run(cq.wette_router(upd, SimpleNamespace(bot=object(), args=["Film", "abend"])))
        assert "Film abend" in msg.replies[-1] and len(w.hintergrund) == 1
        w.hintergrund_laufen()
        assert zugestellt == [(IDEE, "Film abend")]
        # nichts Brauchbares → Meldung an die Dom-Seite
        w.idee = None
        assert _run(cq.wett_vorschlag_auf_abruf(None, "")) is False
        assert "nichts Brauchbares" in w.dom_sends[-1][0]
    finally:
        cq._wett_idee_generieren, cq._wett_idee_zustellen, cq.state.is_paused = alt


def test_verlauf_wird_geschrieben():
    """Jede endgültige Wette landet im Verlauf (max. 5); ein Einspruch korrigiert
    den vorhandenen Eintrag, statt einen zweiten anzulegen."""
    w = _Welt().install()
    _abgelehnt(w)
    verlauf = w.profile["sklave"][wh.FELD_WETT_VERLAUF]
    assert len(verlauf) == 1 and verlauf[0]["ergebnis"] == "abgelehnt" and verlauf[0]["kennung"] == "k1"
    assert IDEE[:50] in verlauf[0]["idee"] and verlauf[0]["datum"].count("-") == 2
    # Entschiedene Wette: neuer Eintrag; Einspruch korrigiert ihn
    w.profile["sklave"][wh.FELD_WETTE] = {"kennung": "k2", "idee": "Zweite Wette: er kocht.",
                                          "einsatz": 50, "status": "gefragt"}
    _run(wh._wette_abschliessen(None, "verloren"))
    verlauf = w.profile["sklave"][wh.FELD_WETT_VERLAUF]
    assert [e["ergebnis"] for e in verlauf] == ["abgelehnt", "verloren"]
    _press(wh.callback_wetteeinspruch, "wetteeinspruch:k2", DOM)
    verlauf = w.profile["sklave"][wh.FELD_WETT_VERLAUF]
    assert len(verlauf) == 2 and verlauf[-1]["ergebnis"] == "gewonnen", "Einspruch korrigiert"
    # Deckel bei 5
    for i in range(5):
        w.profile["sklave"][wh.FELD_WETTE] = {"kennung": f"x{i}", "idee": f"Wette {i}: er wischt.",
                                              "einsatz": 50, "status": "gefragt"}
        _run(wh._wette_abschliessen(None, "verloren"))
    verlauf = w.profile["sklave"][wh.FELD_WETT_VERLAUF]
    assert len(verlauf) == 5 and [e["kennung"] for e in verlauf] == ["x0", "x1", "x2", "x3", "x4"]
    assert wh.letzte_wetten({}) == [] and wh.letzte_wetten(w.profile["sklave"]) == verlauf
    # Verfallene Wette wird als solche vermerkt
    w.profile["sklave"][wh.FELD_WETTE] = {"kennung": "auto", "idee": "Letzte Wette: er schweigt.",
                                          "einsatz": 50, "status": "gefragt"}
    _run(wh._wette_abschliessen(None, "verloren", auto=True))
    assert w.profile["sklave"][wh.FELD_WETT_VERLAUF][-1]["ergebnis"] == "verfallen"


def test_lage_und_anzeige():
    assert wh.lage_text({}) == ""
    assert "Antwort" in wh.lage_text({wh.FELD_WETTE: {"status": "angeboten"}})
    assert "Urteil am" in wh.lage_text({wh.FELD_WETTE: {"status": "laeuft", "frist": "2026-09-18T15:30:00+00:00"}})
    assert "Strafe" in wh.lage_text({wh.FELD_WETTE: {"status": "abgelehnt"}})
    for status, offen in (("angeboten", True), ("laeuft", True), ("gefragt", True), ("abgelehnt", True),
                          ("abgelehnt_erledigt", False), ("entschieden", False), ("gekippt", False)):
        assert wh.wette_offen({wh.FELD_WETTE: {"status": status}}) is offen, status
    assert not wh.wette_laeuft({wh.FELD_WETTE: {"status": "angeboten"}})


def test_prompts_und_verdrahtung():
    system, user = fp.strafe_fuer_ablehnung("Zehn Minuten in der Ecke stehen")
    assert "abgelehnt" in system and "EXAKT" in system and "Zehn Minuten in der Ecke stehen" in user
    system, user = bestrafung.ablehnungs_strafen(IDEE, ["Blut"], ["Schürze tragen"], {}, ["alte Strafe"], "")
    assert "160 Zeichen" in system and "Blut" in user and "alte Strafe" in user
    assert "GENAU EINE Handlung" in system and "keine Belohnung" in system and "Verzicht" in system
    system, _ = fp.wette_an_sklaven(IDEE, einsatz=50)
    assert "ablehnen darf" in system and "kostet" in system
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_src = open(os.path.join(root, "bot", "main.py"), encoding="utf-8").read()
    assert '("wetteantwort:",       paare.ROLLE_SUB)' in main_src
    assert '("wetteablehnung:",     paare.ROLLE_DOM)' in main_src
    assert "coach_quiz.wette_router" in main_src and "waehrung_h.MODE_STRAFE_EIGEN" in main_src
    from bot import commands_katalog as ck
    from bot.locales import commands_en as en
    dom = {e.command: e for _, grp in ck.DOMINA_GRUPPEN for e in grp}
    assert "wette" in dom and dom["wette"].beschreibung_key in en.BESCHREIBUNGEN
    assert "wette" in ck._DOMINA_MENUE_REIHENFOLGE


def _run_alle():
    test_anbieten_und_rollback()
    test_annehmen_per_tipp()
    test_job_erinnerung_und_auto_annahme()
    test_ablehnen_und_abzug()
    test_strafe_waehlen_wird_aufgabe()
    test_strafe_limit_und_zustellfehler()
    test_neu_deckel_und_nur_punkte()
    test_eigene_strafe()
    test_ablehnung_automatisch_nach_24h()
    test_wette_abruf()
    test_verlauf_wird_geschrieben()
    test_lage_und_anzeige()
    test_prompts_und_verdrahtung()
    print("✅ Alle Wett-Annahme-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
