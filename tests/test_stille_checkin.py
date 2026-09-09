"""
Regressions-Tests Stille-Check-in 🔕 (09.09.2026, handlers/stille_checkin):

  - state: Eingangsstempel + Coach-Ruhe überleben _persist_now/load_persisted,
    befristete Ruhe läuft von selbst aus
  - scheduler._flow_aktiv gatet NUR den Dom-Chat bei Ruhe/Zuschauer
  - Phasen-Logik des Jobs: <7 Tage nichts; erste Frage; beantwortet → nie
    wieder; zweite Frage erst nach 14 Tagen; max. 2; Ruhe/Abwesenheit/aus
  - Frage-Text: LLM-Ausfall/Liste → Fallback mit Tagen, brauchbarer Text bleibt
  - Buttons: keine Zeit → Ruhe 14 Tage, läuft ohne Bot → Zuschauer, Vorschläge
    passen nicht → Rückfrage-Mode; Doppel-Tap löst nichts zweites aus
  - Freitext: Rückfrage → Coach-Regel (quelle stille_checkin); ANDERES → Coach-Chat,
    Frage bleibt offen; KEINE_ZEIT → Ruhe
  - /einstellungen → 9: -, ruhe, ruhe 7, zuschauer, Unsinn
  - /stats-Zeilen nennen nie den Inhalt der Antwort

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_stille_checkin.py
"""
import asyncio
import os
import sys
import tempfile
import time
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

from unittest.mock import AsyncMock  # noqa: E402

from bot import config as _config  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"
_config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
_config.STILLE_CHECKIN_TAGE = 7
_config.STILLE_ZWEITE_FRAGE_TAGE = 14
_config.STILLE_RUHE_TAGE = 14

from bot import state  # noqa: E402
from bot.handlers import stille_checkin as sc  # noqa: E402
from bot.scheduler import followup as fu  # noqa: E402
from bot.services import persona_config  # noqa: E402

DOM = "111"
sc.paare.dom_chat_id = lambda: DOM
fu.paare.dom_chat_id = lambda: DOM
fu.paare.sub_chat_id = lambda: "222"

JETZT = datetime.now(timezone.utc)


def _iso(delta_tage: float) -> str:
    return (JETZT - timedelta(days=delta_tage)).isoformat()


def _reset():
    state.set_coach_ruhe(None)
    state.set_mode(DOM, "chat")
    state.get(DOM).pop("stille_rueckfrage", None)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def test_state_persistenz_und_ablauf():
    _reset()
    state.touch_eingang(DOM)
    ts = state.letzter_eingang(DOM)
    assert ts and time.time() - ts < 5
    bis = (JETZT + timedelta(days=3)).isoformat()
    state.set_coach_ruhe("ruhe", bis=bis)
    assert state.coach_ruhe()["modus"] == "ruhe"
    state._persist_now()
    state._state.pop("__eingang__", None)
    state._state.pop("__coach_ruhe__", None)
    assert state.coach_ruhe() is None and state.letzter_eingang(DOM) is None
    state.load_persisted()
    assert state.coach_ruhe()["bis"] == bis, "Coach-Ruhe muss den Neustart überleben"
    assert abs(state.letzter_eingang(DOM) - ts) < 0.01, "Eingangsstempel muss den Neustart überleben"
    # Abgelaufene Ruhe räumt sich selbst weg
    state.set_coach_ruhe("ruhe", bis=(JETZT - timedelta(minutes=1)).isoformat())
    assert state.coach_ruhe() is None
    assert not (state._state.get("__coach_ruhe__") or {})
    # Zuschauer ist unbefristet
    state.set_coach_ruhe("zuschauer")
    assert state.coach_ruhe()["modus"] == "zuschauer" and state.coach_ruhe()["bis"] is None
    _reset()


def test_flow_aktiv_gate_nur_dom_chat():
    _reset()
    state.set_mode("222", "chat")
    assert fu._flow_aktiv(DOM, "T") is False
    state.set_coach_ruhe("zuschauer")
    assert fu._flow_aktiv(DOM, "T") is True, "Dom-Jobs müssen im Zuschauer-Modus pausieren"
    assert fu._flow_aktiv("222", "T") is False, "Sub-Jobs sind nicht betroffen"
    _reset()


