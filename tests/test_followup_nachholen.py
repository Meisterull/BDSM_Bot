"""
Regressions-Tests Nachhol-Lauf der täglichen Nachfrage (scheduler/followup):

Live 01.10.2026: Die unbeantwortete Stimmungsfrage hält den Sub-Mode zwei
Stunden, der Follow-up-Job fällt mitten hinein und lief nur einmal am Tag –
die Nachfrage zu einer fälligen Aufgabe fiel ersatzlos aus.

  - Hauptlauf an einem Mode gescheitert → Merker; Nachhol-Lauf fragt, sobald
    der Mode frei ist, und nur dann
  - Hauptlauf hat gefragt → kein Merker, Nachhol-Lauf tut nichts
  - zweite fällige Aufgabe hinter der gestellten Frage → kein Merker (eine
    Frage pro Tag bleibt)
  - Merker von gestern verfällt

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_followup_nachholen.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import date, timedelta

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

from bot import state  # noqa: E402
from bot.scheduler import followup as jobs  # noqa: E402

SUB = "222"


def _run(coro):
    return asyncio.run(coro)


class _Welt:
    """Stubs für Qdrant/LLM/Versand; `tasks` sind die fälligen Aufgaben."""

    def __init__(self, tasks):
        self.offen = list(tasks)
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        state._state.clear()
        jobs._followup_nachholen.clear()

        async def _open():
            return list(self.offen)

        async def _update(point_id, felder):
            if felder.get("status") == "gefragt":
                self.offen = [t for t in self.offen if t["qdrant_point_id"] != point_id]

        jobs.qdrant.get_open_followup_tasks = AsyncMock(side_effect=_open)
        jobs.qdrant.update_task = AsyncMock(side_effect=_update)
        jobs.qdrant.get_user_profile = AsyncMock(return_value={})
        jobs.qdrant.get_tasks_by_status = AsyncMock(return_value=[])
        jobs.qdrant.get_latest_stimmung = AsyncMock(return_value=None)
        jobs.grok.simple = AsyncMock(return_value="Und, erledigt?")
        jobs.sticker_reaktionen.sende_sklave = AsyncMock()
        jobs._process_serie_tasks = AsyncMock()
        jobs._process_kette_tasks = AsyncMock()

    @property
    def fragen(self) -> int:
        return self.bot.send_message.await_count


def _task(pid):
    return {"qdrant_point_id": pid, "aufgabe": "Bad putzen", "erteilt_am": ""}


def test_blockiert_dann_nachgeholt():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0 and SUB in jobs._followup_nachholen

    # Mode noch frisch → Nachhol-Lauf scheitert erneut, Merker bleibt
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 0 and SUB in jobs._followup_nachholen

    # Mode frei → Frage geht raus, Merker weg, Mode 'followup'
    state.set_mode(SUB, "chat")
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 1 and SUB not in jobs._followup_nachholen
    assert state.get_mode(SUB) == "followup"

    # Weitere Nachhol-Läufe am selben Abend tun nichts
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 1


def test_stale_mode_wird_im_nachhol_lauf_geraeumt():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0
    # Stimmungsfrage ist inzwischen abgelaufen, der Aufräum-Tick kam aber noch nicht
    state.get(SUB)["mode_since"] -= state.STALE_STIMMUNG_SECONDS + 60
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 1 and SUB not in jobs._followup_nachholen


def test_gefragt_setzt_keinen_merker():
    w = _Welt([_task("t1")])
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 1 and SUB not in jobs._followup_nachholen
    aufrufe = jobs.qdrant.get_open_followup_tasks.await_count
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 1
    assert jobs.qdrant.get_open_followup_tasks.await_count == aufrufe, "Nachhol-Lauf muss leer laufen"


def test_zweite_aufgabe_bleibt_fuer_morgen():
    w = _Welt([_task("t1"), _task("t2")])
    _run(jobs.followup_job(w.bot))
    # Nach der ersten Frage steht der Mode auf 'followup' – das ist kein
    # blockierter Lauf, die zweite Aufgabe wartet bis morgen.
    assert w.fragen == 1 and SUB not in jobs._followup_nachholen
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 1


def test_merker_von_gestern_verfaellt():
    w = _Welt([_task("t1")])
    jobs._followup_nachholen[SUB] = date.today() - timedelta(days=2)
    _run(jobs.followup_nachhol_job(w.bot))
    assert w.fragen == 0 and SUB not in jobs._followup_nachholen


def test_keine_aufgabe_kein_merker():
    w = _Welt([])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0 and SUB not in jobs._followup_nachholen


def _run_alle():
    test_blockiert_dann_nachgeholt()
    test_stale_mode_wird_im_nachhol_lauf_geraeumt()
    test_gefragt_setzt_keinen_merker()
    test_zweite_aufgabe_bleibt_fuer_morgen()
    test_merker_von_gestern_verfaellt()
    test_keine_aufgabe_kein_merker()
    print("✅ Alle Follow-up-Nachhol-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
