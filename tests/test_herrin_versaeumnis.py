"""
Regressions-Tests Versäumnis der Herrin ⏳ (15.09.2026, handlers/herrin_versaeumnis):

  - braucht_herrin: Kategorie-Kurzschluss, Grok JA/NEIN, LLM-Ausfall → JA
  - Abzweig in followup_response._handle_no: Rückfrage statt Malus; Gate aus /
    Klassifikation NEIN → Malus-Kette wie bisher
  - Sub-Buttons: „lag an mir" → Malus-Kette; „keine Zeit" → Versäumnis
    (kein nicht_erledigt, kein Streak-Reset, Task offen + Nachfrage in 3 Tagen,
    Herrin-Satz an den Sub, Coach-Einschätzung MIT Buttons an die Dom-Seite,
    Profil-Liste); Doppel-Tap / erledigter Task löst nichts aus
  - 3. Versäumnis → verfallen_herrin, keine Buttons, Kettenfrage bei Kettenglied
  - Freitext-Klassifikation und Text-Pfad
  - Dom-Buttons: nachholen (bleibt offen), streichen (verfallen + Sub-Satz),
    veraltet / inzwischen erledigt → nichts umschalten
  - Coach-Ruhe/Zuschauer: Einschätzung geht trotzdem raus
  - Zählerzeile /aufgaben + Prompt-Fakten (Fenster, verfallen-Markierung)

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_herrin_versaeumnis.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
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

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from bot import config as _config  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"
_config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
_config.HERRIN_VERSAEUMNIS = True
_config.HERRIN_NACHFRAGE_TAGE = 3
_config.HERRIN_MAX_VERSAEUMNISSE = 3
_config.HERRIN_VERSAEUMNIS_FENSTER_TAGE = 90

from bot import state  # noqa: E402
from bot.handlers import herrin_versaeumnis as hv  # noqa: E402
from bot.handlers import followup_response as fr  # noqa: E402
from bot.messages import t  # noqa: E402

SUB = "222"
JETZT = datetime.now(timezone.utc)


def _iso(delta_tage: float) -> str:
    return (JETZT - timedelta(days=delta_tage)).isoformat()


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class _Welt:
    """Qdrant-/Grok-/Telegram-Stub für einen Testfall."""

    def __init__(self, task: dict | None, profil: dict | None = None, grok_antwort="JA"):
        self.tasks = {task["qdrant_point_id"]: dict(task)} if task else {}
        self.profil = dict(profil or {})
        self.patches: list[dict] = []
        self.grok_calls: list = []
        self.grok_antwort = grok_antwort
        self.grok_fehler = False
        self.dom_sends: list = []
        self.sub_sends: list = []
        self.kette_fragen: list = []
        self.malus: list = []

    async def get_task(self, tid):
        tk = self.tasks.get(tid)
        return dict(tk) if tk else None

    async def update_task(self, tid, fields):
        self.tasks.setdefault(tid, {}).update(fields)

    async def get_user_profile(self, uid):
        return dict(self.profil)

    async def patch_profile_fields(self, uid, fields, **kw):
        self.patches.append(fields)
        self.profil.update(fields)

    async def grok_simple(self, prompt, **kw):
        self.grok_calls.append((prompt, kw))
        if self.grok_fehler:
            raise RuntimeError("LLM down")
        if isinstance(self.grok_antwort, list):
            return self.grok_antwort.pop(0)
        return self.grok_antwort

    async def send_domina(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.dom_sends.append((text, reply_markup))

    async def send_sklave(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.sub_sends.append(text)

    async def kette_frage(self, bot, task):
        self.kette_fragen.append(task)
        return True

    async def malus_kette(self, message, context, chat_id, task_id, aufgabe):
        self.malus.append(task_id)

    def install(self):
        hv.qdrant.get_task = self.get_task
        hv.qdrant.update_task = self.update_task
        hv.qdrant.get_user_profile = self.get_user_profile
        hv.qdrant.patch_profile_fields = self.patch_profile_fields
        hv.qdrant.followup_zeitpunkt_utc = lambda tage=1: (JETZT + timedelta(days=tage)).isoformat()
        hv.grok.simple = self.grok_simple
        hv.telegram_helper.send_domina = self.send_domina
        hv.telegram_helper.send_sklave = self.send_sklave
        fr.qdrant.get_task = self.get_task
        fr.qdrant.update_task = self.update_task
        fr.malus_kette = self.malus_kette
        import bot.handlers.kette_adaptiv as ka
        ka.frage_bei_fehlschlag = self.kette_frage
        return self


class _Query:
    def __init__(self, data, chat_id=SUB):
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()
        self.message = SimpleNamespace(reply_text=AsyncMock(), chat_id=chat_id)


def _cb(data, chat_id=SUB):
    return SimpleNamespace(callback_query=_Query(data, chat_id),
                           effective_chat=SimpleNamespace(id=int(chat_id)))


def _msg(text, chat_id=SUB):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=int(chat_id)),
                           message=SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=chat_id))


def _ctx():
    return SimpleNamespace(bot=MagicMock())


def _task(**extra):
    base = {"qdrant_point_id": "t1", "aufgabe": "Heute Abend Strap-on-Session im Schlafzimmer",
            "kategorie": "Psycho", "status": "gefragt", "erteilt_am": _iso(1)}
    base.update(extra)
    return base


def _reset():
    state.set_coach_ruhe(None)
    state.set_mode(SUB, "chat")
    state.get(SUB).pop("herrin_frage_task_id", None)
    state.get(SUB)["followup_task_id"] = None


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# braucht_herrin
# --------------------------------------------------------------------------

def test_braucht_herrin():
    w = _Welt(_task()).install()
    # Kategorie-Kurzschluss: kein LLM-Call
    assert _run(hv.braucht_herrin(_task(kategorie="Strap_on"))) is True
    assert w.grok_calls == []
    # LLM JA / NEIN (temperature 0)
    w.grok_antwort = "JA"
    assert _run(hv.braucht_herrin(_task())) is True
    assert w.grok_calls[-1][1].get("temperature") == 0
    w.grok_antwort = "Nein."
    assert _run(hv.braucht_herrin(_task())) is False
    # leere Aufgabe → NEIN ohne LLM
    n = len(w.grok_calls)
    assert _run(hv.braucht_herrin(_task(aufgabe=""))) is False and len(w.grok_calls) == n
    # LLM-Ausfall → im Zweifel nachfragen
    w.grok_fehler = True
    assert _run(hv.braucht_herrin(_task())) is True


# --------------------------------------------------------------------------
# Abzweig in _handle_no
# --------------------------------------------------------------------------

def test_handle_no_abzweig():
    _reset()
    w = _Welt(_task(), grok_antwort="JA").install()
    msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=SUB)
    _run(fr._handle_no(msg, _ctx(), SUB, "t1", "x", _task()))
    assert w.malus == [], "bei Herrin-Beteiligung darf die Malus-Kette nicht laufen"
    assert msg.reply_text.await_count == 1
    assert msg.reply_text.await_args.args[0] == t("HERRIN_FRAGE")
    assert msg.reply_text.await_args.kwargs.get("reply_markup") is not None
    assert w.tasks["t1"]["herrin_frage_offen"] is True
    assert w.tasks["t1"]["status"] == "gefragt", "Task bleibt gefragt (Recovery-gedeckt)"
    assert state.get_mode(SUB) == hv.MODE
    assert state.get(SUB)["herrin_frage_task_id"] == "t1"

    # Klassifikation NEIN → Malus wie bisher
    _reset()
    w = _Welt(_task(), grok_antwort="NEIN").install()
    msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=SUB)
    _run(fr._handle_no(msg, _ctx(), SUB, "t1", "x", _task()))
    assert w.malus == ["t1"] and msg.reply_text.await_count == 0

    # Gate aus → Malus ohne Klassifikation
    _reset()
    _config.HERRIN_VERSAEUMNIS = False
    try:
        w = _Welt(_task(kategorie="Strap_on")).install()
        msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=SUB)
        _run(fr._handle_no(msg, _ctx(), SUB, "t1", "x", _task(kategorie="Strap_on")))
        assert w.malus == ["t1"] and w.grok_calls == []
    finally:
        _config.HERRIN_VERSAEUMNIS = True
    # Ohne mitgegebenen Task holt der Abzweig ihn selbst
    _reset()
    w = _Welt(_task(kategorie="Strap_on")).install()
    msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=SUB)
    _run(fr._handle_no(msg, _ctx(), SUB, "t1", "x"))
    assert w.malus == [] and w.tasks["t1"]["herrin_frage_offen"] is True


# --------------------------------------------------------------------------
# Sub-Buttons
# --------------------------------------------------------------------------

def test_button_lag_an_mir():
    _reset()
    w = _Welt(_task(herrin_frage_offen=True)).install()
    state.set_mode(SUB, hv.MODE)
    state.get(SUB)["herrin_frage_task_id"] = "t1"
    u = _cb("herrin:ich:t1")
    _run(hv.callback(u, _ctx()))
    assert w.malus == ["t1"]
    assert w.tasks["t1"]["herrin_frage_offen"] is False
    assert state.get_mode(SUB) == "chat" and "herrin_frage_task_id" not in state.get(SUB)
    assert u.callback_query.edit_message_reply_markup.await_count == 1
    # Doppel-Tap: Marker weg → nichts mehr
    u2 = _cb("herrin:vergessen:t1")
    _run(hv.callback(u2, _ctx()))
    assert w.malus == ["t1"] and w.dom_sends == []
    assert u2.callback_query.message.reply_text.await_args.args[0] == t("MEINEAUFGABEN_NICHT_OFFEN")


def test_button_herrin_keine_zeit():
    _reset()
    w = _Welt(_task(herrin_frage_offen=True),
              grok_antwort=["Das geht nicht auf deine Kappe, die Aufgabe bleibt offen und ich hole sie nach.",
                            "Die Strap-on-Session ist an dir hängen geblieben – er büßt nicht dafür. Unten kannst du entscheiden."]).install()
    state.set_mode(SUB, hv.MODE)
    state.get(SUB)["herrin_frage_task_id"] = "t1"
    u = _cb("herrin:vergessen:t1")
    _run(hv.callback(u, _ctx()))
    tk = w.tasks["t1"]
    assert w.malus == [], "kein Malus"
    assert tk["status"] == "offen" and tk["herrin_versaeumt"] == 1
    assert tk["herrin_frage_offen"] is False and tk["herrin_entscheidung_offen"] is True
    assert len(tk["herrin_versaeumt_am"]) == 1
    naechste = datetime.fromisoformat(tk["follow_up_datum"])
    assert timedelta(days=2, hours=23) < naechste - JETZT < timedelta(days=3, hours=1)
    assert "nicht_erledigt" not in str(tk)
    # Sub-Satz (Herrin) + Dom-Einschätzung mit Buttons
    assert u.callback_query.message.reply_text.await_count == 1
    assert "Kappe" in u.callback_query.message.reply_text.await_args.args[0]
    assert len(w.dom_sends) == 1 and w.dom_sends[0][1] is not None
    # Profil-Liste
    liste = w.profil["herrin_versaeumnisse"]
    assert len(liste) == 1 and liste[0]["task_id"] == "t1" and liste[0]["verfallen"] is False
    assert state.get_mode(SUB) == "chat"
    # Coach-Prompt trägt Stufe 1 (erstes Mal) und die Anzahl
    coach_prompt = w.grok_calls[-1][0]
    system, user = coach_prompt
    assert "Erstes Mal" in system and "inkl. diesem): 1" in user
    # Keine Kettenfrage beim bloßen Versäumnis
    assert w.kette_fragen == []


def test_button_ohne_marker_oder_erledigt():
    _reset()
    # erledigter Task mit altem Button
    w = _Welt(_task(status="erledigt", herrin_frage_offen=True)).install()
    u = _cb("herrin:vergessen:t1")
    _run(hv.callback(u, _ctx()))
    assert w.dom_sends == [] and w.malus == []
    assert u.callback_query.message.reply_text.await_args.args[0] == t("MEINEAUFGABEN_NICHT_OFFEN")
    # unbekannter Task
    w = _Welt(None).install()
    u = _cb("herrin:ich:t9")
    _run(hv.callback(u, _ctx()))
    assert u.callback_query.message.reply_text.await_args.args[0] == t("COMMON_TASK_NICHT_GEFUNDEN")


def test_drittes_versaeumnis_verfaellt():
    _reset()
    w = _Welt(_task(herrin_frage_offen=True, herrin_versaeumt=2,
                    herrin_versaeumt_am=[_iso(10), _iso(5)], kette_id="k1", kette_position=1),
              profil={"herrin_versaeumnisses": []},
              grok_antwort=["Vom Tisch – das geht nicht auf deine Kappe, ganz ruhig.",
                            "Das war das dritte Mal, damit ist die Aufgabe vom Tisch. Er büßt nicht."]).install()
    w.profil["herrin_versaeumnisse"] = [
        {"am": _iso(10), "task_id": "t1", "aufgabe": "x", "verfallen": False},
        {"am": _iso(5), "task_id": "t1", "aufgabe": "x", "verfallen": False},
    ]
    u = _cb("herrin:vergessen:t1")
    _run(hv.callback(u, _ctx()))
    tk = w.tasks["t1"]
    assert tk["status"] == hv.STATUS_VERFALLEN and tk["herrin_versaeumt"] == 3
    assert tk["herrin_entscheidung_offen"] is False and tk.get("verfallen_am")
    assert len(w.dom_sends) == 1 and w.dom_sends[0][1] is None, "beim Verfall keine Buttons"
    system, user = w.grok_calls[-1][0]
    assert "3. Mal" in system and "vom Tisch" in system
    assert w.profil["herrin_versaeumnisse"][-1]["verfallen"] is True
    assert len(w.kette_fragen) == 1 and w.kette_fragen[0]["status"] == hv.STATUS_VERFALLEN


def test_llm_ausfall_faellt_auf_fallbacks():
    _reset()
    w = _Welt(_task(herrin_frage_offen=True)).install()
    w.grok_fehler = True
    u = _cb("herrin:vergessen:t1")
    _run(hv.callback(u, _ctx()))
    assert u.callback_query.message.reply_text.await_args.args[0] == t("FALLBACK_HERRIN_VERSAEUMT")
    text, markup = w.dom_sends[0]
    assert "hängen geblieben" in text and markup is not None
    assert w.tasks["t1"]["status"] == "offen"


def test_coach_ruhe_blockiert_nicht():
    _reset()
    w = _Welt(_task(herrin_frage_offen=True)).install()
    state.set_coach_ruhe("zuschauer")
    u = _cb("herrin:vergessen:t1")
    _run(hv.callback(u, _ctx()))
    assert len(w.dom_sends) == 1 and w.dom_sends[0][1] is not None
    state.set_coach_ruhe(None)


# --------------------------------------------------------------------------
# Freitext
# --------------------------------------------------------------------------

def test_klassifiziere_text_und_textpfad():
    k = hv.klassifiziere_text
    assert k("lag an mir") == "ich"
    assert k("Ich war einfach zu müde") == "ich"
    assert k("meine Schuld") == "ich"
    assert k("du hattest keine Zeit") == "vergessen"
    assert k("ich glaube, du hattest keine Zeit") == "vergessen"
    assert k("sie hat es vergessen") == "vergessen"
    assert k("you forgot") == "vergessen"
    assert k("hm") is None

    _reset()
    w = _Welt(_task(herrin_frage_offen=True)).install()
    state.set_mode(SUB, hv.MODE)
    state.get(SUB)["herrin_frage_task_id"] = "t1"
    u = _msg("keine Ahnung")
    _run(hv.handle(u, _ctx()))
    assert u.message.reply_text.await_args.args[0] == t("HERRIN_FRAGE_KLARSTELLUNG")
    assert state.get_mode(SUB) == hv.MODE
    u = _msg("lag an mir")
    _run(hv.handle(u, _ctx()))
    assert w.malus == ["t1"] and state.get_mode(SUB) == "chat"
    # Ohne Task-Id im State → Mode sauber beenden
    _reset()
    state.set_mode(SUB, hv.MODE)
    u = _msg("egal")
    _run(hv.handle(u, _ctx()))
    assert state.get_mode(SUB) == "chat"


# --------------------------------------------------------------------------
# Dom-Buttons
# --------------------------------------------------------------------------

def test_domina_nachholen_und_streichen():
    _reset()
    w = _Welt(_task(status="offen", herrin_entscheidung_offen=True, herrin_versaeumt=1),
              grok_antwort="Vom Tisch – nicht deine Kappe, ich hab's entschieden.").install()
    w.profil["herrin_versaeumnisse"] = [{"am": _iso(0), "task_id": "t1", "aufgabe": "x", "verfallen": False}]
    u = _cb("herrinfehl:nachholen:t1", chat_id="111")
    _run(hv.callback_domina(u, _ctx()))
    assert w.tasks["t1"]["status"] == "offen" and w.tasks["t1"]["herrin_entscheidung_offen"] is False
    assert "bleibt offen" in u.callback_query.message.reply_text.await_args.args[0]
    # zweiter Tap → veraltet
    u = _cb("herrinfehl:streichen:t1", chat_id="111")
    _run(hv.callback_domina(u, _ctx()))
    assert w.tasks["t1"]["status"] == "offen"
    assert u.callback_query.message.reply_text.await_args.args[0] == t("HERRIN_ENTSCHEIDUNG_VERALTET")

    # streichen
    w = _Welt(_task(status="offen", herrin_entscheidung_offen=True, herrin_versaeumt=1, kette_id="k1"),
              grok_antwort="Vom Tisch – nicht deine Kappe, ich hab's entschieden.").install()
    w.profil["herrin_versaeumnisse"] = [{"am": _iso(0), "task_id": "t1", "aufgabe": "x", "verfallen": False}]
    u = _cb("herrinfehl:streichen:t1", chat_id="111")
    _run(hv.callback_domina(u, _ctx()))
    tk = w.tasks["t1"]
    assert tk["status"] == hv.STATUS_VERFALLEN and tk["herrin_entscheidung_offen"] is False
    assert len(w.sub_sends) == 1 and "Kappe" in w.sub_sends[0]
    assert w.profil["herrin_versaeumnisse"][0]["verfallen"] is True
    assert len(w.kette_fragen) == 1

    # Sub hat sie inzwischen erledigt → Streichen wirkt nicht mehr
    w = _Welt(_task(status="erledigt", herrin_entscheidung_offen=True)).install()
    u = _cb("herrinfehl:streichen:t1", chat_id="111")
    _run(hv.callback_domina(u, _ctx()))
    assert w.tasks["t1"]["status"] == "erledigt" and w.tasks["t1"]["herrin_entscheidung_offen"] is False
    assert w.sub_sends == []
    assert u.callback_query.message.reply_text.await_args.args[0] == t("HERRIN_ENTSCHEIDUNG_VERALTET")


# --------------------------------------------------------------------------
# Sichtbarkeit
# --------------------------------------------------------------------------

def test_zaehler_und_fakten():
    w = _Welt(None).install()
    assert _run(hv.zaehler_zeile()) == ""
    assert _run(hv.prompt_fakten(14)) == ""
    w.profil["herrin_versaeumnisse"] = [
        {"am": _iso(100), "task_id": "alt", "aufgabe": "Uralt", "verfallen": True},   # außerhalb 90 Tage
        {"am": _iso(20), "task_id": "t1", "aufgabe": "Session A", "verfallen": False},
        {"am": _iso(2), "task_id": "t2", "aufgabe": "Session B", "verfallen": True},
    ]
    zeile = _run(hv.zaehler_zeile())
    assert "*2*" in zeile and "90" in zeile and "1 verfallen" in zeile
    fakten = _run(hv.prompt_fakten(14))
    assert "Session B" in fakten and "Session A" not in fakten and "verfallen" in fakten
    assert "Uralt" not in _run(hv.prompt_fakten(90))
    # Gate aus → nichts anzeigen
    _config.HERRIN_VERSAEUMNIS = False
    try:
        assert _run(hv.zaehler_zeile()) == "" and _run(hv.prompt_fakten(90)) == ""
    finally:
        _config.HERRIN_VERSAEUMNIS = True


def _run_alle():
    test_braucht_herrin()
    test_handle_no_abzweig()
    test_button_lag_an_mir()
    test_button_herrin_keine_zeit()
    test_button_ohne_marker_oder_erledigt()
    test_drittes_versaeumnis_verfaellt()
    test_llm_ausfall_faellt_auf_fallbacks()
    test_coach_ruhe_blockiert_nicht()
    test_klassifiziere_text_und_textpfad()
    test_domina_nachholen_und_streichen()
    test_zaehler_und_fakten()
    print("✅ Alle Versäumnis-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