def test_gleiche_phase():
    letzte = JETZT - timedelta(days=9)
    assert sc.gleiche_phase(letzte, {}) is False, "ohne Check-in nie gleiche Phase"
    ci = {"gefragt_am": (JETZT - timedelta(days=2)).isoformat()}
    assert sc.gleiche_phase(letzte, ci) is True
    # Antwort 5 Min nach der Frage zählt NICHT als neue Aktivität
    ci["antwort_am"] = (JETZT - timedelta(days=2) + timedelta(minutes=5)).isoformat()
    assert sc.gleiche_phase(JETZT - timedelta(days=2) + timedelta(minutes=6), ci) is True
    # Echte Aktivität danach = neue Phase
    assert sc.gleiche_phase(JETZT - timedelta(days=1), ci) is False


# --------------------------------------------------------------------------
# Job-Phasen
# --------------------------------------------------------------------------

def _job_lauf(letzte_tage, ci, ruhe=None, abwesend=False, tage_cfg=7):
    """stille_checkin_job (ohne _job_guard-Fangnetz) mit Stubs; liefert
    (frage_aufrufe, patches)."""
    _reset()
    _config.STILLE_CHECKIN_TAGE = tage_cfg
    if ruhe:
        state.set_coach_ruhe(ruhe)
    fragen, patches = [], []

    async def fake_letzte():
        return None if letzte_tage is None else JETZT - timedelta(days=letzte_tage)

    async def fake_profile(user_id):
        return {"stille_checkin": ci} if ci else {}

    async def fake_patch(user_id, fields, **kw):
        patches.append(fields)

    async def fake_frage(bot, tage, zweite=False, tage_seit_frage=0):
        fragen.append((tage, zweite, tage_seit_frage))
        return True

    orig = (sc.letzte_domina_eingabe, fu.qdrant.get_user_profile, fu.qdrant.patch_profile_fields,
            sc.frage_stellen, persona_config.abwesenheit)
    sc.letzte_domina_eingabe = fake_letzte
    fu.qdrant.get_user_profile = fake_profile
    fu.qdrant.patch_profile_fields = fake_patch
    sc.frage_stellen = fake_frage
    persona_config.abwesenheit = (lambda: ("a", "b", "")) if abwesend else (lambda: None)
    try:
        asyncio.run(fu.stille_checkin_job.__wrapped__(None))
    finally:
        (sc.letzte_domina_eingabe, fu.qdrant.get_user_profile, fu.qdrant.patch_profile_fields,
         sc.frage_stellen, persona_config.abwesenheit) = orig
        _config.STILLE_CHECKIN_TAGE = 7
        _reset()
    return fragen, patches


def test_job_phasen():
    # Frisch aktiv / nie aktiv → nichts
    assert _job_lauf(3, {}) == ([], [])
    assert _job_lauf(None, {}) == ([], [])
    # 9 Tage still, kein Check-in bisher → erste Frage, anzahl 1
    fragen, patches = _job_lauf(9, {})
    assert fragen == [(9, False, 0)] and patches[0]["stille_checkin"]["anzahl"] == 1
    assert "antwort_am" not in patches[0]["stille_checkin"]
    # Gleiche Phase, beantwortet → nie wieder
    ci = {"gefragt_am": _iso(2), "antwort_am": _iso(1.9), "anzahl": 1}
    assert _job_lauf(9, ci) == ([], [])
    # Gleiche Phase, unbeantwortet, erst 5 Tage her → warten
    ci = {"gefragt_am": _iso(5), "anzahl": 1}
    assert _job_lauf(12, ci) == ([], [])
    # … nach 15 Tagen → zweite (letzte) Frage
    ci = {"gefragt_am": _iso(15), "anzahl": 1}
    fragen, patches = _job_lauf(22, ci)
    assert fragen == [(22, True, 15)] and patches[0]["stille_checkin"]["anzahl"] == 2
    # Zweimal gefragt → Ruhe
    ci = {"gefragt_am": _iso(20), "anzahl": 2}
    assert _job_lauf(40, ci) == ([], [])
    # Neue Aktivität NACH dem alten Check-in → neue Phase, wieder erste Frage
    ci = {"gefragt_am": _iso(30), "antwort_am": _iso(29), "anzahl": 1}
    fragen, patches = _job_lauf(8, ci)
    assert fragen == [(8, False, 0)] and patches[0]["stille_checkin"]["anzahl"] == 1
    # Ruhe/Zuschauer, Abwesenheit, Feature aus → nichts
    assert _job_lauf(9, {}, ruhe="zuschauer") == ([], [])
    assert _job_lauf(9, {}, abwesend=True) == ([], [])
    assert _job_lauf(9, {}, tage_cfg=0) == ([], [])


# --------------------------------------------------------------------------
# Frage-Text
# --------------------------------------------------------------------------

