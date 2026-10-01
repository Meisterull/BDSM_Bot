"""
Regressions-Tests Nachholen blockierter Einmal-Jobs (scheduler/followup):

Live 01.10.2026: Die unbeantwortete Stimmungsfrage hält den Sub-Mode zwei
Stunden, der Follow-up-Job fällt mitten hinein und lief nur einmal am Tag –
die Nachfrage zu einer fälligen Aufgabe fiel ersatzlos aus. Dasselbe Muster
traf alle Einmal-Jobs mit Mode-Guard (Wochenplanung, Coach-Reflexion, …).

  - @_nachholbar: scheitert ein Job an einem Mode, wird er vorgemerkt;
    nachhol_tick_job holt ihn nach, sobald der Chat frei ist
  - nur im Fenster NACHHOL_FENSTER, höchstens ein Job pro Tick, Frist
    max_stunden ab dem regulären Lauf
  - Nachfrage: „eine Frage pro Tag" bleibt; eine in diesem Lauf zugestellte
    Serien-Aufgabe zählt nicht als blockiert
  - 2-Wochen-Takt gilt im Nachhol-Lauf als bestanden; Safeword-Pause merkt
    nichts vor

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_followup_nachholen.py
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

from bot import state  # noqa: E402
from bot.scheduler import followup as jobs  # noqa: E402

DOM, SUB = "111", "222"
IMMER = "00:00-23:59"


def _run(coro):
    return asyncio.run(coro)


def _fenster_ohne_jetzt() -> str:
    """Ein Fenster, das die aktuelle Uhrzeit sicher ausschließt."""
    jetzt = datetime.now(jobs.ZoneInfo(_config.TIMEZONE))
    return f"{(jetzt + timedelta(hours=2)):%H:%M}-{(jetzt + timedelta(hours=3)):%H:%M}"


def _vorgemerkt(name: str) -> bool:
    return any(k[1] == name for k in jobs._nachhol)


class _Welt:
    """Stubs für Qdrant/LLM/Versand; `tasks` sind die fälligen Aufgaben."""

    def __init__(self, tasks=()):
        self.offen = list(tasks)
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        state._state.clear()
        jobs._nachhol.clear()
        _config.NACHHOL_FENSTER = IMMER

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
        jobs._process_serie_tasks = AsyncMock(return_value=False)
        jobs._process_kette_tasks = AsyncMock()

    @property
    def fragen(self) -> int:
        return self.bot.send_message.await_count


def _task(pid):
    return {"qdrant_point_id": pid, "aufgabe": "Bad putzen", "erteilt_am": ""}


# --------------------------------------------------------------------------
# Nachfrage (followup_job)
# --------------------------------------------------------------------------

def test_nachfrage_blockiert_dann_nachgeholt():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0 and _vorgemerkt("followup_job")

    # Mode noch frisch → Tick läuft leer, Vormerkung bleibt
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 0 and _vorgemerkt("followup_job")

    # Außerhalb des Abendfensters wird nichts nachgereicht
    state.set_mode(SUB, "chat")
    _config.NACHHOL_FENSTER = _fenster_ohne_jetzt()
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 0 and _vorgemerkt("followup_job")

    # Im Fenster und Chat frei → Frage geht raus, Vormerkung weg
    _config.NACHHOL_FENSTER = IMMER
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 1 and not _vorgemerkt("followup_job")
    assert state.get_mode(SUB) == "followup"

    # Weitere Ticks am selben Abend tun nichts
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 1


def test_abgelaufener_mode_wird_im_tick_geraeumt():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0
    state.get(SUB)["mode_since"] -= state.STALE_STIMMUNG_SECONDS + 60
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 1 and not _vorgemerkt("followup_job")


def test_gefragt_merkt_nichts_vor():
    w = _Welt([_task("t1"), _task("t2")])
    _run(jobs.followup_job(w.bot))
    # Nach der ersten Frage steht der Mode auf 'followup' – kein blockierter
    # Lauf, die zweite Aufgabe wartet bis morgen (eine Frage pro Tag).
    assert w.fragen == 1 and not jobs._nachhol
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 1


def test_serie_zugestellt_ist_nicht_blockiert():
    w = _Welt([_task("t1")])

    async def _serie(bot):
        state.set_mode(SUB, "followup")   # Serien-Aufgabe ging raus
        return True
    jobs._process_serie_tasks = _serie
    _run(jobs.followup_job(w.bot))
    assert w.fragen == 0 and not jobs._nachhol


def test_frist_laeuft_ab():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    _run(jobs.followup_job(w.bot))
    eintrag = next(iter(jobs._nachhol.values()))
    assert eintrag["max_stunden"] == 5
    eintrag["seit"] = datetime.now(timezone.utc) - timedelta(hours=6)
    state.set_mode(SUB, "chat")
    _run(jobs.nachhol_tick_job(w.bot))
    assert w.fragen == 0 and not jobs._nachhol


def test_pause_merkt_nichts_vor():
    w = _Welt([_task("t1")])
    state.set_mode(SUB, "stimmung")
    state.set_paused(True)
    try:
        _run(jobs.followup_job(w.bot))
        assert not jobs._nachhol
    finally:
        state.set_paused(False)


# --------------------------------------------------------------------------
# Allgemeine Mechanik (beliebiger Job mit _flow_aktiv-Guard)
# --------------------------------------------------------------------------

def _dummy_job(name: str, max_stunden: int, laeufe: list):
    async def job(bot):
        if jobs._flow_aktiv(DOM, name):
            return
        laeufe.append(name)
    job.__name__ = name
    return jobs._nachholbar(max_stunden=max_stunden)(job)


def test_dom_job_wird_nachgeholt_und_nur_einer_pro_tick():
    w = _Welt()
    laeufe: list = []
    a = _dummy_job("job_a", 26, laeufe)
    b = _dummy_job("job_b", 26, laeufe)
    state.set_mode(DOM, "reaktion_pending")
    _run(a(w.bot))
    _run(b(w.bot))
    assert laeufe == [] and _vorgemerkt("job_a") and _vorgemerkt("job_b")
    # Reihenfolge = Reihenfolge der regulären Läufe
    jobs._nachhol[next(k for k in jobs._nachhol if k[1] == "job_a")]["seit"] -= timedelta(minutes=30)

    state.set_mode(DOM, "chat")
    _run(jobs.nachhol_tick_job(w.bot))
    assert laeufe == ["job_a"], laeufe          # keine Nachrichten-Salve
    _run(jobs.nachhol_tick_job(w.bot))
    assert laeufe == ["job_a", "job_b"] and not jobs._nachhol


def test_regulaerer_lauf_ohne_block_raeumt_vormerkung():
    w = _Welt()
    laeufe: list = []
    a = _dummy_job("job_a", 26, laeufe)
    state.set_mode(DOM, "reaktion_pending")
    _run(a(w.bot))
    assert _vorgemerkt("job_a")
    state.set_mode(DOM, "chat")
    _run(a(w.bot))                              # nächster regulärer Lauf
    assert laeufe == ["job_a"] and not jobs._nachhol


def test_fehler_im_nachhol_lauf_raeumt_vormerkung():
    w = _Welt()

    async def kaputt(bot):
        if jobs._flow_aktiv(DOM, "kaputt"):
            return
        raise RuntimeError("x")
    kaputt.__name__ = "kaputt"
    job = jobs._nachholbar(max_stunden=26)(kaputt)
    state.set_mode(DOM, "reaktion_pending")
    _run(job(w.bot))
    state.set_mode(DOM, "chat")
    _run(jobs.nachhol_tick_job(w.bot))          # _job_guard fängt den Fehler
    assert not jobs._nachhol


def test_zweiwochen_takt_gilt_im_nachhol_lauf():
    token = jobs._nachhol_lauf.set(True)
    try:
        assert jobs._zweiwochen_takt() is True
    finally:
        jobs._nachhol_lauf.reset(token)


def test_einmal_jobs_sind_markiert():
    # Markierte Jobs tragen den Wrapper von _nachholbar (über _job_guard hinweg
    # per __wrapped__ erreichbar).
    for name in ("followup_job", "tiny_task_vorschlag_job", "wochenplanung_job",
                 "coach_reflexion_job", "profil_pflege_job", "lernkurve_job",
                 "ziel_erinnerung_job", "resurface_job", "kommentar_analyse_job",
                 "rollenspiel_vorschlag_job", "stille_checkin_job", "luecken_check_job"):
        fn = getattr(jobs, name)
        tiefe = 0
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
            tiefe += 1
        assert tiefe >= 2, f"{name} ist nicht als nachholbar markiert"


def _run_alle():
    test_nachfrage_blockiert_dann_nachgeholt()
    test_abgelaufener_mode_wird_im_tick_geraeumt()
    test_gefragt_merkt_nichts_vor()
    test_serie_zugestellt_ist_nicht_blockiert()
    test_frist_laeuft_ab()
    test_pause_merkt_nichts_vor()
    test_dom_job_wird_nachgeholt_und_nur_einer_pro_tick()
    test_regulaerer_lauf_ohne_block_raeumt_vormerkung()
    test_fehler_im_nachhol_lauf_raeumt_vormerkung()
    test_zweiwochen_takt_gilt_im_nachhol_lauf()
    test_einmal_jobs_sind_markiert()
    print("✅ Alle Nachhol-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
