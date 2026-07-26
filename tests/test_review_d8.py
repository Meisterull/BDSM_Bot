"""
Regressions-Tests für Review Durchgang 8 (2026-07-26), HOCH-Fixes:

  H1 – Ketten-Glieder 2..n laufen durch den Hard-Limits-Check
  H2 – grok._post_chat wirft bei leerer LLM-Antwort (statt "" zurückzugeben)
  H3 – _process_kette_tasks: hängende Ketten beobachten + Domina fragen
  H4 – /loeschen-Statusliste ohne tote Werte, Serie-Stopp-Key im Flow-State
  H5 – reaktion.py flippt den Task-Status nicht mehr (bleibt nicht_erledigt)
  H6 – privileg_effekte.cleanup erstattet unentschiedene Einlösungen

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_review_d8.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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

from bot import state as _state  # noqa: E402
from bot.services import grok as _grok  # noqa: E402
from bot.services import limits_check as _lc  # noqa: E402
from bot.services import telegram_helper as _th  # noqa: E402
from bot.services import privileg_effekte as _pe  # noqa: E402
from bot.handlers import domina as _domina  # noqa: E402
from bot.handlers import reaktion as _reaktion  # noqa: E402
from bot.handlers import aufgaben as _aufgaben  # noqa: E402
from bot.scheduler import followup as _sched  # noqa: E402


def _aw(fn):
    async def _w(*a, **k):
        return fn(*a, **k)
    return _w


# --------------------------------------------------------------------------
# H2 – leere LLM-Antwort ist ein Fehler (Retry/Fallback greifen), kein ""
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


async def test_post_chat_leere_antwort_raist():
    orig = _grok._client
    _grok._client = MagicMock()
    try:
        for leer in (None, "", "   "):
            _grok._client.post = AsyncMock(return_value=_FakeResp(leer))
            try:
                await _grok._post_chat("http://x", {}, {})
                raise AssertionError(f"Regression H2: leerer Content {leer!r} kam als Rückgabe durch")
            except ValueError:
                pass
        _grok._client.post = AsyncMock(return_value=_FakeResp("echte Antwort"))
        assert await _grok._post_chat("http://x", {}, {}) == "echte Antwort"
    finally:
        _grok._client = orig


# --------------------------------------------------------------------------
# H1 – Ketten-Glieder 2..n gehen durchs Sicherheits-Gate
# --------------------------------------------------------------------------

async def test_kette_glied_limits_check():
    chat_id = _config.DOMINA_CHAT_ID
    _state._state.clear()
    _state.set_mode(chat_id, "kette_aufgaben")
    s = _state.get(chat_id)
    s["kette_aufgaben_liste"] = []

    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = "Grenzverletzendes Glied"
    update.message.reply_text = AsyncMock()

    orig_verl, orig_fmt = _lc.verletzungen, _lc.format_verletzungen
    orig_reply = _th.reply_markdown_safe
    _lc.verletzungen = AsyncMock(return_value=[
        {"limit": "X", "quelle": "sklave_hard_limit", "matched_via": "X"}])
    _lc.format_verletzungen = lambda tr: "X"
    _th.reply_markdown_safe = AsyncMock()
    try:
        await _domina.handle_kette_aufgaben(update, MagicMock())
        assert s.get("kette_aufgaben_liste") == [], \
            "Regression H1: grenzverletzendes Glied wurde in die Kette übernommen"
        assert _th.reply_markdown_safe.await_count == 1, "Grenzen-Hinweis fehlt"

        # Sauberes Glied kommt weiterhin durch
        _lc.verletzungen = AsyncMock(return_value=[])
        update.message.text = "Sauberes Glied"
        await _domina.handle_kette_aufgaben(update, MagicMock())
        assert s.get("kette_aufgaben_liste") == ["Sauberes Glied"]
    finally:
        _lc.verletzungen, _lc.format_verletzungen = orig_verl, orig_fmt
        _th.reply_markdown_safe = orig_reply
        _state._state.clear()


# --------------------------------------------------------------------------
# H5 – Domina-Reaktion lässt den Status auf nicht_erledigt
# --------------------------------------------------------------------------

async def test_reaktion_flippt_status_nicht():
    chat_id = _config.DOMINA_CHAT_ID
    _state._state.clear()
    s = _state.get(chat_id)
    captured = {}

    async def fake_update_task(task_id, fields):
        captured.update(fields)

    orig_ut = _reaktion.qdrant.update_task
    orig_grok = _reaktion.grok.simple
    orig_send = _reaktion.telegram_helper.send_sklave
    _reaktion.qdrant.update_task = fake_update_task
    _reaktion.grok.simple = AsyncMock(return_value="Reaktion der Herrin")
    _reaktion.telegram_helper.send_sklave = AsyncMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    try:
        await _reaktion._weiterleiten(update, MagicMock(), chat_id, "task-1", s, "mein Kommentar")
        assert "status" not in captured, \
            "Regression H5: Status-Flip ist zurück (Write-only-Status reaktion_gesendet)"
        assert captured.get("domina_reaktion") == "mein Kommentar"
        assert captured.get("reaktion_am"), "reaktion_am-Zeitstempel fehlt"
    finally:
        _reaktion.qdrant.update_task = orig_ut
        _reaktion.grok.simple = orig_grok
        _reaktion.telegram_helper.send_sklave = orig_send
        _state._state.clear()


# --------------------------------------------------------------------------
# H4 – Statusliste ohne tote Werte + Serie-Stopp-Key im Flow-State
# --------------------------------------------------------------------------

def test_loeschbare_status_liste():
    assert "serie_aktiv" not in _aufgaben._LOESCHBARE_STATUS, "toter Status serie_aktiv ist zurück"
    assert "reaktion_pending" not in _aufgaben._LOESCHBARE_STATUS, "toter Status reaktion_pending ist zurück"
    for echt in ("serie_wartend", "kette_wartend", "geplant", "pausiert"):
        assert echt in _aufgaben._LOESCHBARE_STATUS, f"echter Wartestatus fehlt: {echt}"
    assert "gefuehl_pending" not in _aufgaben._STOPPBARE_GLIED_STATUS, \
        "gefuehl_pending darf beim Serie-Stopp nicht verworfen werden (Aufgabe ist erledigt)"
    assert "loeschen_serie_stopp" in _state.FLOW_STATE_KEYS, \
        "loeschen_serie_stopp fehlt in FLOW_STATE_KEYS (bleibt nach /abbrechen liegen)"


# --------------------------------------------------------------------------
# H6 – Cleanup erstattet unentschiedene Einlösungen
# --------------------------------------------------------------------------

async def test_cleanup_erstattet_unentschiedene():
    alt = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    frisch = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    profil = {
        "punkte": 10,
        "aktive_privilegien": [
            # unentschieden + alt → weg, aber erstattet (pause_tag = 50 P)
            {"aktiv_id": "a1", "privileg_id": "pause_tag", "name": "Pause-Tag",
             "wirkung": "skip_next_task", "eingeloest_am": alt,
             "domina_bestaetigt": False, "verbraucht": False},
            # unentschieden + frisch → bleibt
            {"aktiv_id": "a2", "privileg_id": "easy_mode", "name": "Easy Mode",
             "wirkung": "schwierigkeit_niedrig_3tage", "eingeloest_am": frisch,
             "domina_bestaetigt": False, "verbraucht": False},
            # verbraucht → weg, keine Erstattung
            {"aktiv_id": "a3", "privileg_id": "lob", "name": "Lob",
             "wirkung": "sofort_lob", "eingeloest_am": alt,
             "domina_bestaetigt": True, "verbraucht": True},
        ],
    }
    captured = {}

    async def fake_patch(user_id, fields):
        captured.update(fields)

    orig_get, orig_patch = _pe.qdrant.get_user_profile, _pe.qdrant.patch_profile_fields
    _pe.qdrant.get_user_profile = _aw(lambda uid: profil)
    _pe.qdrant.patch_profile_fields = fake_patch
    try:
        entfernt = await _pe.cleanup()
        assert entfernt == 2, f"erwartet 2 entfernte Einträge, war {entfernt}"
        assert captured.get("punkte") == 60, \
            f"Regression H6: Erstattung fehlt/falsch (erwartet 10+50=60, war {captured.get('punkte')})"
        uebrig = [p["aktiv_id"] for p in captured.get("aktive_privilegien", [])]
        assert uebrig == ["a2"], f"falsche Einträge behalten: {uebrig}"
    finally:
        _pe.qdrant.get_user_profile = orig_get
        _pe.qdrant.patch_profile_fields = orig_patch


# --------------------------------------------------------------------------
# H3 – Ketten-Fangnetz: erst beobachten, nach Wartezeit Domina fragen
# --------------------------------------------------------------------------

async def test_kette_fangnetz():
    glied = {
        "qdrant_point_id": "g3", "kette_id": "k1", "kette_position": 3,
        "kette_gesamt": 4, "aufgabe": "Glied drei", "status": "kette_wartend",
    }
    alle = [
        {"kette_position": 1, "status": "erledigt"},
        {"kette_position": 2, "status": "geloescht"},
        dict(glied),
        {"kette_position": 4, "status": "kette_wartend"},
    ]
    updates = []

    async def fake_update_task(task_id, fields):
        updates.append((task_id, fields))

    orig_gts = _sched.qdrant.get_tasks_by_status
    orig_ggg = getattr(_sched.qdrant, "get_gruppen_glieder", None)
    orig_ut = _sched.qdrant.update_task
    orig_send = _sched.telegram_helper.send_domina
    _sched.qdrant.get_tasks_by_status = _aw(lambda status, **k: [dict(glied)])
    _sched.qdrant.get_gruppen_glieder = _aw(lambda feld, wert, status=None: list(alle))
    _sched.qdrant.update_task = fake_update_task
    _sched.telegram_helper.send_domina = AsyncMock()
    try:
        # 1. Lauf: nur beobachten (kette_sweep_am stempeln), keine Frage
        await _sched._process_kette_tasks(MagicMock())
        assert len(updates) == 1 and "kette_sweep_am" in updates[0][1]
        assert _sched.telegram_helper.send_domina.await_count == 0, \
            "Regression H3: Frage ging schon im Beobachtungs-Lauf raus"

        # 2. Lauf mit altem Stempel: Domina wird gefragt + neuer Stempel (Throttle)
        updates.clear()
        vor_drei_tagen = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        alter_fall = dict(glied, kette_sweep_am=vor_drei_tagen)
        _sched.qdrant.get_tasks_by_status = _aw(lambda status, **k: [alter_fall])
        await _sched._process_kette_tasks(MagicMock())
        assert _sched.telegram_helper.send_domina.await_count == 1, \
            "Regression H3: hängende Kette wurde nicht nachgefragt"
        assert len(updates) == 1 and "kette_sweep_am" in updates[0][1]

        # Aktiver Vorgänger → kein Eingriff
        updates.clear()
        _sched.telegram_helper.send_domina.reset_mock()
        alle[1] = {"kette_position": 2, "status": "offen"}
        _sched.qdrant.get_tasks_by_status = _aw(lambda status, **k: [dict(glied)])
        await _sched._process_kette_tasks(MagicMock())
        assert not updates and _sched.telegram_helper.send_domina.await_count == 0, \
            "Regression H3: Sweep greift ein, obwohl die Kette normal läuft"
    finally:
        _sched.qdrant.get_tasks_by_status = orig_gts
        if orig_ggg is not None:
            _sched.qdrant.get_gruppen_glieder = orig_ggg
        _sched.qdrant.update_task = orig_ut
        _sched.telegram_helper.send_domina = orig_send


# --------------------------------------------------------------------------

def main() -> None:
    test_loeschbare_status_liste()
    for coro in (
        test_post_chat_leere_antwort_raist,
        test_kette_glied_limits_check,
        test_reaktion_flippt_status_nicht,
        test_cleanup_erstattet_unentschiedene,
        test_kette_fangnetz,
    ):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    print("✅ test_loeschbare_status_liste")
    print("✅ Alle Review-D8-Tests bestanden")


if __name__ == "__main__":
    main()