def _mit_grok(antworten):
    """grok.simple-Stub: `antworten` = Liste oder Callable(system) → Text/Exception."""
    async def fake_simple(prompt, system="", **kw):
        r = antworten(system) if callable(antworten) else antworten
        if isinstance(r, Exception):
            raise r
        return r
    return fake_simple


def test_frage_text_fallback_und_abnahme():
    orig = sc.grok.simple
    try:
        sc.grok.simple = _mit_grok(RuntimeError("LLM weg"))
        text = asyncio.run(sc._frage_text(9, False, 0))
        assert "9 Tagen" in text and "?" in text
        sc.grok.simple = _mit_grok("Hier drei Ideen:\n1. Fessel ihn\n2. Wachs\n3. Facesitting?")
        text = asyncio.run(sc._frage_text(9, False, 0))
        assert "Fessel" not in text, "nummerierte Liste muss der Detektor verwerfen"
        sc.grok.simple = _mit_grok("Ohne Fragezeichen kein Check-in.")
        assert "?" in asyncio.run(sc._frage_text(9, False, 0))
        gut = "Hey, alles okay bei dir? Tipp einfach unten an, Pause ist auch fein."
        sc.grok.simple = _mit_grok(gut)
        assert asyncio.run(sc._frage_text(9, False, 0)) == gut
        # Zweite Frage: Fallback nennt die Tage seit der ERSTEN Frage
        sc.grok.simple = _mit_grok(RuntimeError("x"))
        assert "15 Tagen" in asyncio.run(sc._frage_text(30, True, 15))
    finally:
        sc.grok.simple = orig


def test_frage_stellen_toctou_und_mode():
    _reset()
    gesendet = []

    async def fake_send(bot, text, parse_mode=None, reply_markup=None, **kw):
        gesendet.append(text)

    orig = (sc.telegram_helper.send_domina, sc.grok.simple)
    sc.telegram_helper.send_domina = fake_send
    sc.grok.simple = _mit_grok(RuntimeError("x"))
    try:
        assert asyncio.run(sc.frage_stellen(None, 9)) is True
        assert len(gesendet) == 1 and state.get_mode(DOM) == sc.MODE
        # Im LLM-Fenster kam ein anderer Flow → nicht senden
        state.set_mode(DOM, "aufgabe_bestaetigung")
        assert asyncio.run(sc.frage_stellen(None, 9)) is False
        assert len(gesendet) == 1
    finally:
        sc.telegram_helper.send_domina, sc.grok.simple = orig
        _reset()


# --------------------------------------------------------------------------
# Buttons + Freitext
# --------------------------------------------------------------------------

class _Query:
    def __init__(self, data):
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()
        self.message = SimpleNamespace(reply_text=AsyncMock())


def _cb_update(data):
    return SimpleNamespace(callback_query=_Query(data), effective_chat=SimpleNamespace(id=111))


def _msg_update(text):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=111),
                           message=SimpleNamespace(text=text, reply_text=AsyncMock()))


class _Profil:
    """Domina-Profil-Stub mit Patch-Merge (stille_checkin wird komplett ersetzt)."""
    def __init__(self, ci):
        self.daten = {"stille_checkin": dict(ci)} if ci is not None else {}
        self.patches = []

    async def get(self, user_id):
        return dict(self.daten)

    async def patch(self, user_id, fields, **kw):
        self.patches.append(fields)
        self.daten.update(fields)


def _mit_profil(ci):
    p = _Profil(ci)
    sc.qdrant.get_user_profile = p.get
    sc.qdrant.patch_profile_fields = p.patch
    return p


