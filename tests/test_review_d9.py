"""
Regressions-Tests für Review Durchgang 9 (2026-08-15), MITTEL-Fixes:

  M1  – rollenspiel_aktiv hat ein eigenes Stale-Fenster (3 Tage) + touch_mode
  M2  – Send zuerst, Followup-Mode danach (kette_adaptiv); blocked_modes
        kennen stimmung/quiz_antwort
  M3  – reaktion.py: "ja bitte" bestätigt die Strafe (Token-Matching)
  M4  – wunschkategorien: "abbrechen" wird keine eigene Kategorie
  M5  – resurface: Limits-Check gegen die AKTUELLEN Grenzen
  M6  – get_nicht_erledigt_streak filtert auf erledigt/nicht_erledigt
  M7  – Blitz: TOCTOU-Re-Check nach den LLM-Awaits (Safeword-Pause)
  M8  – Advent: Safeword-Guard vor Generierung/Zustellung
  M9  – daily_training: kein Geister-Mode bei Send-Fehler
  M10 – Vorlieben zeilenweise statt komma-verkettet (bestrafung)
  M11 – Hybrid-Kontext läuft ohne query_vector (Embedding-Ausfall)

Security-Fixes (S-Befunde):
  S1  – zentrales Callback-Gate (fremder Chat / falsche Rolle → Stop)
  S2  – state.json wird mit 0600 persistiert
  S3  – Logdatei behält 0600 auch nach doRollover()
  S6  – Stimmungs-Freitext im Coach-Prompt mit Daten-Delimiter

N-/DIV-Fixes (15.08. abends):
  N1/N2 – Wunsch-Callbacks: Status-Guard + Inhalts-Hash gegen Index-Verschiebung
  N13 – get_latest_stimmung mit Frische-Fenster
  N17 – Limit-Patches dedupen Eingabe + Case-Varianten
  N18 – persona_config: Cache erst NACH erfolgreichem Persist
  N19 – upsert_user_profile mit Merge-Semantik
  N22 – kat_to_cmd erzeugt tippbare Commands
  DIV1/3/5 – Schablonen-Detektor (passt-weil / Wie wär's / Wie lange)
  DIV3/DIV6 – Abschluss-Sperrliste + Lieblings-vs-schwach-Abgleich im Prompt

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_review_d9.py
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import date
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

from bot import state as _state  # noqa: E402
from bot.services import qdrant as _q  # noqa: E402
from bot.handlers import reaktion as _reaktion  # noqa: E402
from bot.handlers import wunschkategorien as _wk  # noqa: E402
from bot.handlers import resurface as _rs  # noqa: E402
from bot.handlers import blitz as _bl  # noqa: E402
from bot.handlers import advent as _ad  # noqa: E402
from bot.handlers import training as _tr  # noqa: E402
from bot.handlers import kette_adaptiv as _ka  # noqa: E402
from bot.prompts import bestrafung as _bp  # noqa: E402


def _fake_update(chat_id: str, text: str):
    u = MagicMock()
    u.effective_chat.id = chat_id
    u.message.text = text
    u.message.reply_text = AsyncMock()
    return u


def _fake_query(data: str, chat_id: str = "111"):
    u = MagicMock()
    q = u.callback_query
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    q.message.reply_text = AsyncMock()
    q.message.chat_id = chat_id
    return u


# --------------------------------------------------------------------------
# M1 – rollenspiel_aktiv: eigenes Stale-Fenster + touch_mode
# --------------------------------------------------------------------------

def test_rollenspiel_stale_fenster():
    assert _state._MODE_MAX_AGE.get("rollenspiel_aktiv", 0) >= 3 * 86400
    chat = "111"
    _state.set_mode(chat, "rollenspiel_aktiv")
    s = _state.get(chat)
    s["szenario_name"] = "Test"
    # 2 Stunden alt: darf NICHT resetten (alter Default war 30 Min)
    s["mode_since"] = time.time() - 2 * 3600
    assert _state.clear_if_stale(chat) is False
    assert _state.get_mode(chat) == "rollenspiel_aktiv"
    assert s.get("szenario_name") == "Test"
    # touch_mode frischt das Fenster auf
    s["mode_since"] = time.time() - 2 * 86400
    _state.touch_mode(chat)
    assert time.time() - s["mode_since"] < 60
    # 4 Tage idle: Reset inkl. szenario_*-Keys
    s["mode_since"] = time.time() - 4 * 86400
    assert _state.clear_if_stale(chat) is True
    assert _state.get_mode(chat) == "chat"
    assert "szenario_name" not in _state.get(chat)


# --------------------------------------------------------------------------
# M2 – blocked_modes kennen stimmung/quiz_antwort
# --------------------------------------------------------------------------

def test_set_followup_blockt_stimmung_und_quiz():
    chat = "222"
    for mode in ("stimmung", "quiz_antwort"):
        _state.set_mode(chat, mode)
        assert _state.set_followup_task(chat, "t1") is False, mode
        assert _state.get_mode(chat) == mode
    _state.set_mode(chat, "chat")
    assert _state.set_followup_task(chat, "t1") is True
    _state.set_mode(chat, "chat")


# --------------------------------------------------------------------------
# M2 – kette_adaptiv: Send-Fehler setzt keinen Followup-Mode
# --------------------------------------------------------------------------

async def test_kette_adaptiv_send_zuerst():
    _state.set_mode("222", "chat")
    _state.get("222")["followup_task_id"] = None
    _ka.paare.sub_chat_id = lambda: "222"
    _ka.qdrant.get_task = AsyncMock(return_value={
        "status": "kette_wartend", "aufgabe": "X", "kette_position": 2,
        "kette_gesamt": 3, "kette_id": "k1", "qdrant_point_id": "tid",
    })
    _ka.qdrant.update_task = AsyncMock()
    _ka.grok.simple = AsyncMock(return_value="Anweisung")
    _ka.telegram_helper.send_sklave = AsyncMock(side_effect=Exception("netz"))

    fehler = None
    try:
        await _ka.callback_fehlschlag(_fake_query("kettefail:weiter:tid"), MagicMock())
    except Exception as e:  # Send-Fehler propagiert zum error_handler
        fehler = e
    assert fehler is not None
    assert _state.get_mode("222") != "followup"
    assert _state.get("222").get("followup_task_id") != "tid"


# --------------------------------------------------------------------------
# M3 – reaktion: Token-Matching aufs erste Wort
# --------------------------------------------------------------------------

async def test_reaktion_ja_bitte_bestaetigt():
    chat = "111"
    orig_best, orig_alt = _reaktion._bestaetigen, _reaktion._alternativ_senden
    try:
        _reaktion._bestaetigen = AsyncMock()
        _reaktion._alternativ_senden = AsyncMock()

        def _arm():
            s = _state.get(chat)
            s["reaktion_fuer_task_id"] = "t1"
            s["strafe_id"] = "s1"
            _state.set_mode(chat, "reaktion_pending")

        _arm()
        await _reaktion.handle(_fake_update(chat, "Ja bitte, mach das!"), MagicMock())
        assert _reaktion._bestaetigen.await_count == 1
        assert _reaktion._alternativ_senden.await_count == 0

        # "geschafft" im Freitext darf KEIN Ja sein (bewusst nicht synonyme.ja_nein)
        _arm()
        await _reaktion.handle(_fake_update(chat, "Er soll zeigen was er geschafft hat"), MagicMock())
        assert _reaktion._alternativ_senden.await_count == 1
        assert _reaktion._bestaetigen.await_count == 1

        # "Nein." -> Alternativ-Frage
        _arm()
        u = _fake_update(chat, "Nein.")
        await _reaktion.handle(u, MagicMock())
        assert _state.get_mode(chat) == "reaktion_alternativ"
        assert u.message.reply_text.await_count == 1
    finally:
        _reaktion._bestaetigen, _reaktion._alternativ_senden = orig_best, orig_alt
        _state.set_mode(chat, "chat")
        _state.clear_flow_keys(chat)


# --------------------------------------------------------------------------
# M4 – wunschkategorien: "abbrechen" legt keine Kategorie an
# --------------------------------------------------------------------------

async def test_wunschkategorien_abbrechen():
    chat = "222"
    _state.set_mode(chat, "wunschkategorien_wahl")
    orig = _wk.qdrant.get_user_profile
    try:
        _wk.qdrant.get_user_profile = AsyncMock()
        u = _fake_update(chat, "abbrechen")
        await _wk.handle(u, MagicMock())
        assert _wk.qdrant.get_user_profile.await_count == 0
        assert _state.get_mode(chat) == "chat"
        assert u.message.reply_text.await_count == 1
    finally:
        _wk.qdrant.get_user_profile = orig


# --------------------------------------------------------------------------
# M5 – resurface: Limits-Treffer blockt die Re-Erteilung
# --------------------------------------------------------------------------

async def test_resurface_limits_block():
    _rs.paare.dom_chat_id = lambda: "111"
    _rs.qdrant.get_task = AsyncMock(return_value={
        "aufgabe": "Alte Aufgabe", "kategorie": "Anal", "level": 3,
    })
    _rs.qdrant.get_user_profile = AsyncMock(return_value={})
    _rs.qdrant.erstelle_task = AsyncMock()
    orig_verl, orig_fmt = _rs.limits_check.verletzungen, _rs.limits_check.format_verletzungen
    try:
        _rs.limits_check.verletzungen = AsyncMock(
            return_value=[{"quelle": "sklave_hard_limit", "begriff": "x"}])
        _rs.limits_check.format_verletzungen = lambda t: "x"
        u = _fake_query("resurface:erteilen:tid")
        await _rs.callback(u, MagicMock())
        assert _rs.qdrant.erstelle_task.await_count == 0
        assert u.callback_query.message.reply_text.await_count == 1
    finally:
        _rs.limits_check.verletzungen = orig_verl
        _rs.limits_check.format_verletzungen = orig_fmt


# --------------------------------------------------------------------------
# M6 – Streak filtert auf entschiedene Status + zählt korrekt
# --------------------------------------------------------------------------

async def test_streak_status_filter():
    orig_aio, orig_mk = _q._aio, _q.mandanten_key
    try:
        _q.mandanten_key = lambda u: u
        punkte = [SimpleNamespace(payload={"status": s})
                  for s in ("nicht_erledigt", "nicht_erledigt", "erledigt", "nicht_erledigt")]
        _q._aio = AsyncMock(return_value=(punkte, None))
        streak = await _q.get_nicht_erledigt_streak("sklave")
        assert streak == 2

        f = _q._aio.call_args.kwargs["scroll_filter"]
        if isinstance(_q.qm.Filter, MagicMock):
            # Stub-Modus: der MatchAny-Aufruf mit beiden Status muss gefallen sein
            assert any(c.kwargs.get("any") == ["erledigt", "nicht_erledigt"]
                       for c in _q.qm.MatchAny.call_args_list)
        else:
            status_conds = [c for c in f.must if getattr(c, "key", "") == "status"]
            assert status_conds and set(status_conds[0].match.any) == {"erledigt", "nicht_erledigt"}
    finally:
        _q._aio, _q.mandanten_key = orig_aio, orig_mk


# --------------------------------------------------------------------------
# M7 – Blitz: Safeword im LLM-Fenster -> kein Task, kein Send
# --------------------------------------------------------------------------

async def test_blitz_toctou_pause():
    _bl.paare.sub_chat_id = lambda: "222"
    _bl.qdrant.get_user_profile = AsyncMock(return_value={})
    _bl._generiere_blitz = AsyncMock(return_value=("Text", "Anal"))
    _bl.grok.simple = AsyncMock(return_value="Anweisung")
    _bl.qdrant.erstelle_task = AsyncMock()
    _state.set_paused(True)
    try:
        ok = await _bl.sende_blitz(MagicMock())
        assert ok is False
        assert _bl.qdrant.erstelle_task.await_count == 0
    finally:
        _state.set_paused(False)


# --------------------------------------------------------------------------
# M8 – Advent: Safeword-Pause -> Türchen wird übersprungen
# --------------------------------------------------------------------------

async def test_advent_pause_skip():
    orig_akt, orig_heute, orig_profil = _ad._aktueller, _ad._heute, _ad.qdrant.get_user_profile
    try:
        _ad._aktueller = AsyncMock(return_value={"jahr": 2026, "letzte_tuer": 0, "thema": "Test"})
        _ad._heute = lambda: date(2026, 12, 12)
        _ad.qdrant.get_user_profile = AsyncMock(return_value={})
        _state.set_paused(True)
        await _ad.oeffne_tuerchen(MagicMock())
        assert _ad.qdrant.get_user_profile.await_count == 0
    finally:
        _state.set_paused(False)
        _ad._aktueller, _ad._heute, _ad.qdrant.get_user_profile = orig_akt, orig_heute, orig_profil


# --------------------------------------------------------------------------
# M9 – daily_training: Send-Fehler -> kein Geister-Mode
# --------------------------------------------------------------------------

async def test_training_send_fehler_kein_geister_mode():
    orig_enabled = _config.TRAINING_ENABLED
    orig_gen = _tr._generiere_uebung
    try:
        _config.TRAINING_ENABLED = True
        _tr.paare.dom_chat_id = lambda: "111"
        _state.set_mode("111", "chat")
        _tr.qdrant.get_user_profile = AsyncMock(return_value={"erfahrungsstand": "x"})
        _tr.qdrant.get_tasks_by_status = AsyncMock(return_value=[])
        _tr.qdrant.get_training_entries = AsyncMock(return_value=[])
        _tr._generiere_uebung = AsyncMock(return_value="Übung")
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("netz"))
        await _tr.daily_training(bot)  # Fehler wird intern gefangen
        assert _state.get_mode("111") == "chat"
        assert "training_typ" not in _state.get("111") or not _state.get("111").get("training_typ")
    finally:
        _config.TRAINING_ENABLED = orig_enabled
        _tr._generiere_uebung = orig_gen


# --------------------------------------------------------------------------
# M10 – Vorlieben zeilenweise (Richtungs-Constraints bleiben beisammen)
# --------------------------------------------------------------------------

def test_vorlieben_zeilenweise():
    system, prompt = _bp.bestrafungsvorschlag(
        "Aufgabe X", 0,
        sklave_vorlieben=["Wachs (nur bei der Domina, Sub gießt)", "Lecken"],
    )
    beides = system + "\n" + prompt
    assert "- Wachs (nur bei der Domina, Sub gießt)\n- Lecken" in beides
    assert "Wachs (nur bei der Domina, Sub gießt), Lecken" not in beides


# --------------------------------------------------------------------------
# M11 – Hybrid-Kontext ohne query_vector (Embedding-Ausfall)
# --------------------------------------------------------------------------

async def test_hybrid_ohne_vektor():
    orig_aio, orig_mk, orig_sem = _q._aio, _q.mandanten_key, _q.get_conversation_context
    try:
        _q.mandanten_key = lambda u: u
        _q.get_conversation_context = AsyncMock(
            side_effect=AssertionError("Semantik-Arm darf ohne Vektor nicht laufen"))
        punkte = [SimpleNamespace(payload={"session_id": f"s{i}", "datum": f"2026-08-{10+i:02d}",
                                           "thema": "allgemein"}) for i in range(5)]
        _q._aio = AsyncMock(return_value=(punkte, None))
        res = await _q.get_hybrid_conversation_context("domina", None, limit=3)
        assert len(res) == 3
        assert _q.get_conversation_context.await_count == 0
    finally:
        _q._aio, _q.mandanten_key, _q.get_conversation_context = orig_aio, orig_mk, orig_sem


# --------------------------------------------------------------------------
# S1 – zentrales Callback-Gate
# --------------------------------------------------------------------------

async def test_callback_gate():
    from bot import main as _main

    class _Stop(Exception):
        pass

    orig_stop, orig_resolve = _main.ApplicationHandlerStop, _main.paare.resolve
    _main.ApplicationHandlerStop = _Stop
    try:
        def gate_update(data, chat="999"):
            u = MagicMock()
            u.callback_query.data = data
            u.callback_query.answer = AsyncMock()
            u.effective_chat.id = chat
            return u

        # Fremder Chat → Stop
        _main.paare.resolve = lambda cid: None
        try:
            await _main._callback_gate(gate_update("privileg:einloesen:pause_tag"))
            raise AssertionError("fremder Chat nicht gestoppt")
        except _Stop:
            pass
        # Falsche Rolle (Dom drückt Sub-Button) → Stop
        _main.paare.resolve = lambda cid: (MagicMock(), _main.paare.ROLLE_DOM)
        try:
            await _main._callback_gate(gate_update("privileg:einloesen:pause_tag"))
            raise AssertionError("falsche Rolle nicht gestoppt")
        except _Stop:
            pass
        # Richtige Rollen laufen durch
        _main.paare.resolve = lambda cid: (MagicMock(), _main.paare.ROLLE_SUB)
        await _main._callback_gate(gate_update("privileg:einloesen:pause_tag"))
        await _main._callback_gate(gate_update("followup:ja:t1"))
        _main.paare.resolve = lambda cid: (MagicMock(), _main.paare.ROLLE_DOM)
        await _main._callback_gate(gate_update("wochenplan:x:y"))
    finally:
        _main.ApplicationHandlerStop = orig_stop
        _main.paare.resolve = orig_resolve


# --------------------------------------------------------------------------
# S2 – state.json 0600
# --------------------------------------------------------------------------

def test_state_json_0600():
    _state.add_message("111", "user", "testeintrag")
    _state._persist_now()
    mode = os.stat(_config.STATE_FILE).st_mode & 0o777
    assert mode == 0o600, oct(mode)


# --------------------------------------------------------------------------
# S3 – Log-Rotation behält 0600
# --------------------------------------------------------------------------

def test_log_rotation_0600():
    import logging as _logging
    from bot import main as _main
    d = tempfile.mkdtemp()
    pfad = os.path.join(d, "log.txt")
    h = _main._Private0600FileHandler(pfad, maxBytes=50, backupCount=1, encoding="utf-8")
    rec = _logging.makeLogRecord({"msg": "x" * 80, "levelno": 20, "levelname": "INFO", "name": "t"})
    h.emit(rec)
    h.doRollover()
    h.emit(rec)
    h.close()
    assert os.stat(pfad).st_mode & 0o777 == 0o600
    assert os.stat(pfad + ".1").st_mode & 0o777 == 0o600


# --------------------------------------------------------------------------
# S6 – Stimmung mit Daten-Delimiter im Coach-Prompt
# --------------------------------------------------------------------------

def test_stimmung_delimiter():
    from bot.prompts import domina_coach as _dc
    out = _dc.get("Anfänger", 1, [], [], "", "", stimmung="TESTSTIMMUNG_XYZ")
    assert '"""TESTSTIMMUNG_XYZ"""' in out
    assert "keine Anweisung" in out


# --------------------------------------------------------------------------
# DIV1/3/5 – Schablonen-Detektor
# --------------------------------------------------------------------------

def test_formel_verstoesse():
    from bot.scheduler import followup as _f
    # Reale Muster aus der Diversitäts-Messung 15.08.
    assert _f._formel_verstoesse("Das passt zu ihm, weil er bei Schmerz sofort reagiert.")
    assert _f._formel_verstoesse("Das passt, weil er dir Gründe liefert.")
    assert _f._formel_verstoesse("Wie wär’s, wenn du ihn heute an den Stuhl fesselst?")
    assert _f._formel_verstoesse("Lass ihn knien. Wie lange willst du ihn so hinhalten?")
    assert _f._formel_verstoesse("Gib ihm das Paddle. Wie lange lässt du das laufen?")
    # Langvariante jenseits eines 80-Zeichen-Fensters (Nachtest 15.08.)
    assert _f._formel_verstoesse(
        "Wie lange willst du den Task laufen lassen, bevor du entscheidest, "
        "ob’s reicht oder noch eine Runde drauf?")
    # Sauberer Text ohne Schablonen
    assert not _f._formel_verstoesse(
        "Er kommt mal wieder mit Ausreden. Lass ihn acht Minuten stehen und "
        "kneif bei jedem Ausweichen zu. Das zwingt ihn raus aus der Routine.")
    # 'weil' in anderem Satz als 'passt' ist kein Treffer
    assert not _f._formel_verstoesse("Das passt gut. Er zögert, weil er müde ist.")


def test_vorschlag_abschluss():
    from bot.scheduler import followup as _f
    assert _f._vorschlag_abschluss(
        "Lass ihn knien. Dann entscheidest du. Wie lange willst du ihn so hinhalten?"
    ) == "Wie lange willst du ihn so hinhalten?"
    assert _f._vorschlag_abschluss("") == ""


# --------------------------------------------------------------------------
# DIV3/DIV6 – Prompt-Rendering: Abschluss-Sperrliste + Lieblings-Abgleich
# --------------------------------------------------------------------------

def test_prompt_abschluesse_und_div6():
    from bot.prompts import followup as _fp
    _sys, prompt = _fp.tiny_task_vorschlag(
        erfahrungsstand="Anfänger", level=1, interessen=[],
        sklave_vorlieben=[], sklave_hard_limits=[],
        verbrauchte_abschluesse=["Wie lange willst du das laufen lassen?"],
        sklave_wunsch_kategorien=["Buttplug_Tragen", "Pegging"],
        bewertungs_kontext="Aufgaben die weniger gefielen (1-2★): Buttplug_Tragen\n",
    )
    assert "VERBRAUCHTE ABSCHLÜSSE" in prompt
    assert "Wie lange willst du das laufen lassen?" in prompt
    assert "Buttplug_Tragen (zuletzt aber schwach bewertet" in prompt
    assert "💚 Pegging" in prompt and "Pegging (zuletzt" not in prompt


# --------------------------------------------------------------------------
# N1/N2 – Wunsch-Callbacks
# --------------------------------------------------------------------------

async def test_wunsch_guards():
    from bot.handlers import wunsch as _w
    # N1: bereits entschiedener Wunsch wird nicht erneut entschieden
    orig_get, orig_save = _w.qdrant.get_wunsch, _w._entscheidung_speichern
    try:
        _w.qdrant.get_wunsch = AsyncMock(return_value={"status": "angenommen"})
        _w._entscheidung_speichern = AsyncMock()
        u = _fake_query("wunsch:ablehnen:w1")
        await _w.callback_entscheidung(u, MagicMock())
        assert _w._entscheidung_speichern.await_count == 0
    finally:
        _w.qdrant.get_wunsch, _w._entscheidung_speichern = orig_get, orig_save

    # N2: Hash-Mismatch löscht nichts, sondern aktualisiert die Liste
    orig_prof, orig_patch = _w.qdrant.get_user_profile, _w.qdrant.patch_profile_fields
    orig_sub = _w.paare.sub_chat_id
    try:
        _w.paare.sub_chat_id = lambda: "222"
        _w.qdrant.get_user_profile = AsyncMock(
            return_value={"entdeckte_wuensche": ["Wunsch A", "Wunsch B"]})
        _w.qdrant.patch_profile_fields = AsyncMock()
        u = _fake_query(f"wunschdel:0:{_w._wunsch_hash('ETWAS ANDERES')}", chat_id="222")
        u.callback_query.message.chat_id = "222"
        u.callback_query.edit_message_text = AsyncMock()
        await _w.callback_loeschen(u, MagicMock())
        assert _w.qdrant.patch_profile_fields.await_count == 0
        assert u.callback_query.edit_message_text.await_count == 1
    finally:
        _w.qdrant.get_user_profile, _w.qdrant.patch_profile_fields = orig_prof, orig_patch
        _w.paare.sub_chat_id = orig_sub


# --------------------------------------------------------------------------
# N13 – Stimmung-Frische
# --------------------------------------------------------------------------

async def test_stimmung_frische():
    from datetime import datetime, timedelta, timezone as _tz
    orig_aio, orig_mk = _q._aio, _q.mandanten_key
    try:
        _q.mandanten_key = lambda u: u
        alt = (datetime.now(_tz.utc) - timedelta(days=5)).isoformat()
        _q._aio = AsyncMock(return_value=(
            [SimpleNamespace(payload={"datum": alt, "zusammenfassung": "alt"})], None))
        assert await _q.get_latest_stimmung("sklave", max_stunden=48) is None
        assert (await _q.get_latest_stimmung("sklave"))["zusammenfassung"] == "alt"
    finally:
        _q._aio, _q.mandanten_key = orig_aio, orig_mk


# --------------------------------------------------------------------------
# N17 – Limit-Dedup (Eingabe + Case)
# --------------------------------------------------------------------------

async def test_limit_dedup():
    orig_aio, orig_mk = _q._aio, _q.mandanten_key
    try:
        _q.mandanten_key = lambda u: u
        punkt = SimpleNamespace(id="p1", payload={"user_id": "sklave", "hard_limits": ["Nadeln"]})
        aufrufe = []

        async def _fake_aio(fn, **kw):
            aufrufe.append(kw)
            return ([punkt], None) if "scroll_filter" in kw else None

        _q._aio = _fake_aio
        orig_reembed = _q._reembed_profile_vector
        _q._reembed_profile_vector = AsyncMock()
        try:
            neue = await _q.append_profile_limits("sklave", "hard_limits",
                                                  ["Nadeln", "nadeln", "Blut", "Blut"])
        finally:
            _q._reembed_profile_vector = orig_reembed
        assert neue == ["Blut"], neue
        set_calls = [a for a in aufrufe if "payload" in a and "hard_limits" in (a.get("payload") or {})]
        assert set_calls and set_calls[0]["payload"]["hard_limits"] == ["Nadeln", "Blut"]
    finally:
        _q._aio, _q.mandanten_key = orig_aio, orig_mk


# --------------------------------------------------------------------------
# N18 – Cache erst nach Persist
# --------------------------------------------------------------------------

async def test_persona_cache_nach_persist():
    from bot.services import persona_config as _pc
    orig = _pc.qdrant.patch_profile_fields
    try:
        _pc.qdrant.patch_profile_fields = AsyncMock(side_effect=RuntimeError("qdrant down"))
        cache_vorher = dict(_pc._aktueller_cache())
        fehler = None
        try:
            await _pc.set_safeword("neuwort", "weiterwort")
        except RuntimeError as e:
            fehler = e
        assert fehler is not None
        assert _pc._aktueller_cache().get("safeword") == cache_vorher.get("safeword")
        assert _pc._aktueller_cache().get("safeword") != "neuwort"
    finally:
        _pc.qdrant.patch_profile_fields = orig


# --------------------------------------------------------------------------
# N19 – upsert_user_profile merged statt zu ersetzen
# --------------------------------------------------------------------------

async def test_upsert_profile_merge():
    orig_aio, orig_mk, orig_emb = _q._aio, _q.mandanten_key, _q.emb.get_embedding
    try:
        _q.mandanten_key = lambda u: u
        punkt = SimpleNamespace(id="p1", payload={"user_id": "sklave",
                                                  "entdeckte_wuensche": ["W1"], "punkte": 42})
        gespeichert = {}

        async def _fake_aio(fn, **kw):
            if "scroll_filter" in kw:
                return ([punkt], None)
            if "points" in kw and kw["points"]:
                p = kw["points"][0]
                gespeichert.update(getattr(p, "payload", None) or {})
            return None

        _q._aio = _fake_aio
        _q.emb.get_embedding = AsyncMock(return_value=[0.0] * 4)
        await _q.upsert_user_profile("sklave", {"vorlieben": ["Neu"]})
        if gespeichert:  # Stub-Modus: PointStruct ist MagicMock ohne echtes payload
            assert gespeichert.get("entdeckte_wuensche") == ["W1"]
            assert gespeichert.get("punkte") == 42
            assert gespeichert.get("vorlieben") == ["Neu"]
    finally:
        _q._aio, _q.mandanten_key, _q.emb.get_embedding = orig_aio, orig_mk, orig_emb


# --------------------------------------------------------------------------
# N22 – kat_to_cmd
# --------------------------------------------------------------------------

def test_kat_to_cmd_sonderzeichen():
    assert _config.kat_to_cmd("Anal-Training") == "anal_training"
    assert _config.kat_to_cmd("Café Spiel") == "caf__spiel"
    assert _config.kat_to_cmd("Buttplug_Tragen") == "buttplug_tragen"
    assert _config.kat_to_cmd("Größe") == "groesse"


def main():
    for coro in (
        test_kette_adaptiv_send_zuerst,
        test_reaktion_ja_bitte_bestaetigt,
        test_wunschkategorien_abbrechen,
        test_resurface_limits_block,
        test_streak_status_filter,
        test_blitz_toctou_pause,
        test_advent_pause_skip,
        test_training_send_fehler_kein_geister_mode,
        test_hybrid_ohne_vektor,
        test_callback_gate,
        test_wunsch_guards,
        test_stimmung_frische,
        test_limit_dedup,
        test_persona_cache_nach_persist,
        test_upsert_profile_merge,
    ):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    test_formel_verstoesse()
    print("✅ test_formel_verstoesse")
    test_vorschlag_abschluss()
    print("✅ test_vorschlag_abschluss")
    test_prompt_abschluesse_und_div6()
    print("✅ test_prompt_abschluesse_und_div6")
    test_kat_to_cmd_sonderzeichen()
    print("✅ test_kat_to_cmd_sonderzeichen")
    test_state_json_0600()
    print("✅ test_state_json_0600")
    test_log_rotation_0600()
    print("✅ test_log_rotation_0600")
    test_stimmung_delimiter()
    print("✅ test_stimmung_delimiter")
    test_rollenspiel_stale_fenster()
    print("✅ test_rollenspiel_stale_fenster")
    test_set_followup_blockt_stimmung_und_quiz()
    print("✅ test_set_followup_blockt_stimmung_und_quiz")
    test_vorlieben_zeilenweise()
    print("✅ test_vorlieben_zeilenweise")
    print("✅ Alle Review-D9-Tests bestanden")


if __name__ == "__main__":
    main()