def test_buttons_reaktionen_und_doppeltap():
    _reset()
    orig = (sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields)
    try:
        # keine Zeit → Ruhe 14 Tage, Antwort verbucht, Mode zu
        p = _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        state.set_mode(DOM, sc.MODE)
        u = _cb_update("stille:keine_zeit")
        asyncio.run(sc.callback(u, None))
        ruhe = state.coach_ruhe()
        assert ruhe and ruhe["modus"] == "ruhe"
        bis = datetime.fromisoformat(ruhe["bis"])
        assert timedelta(days=13, hours=23) < bis - JETZT < timedelta(days=14, hours=1)
        assert p.daten["stille_checkin"]["antwort_typ"] == "keine_zeit"
        assert state.get_mode(DOM) == "chat"
        assert u.callback_query.message.reply_text.await_count == 1
        # Doppel-Tap: Antwort steht schon → keine zweite Reaktion
        u2 = _cb_update("stille:ohne_bot")
        asyncio.run(sc.callback(u2, None))
        assert state.coach_ruhe()["modus"] == "ruhe", "Doppel-Tap darf den Modus nicht kippen"
        assert u2.callback_query.message.reply_text.await_count == 0

        # läuft ohne Bot → Zuschauer
        _reset()
        _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        asyncio.run(sc.callback(_cb_update("stille:ohne_bot"), None))
        assert state.coach_ruhe()["modus"] == "zuschauer"

        # Vorschläge passen nicht → Rückfrage-Mode, noch keine Schaltung
        _reset()
        p = _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        state.set_mode(DOM, sc.MODE)
        asyncio.run(sc.callback(_cb_update("stille:aufgaben"), None))
        assert state.coach_ruhe() is None
        assert state.get_mode(DOM) == sc.MODE and state.get(DOM)["stille_rueckfrage"] == "aufgaben"
        assert p.daten["stille_checkin"]["antwort_typ"] == "aufgaben"

        # Angebots-Button nach Freitext: schaltet Zuschauer, zweiter Tap ist no-op
        _reset()
        u = _cb_update("stille:ohne_bot_ja")
        asyncio.run(sc.callback(u, None))
        assert state.coach_ruhe()["modus"] == "zuschauer"
        u = _cb_update("stille:ohne_bot_ja")
        asyncio.run(sc.callback(u, None))
        assert u.callback_query.message.reply_text.await_count == 0
    finally:
        sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields = orig
        _reset()


def _grok_router(klasse="SONSTIGES", regel="KEINE_REGEL", kurz="Sie hat gerade viel um die Ohren."):
    def route(system):
        if "Klassifiziere ihre Antwort" in system:
            return klasse
        if "Leite daraus EINE konkrete Regel" in system:
            return regel
        if "Fasse die Antwort" in system:
            return kurz
        return "?"
    return _mit_grok(route)


def test_freitext_rueckfrage_wird_regel():
    _reset()
    regeln = []

    async def fake_save(user_id, text, **kw):
        regeln.append((text, kw))
        return "pid"

    orig = (sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields,
            sc.qdrant.save_coach_regel, sc.grok.simple)
    try:
        p = _mit_profil({"gefragt_am": _iso(1), "anzahl": 1, "antwort_typ": "aufgaben",
                         "antwort_am": _iso(0.9)})
        sc.qdrant.save_coach_regel = fake_save
        sc.grok.simple = _grok_router(regel="Schlag ihr kürzere Aufgaben unter 10 Minuten vor.")
        state.set_mode(DOM, sc.MODE)
        state.get(DOM)["stille_rueckfrage"] = "aufgaben"
        u = _msg_update("die dauern alle ewig, ich hab abends keine stunde")
        asyncio.run(sc.handle(u, None))
        assert regeln and regeln[0][1]["typ"] == "regel" and regeln[0][1]["quelle"] == "stille_checkin"
        assert regeln[0][1]["status"] == "aktiv"
        assert state.get_mode(DOM) == "chat" and "stille_rueckfrage" not in state.get(DOM)
        assert p.daten["stille_checkin"]["antwort_kurz"].startswith("Sie hat")
        # Inhalt der Antwort landet NICHT im Regel-Kontext (nur Muster-Label)
        assert "ewig" not in regeln[0][1]["kontext"]
        # nervt → Notiz statt Regel
        regeln.clear()
        state.set_mode(DOM, sc.MODE)
        state.get(DOM)["stille_rueckfrage"] = "nervt"
        asyncio.run(sc.handle(_msg_update("die quizfragen abends"), None))
        assert regeln and regeln[0][1]["typ"] == "notiz"
    finally:
        (sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields,
         sc.qdrant.save_coach_regel, sc.grok.simple) = orig
        _reset()


def test_freitext_klassen():
    _reset()
    from bot.handlers import domina as _dom
    orig = (sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields,
            sc.qdrant.save_coach_regel, sc.grok.simple, _dom.handle)
    weitergeleitet = []

    async def fake_domina_handle(update, context):
        weitergeleitet.append(update.message.text)

    async def fake_save(user_id, text, **kw):
        return "pid"

    try:
        sc.qdrant.save_coach_regel = fake_save
        _dom.handle = fake_domina_handle
        # ANDERES → Coach-Chat, Frage bleibt offen (Mode unverändert)
        p = _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        sc.grok.simple = _grok_router(klasse="ANDERES")
        state.set_mode(DOM, sc.MODE)
        asyncio.run(sc.handle(_msg_update("kannst du ihm den wochenplan schicken?"), None))
        assert weitergeleitet == ["kannst du ihm den wochenplan schicken?"]
        assert state.get_mode(DOM) == sc.MODE and "antwort_am" not in p.daten["stille_checkin"]
        # KEINE_ZEIT → Ruhe, verbucht, Mode zu
        sc.grok.simple = _grok_router(klasse="KEINE_ZEIT")
        u = _msg_update("sorry, auf arbeit ist gerade die hölle los")
        asyncio.run(sc.handle(u, None))
        assert state.coach_ruhe()["modus"] == "ruhe"
        assert p.daten["stille_checkin"]["antwort_typ"] == "keine_zeit"
        assert state.get_mode(DOM) == "chat"
        # OHNE_BOT aus Freitext schaltet NICHT selbst, sondern bietet an
        _reset()
        p = _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        sc.grok.simple = _grok_router(klasse="OHNE_BOT")
        state.set_mode(DOM, sc.MODE)
        u = _msg_update("wir machen das gerade lieber direkt")
        asyncio.run(sc.handle(u, None))
        assert state.coach_ruhe() is None, "Freitext darf den Zuschauer-Modus nicht selbst schalten"
        assert u.message.reply_text.await_args.kwargs.get("reply_markup") is not None
        # Klassifikations-Ausfall → SONSTIGES (keine Schaltung, Dank)
        _reset()
        _mit_profil({"gefragt_am": _iso(1), "anzahl": 1})
        sc.grok.simple = _mit_grok(RuntimeError("x"))
        state.set_mode(DOM, sc.MODE)
        asyncio.run(sc.handle(_msg_update("ach, weiß auch nicht"), None))
        assert state.coach_ruhe() is None and state.get_mode(DOM) == "chat"
        # abbrechen
        state.set_mode(DOM, sc.MODE)
        asyncio.run(sc.handle(_msg_update("abbrechen"), None))
        assert state.get_mode(DOM) == "chat"
    finally:
        (sc.qdrant.get_user_profile, sc.qdrant.patch_profile_fields,
         sc.qdrant.save_coach_regel, sc.grok.simple, _dom.handle) = orig
        _reset()


# --------------------------------------------------------------------------
# /einstellungen → 9 und /stats
# --------------------------------------------------------------------------

def test_einstellung_anwenden():
    _reset()
    try:
        assert sc.einstellung_anwenden("ruhe") is True
        r = state.coach_ruhe()
        assert r["modus"] == "ruhe"
        assert timedelta(days=13, hours=23) < datetime.fromisoformat(r["bis"]) - JETZT
        assert sc.einstellung_anwenden("ruhe 7") is True
        assert datetime.fromisoformat(state.coach_ruhe()["bis"]) - JETZT < timedelta(days=7, hours=1)
        assert sc.einstellung_anwenden("ruhe x") is False
        assert sc.einstellung_anwenden("Zuschauer") is True
        assert state.coach_ruhe()["modus"] == "zuschauer"
        assert "Zuschauer" in sc.ruhe_status_text()
        assert sc.einstellung_anwenden("quatsch") is False
        assert sc.einstellung_anwenden("-") is True
        assert state.coach_ruhe() is None and sc.ruhe_status_text() == ""
    finally:
        _reset()


def test_status_zeilen_ohne_inhalt():
    _reset()
    orig = sc.qdrant.get_user_profile

    async def fake_profile(user_id):
        return {"stille_checkin": {"gefragt_am": _iso(3), "antwort_am": _iso(2),
                                   "antwort_typ": "aufgaben",
                                   "antwort_kurz": "GEHEIMER INHALT"}}
    try:
        sc.qdrant.get_user_profile = fake_profile
        state.set_coach_ruhe("zuschauer")
        zeilen = asyncio.run(sc.status_zeilen())
        assert len(zeilen) == 2 and "beantwortet" in zeilen[1]
        assert "GEHEIMER" not in " ".join(zeilen) and "aufgaben" not in " ".join(zeilen)
    finally:
        sc.qdrant.get_user_profile = orig
        _reset()


def _run():
    test_state_persistenz_und_ablauf()
    test_flow_aktiv_gate_nur_dom_chat()
    test_gleiche_phase()
    test_job_phasen()
    test_frage_text_fallback_und_abnahme()
    test_frage_stellen_toctou_und_mode()
    test_buttons_reaktionen_und_doppeltap()
    test_freitext_rueckfrage_wird_regel()
    test_freitext_klassen()
    test_einstellung_anwenden()
    test_status_zeilen_ohne_inhalt()
    print("✅ Alle Stille-Check-in-Tests bestanden")


if __name__ == "__main__":
    _run()
