"""
Regressions-Tests für das Lern-System.

Schützt insbesondere die Bugs, die am 2026-06-06 gefunden wurden:
  - `stimmung`-NameError in gefuehl._update_sklave_persoenlichkeit
    (das gesamte Lern-Update gab still None zurück → Profil wurde nie aktualisiert)
  - giftiger Feedback-Loop durch fehlende Grok-Kategorie-Klassifikation
  - Tag-Flip bei umgekehrter Reaktions-Ratio

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_lern_system.py
    # oder, falls pytest installiert:
    pytest tests/test_lern_system.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Falls die echten Telegram/Qdrant-Deps fehlen (lokal), durch Stubs ersetzen.
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

from bot.handlers import gefuehl  # noqa: E402
from bot.handlers import kette_adaptiv  # noqa: E402
from bot.handlers import serie_handler  # noqa: E402
from bot.handlers import wochenplanung  # noqa: E402
from bot.handlers import sklave as _sklave_h  # noqa: E402
from bot.handlers import dossier as _dossier  # noqa: E402
from bot.prompts import sklave as _sklave_p  # noqa: E402
from bot.handlers import followup_response as _fur  # noqa: E402
from bot.prompts import persona as _persona  # noqa: E402
from bot.prompts import coach_persona as _coach_p  # noqa: E402
from bot.services import persona_config as _pc  # noqa: E402
from bot.services import kategorie_logik  # noqa: E402
from bot.services import grok as _grok  # noqa: E402
from bot import state as _state_mod  # noqa: E402
from bot import config as _config  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"

def _aw(fn):
    """Wrappt ein sync-Lambda als async (qdrant-Funktionen sind jetzt async)."""
    async def _w(*a, **k):
        return fn(*a, **k)
    return _w



async def test_lernkern_schreibt_profil():
    """Regression: _update_sklave_persoenlichkeit muss kategorie_reaktionen schreiben
    und die Analyse zurückgeben (nicht None durch verschluckten NameError)."""
    captured = {}

    async def fake_patch(user_id, fields):
        captured.update(fields)

    async def fake_grok(prompt, **kwargs):  # **kwargs: akzeptiert temperature=/reasoning= etc.
        return '{"stimmung":"begeistert","intensitaet":"hoch","kategorie_reaktion":"mag_sehr"}'

    gefuehl.qdrant.get_user_profile = _aw(lambda uid: {"kategorie_reaktionen": {}, "persoenlichkeit_tags": []})
    gefuehl.qdrant.patch_profile_fields = fake_patch
    gefuehl.grok.simple = fake_grok

    res = await gefuehl._update_sklave_persoenlichkeit(
        "Trag den Plug", "war intensiv", {"kategorie": "Pegging", "qdrant_point_id": "p1"}
    )
    assert res is not None, "Regression: gibt None zurück (NameError wieder da)"
    assert res["stimmung"] == "begeistert"
    assert captured["kategorie_reaktionen"]["Pegging"]["positiv"] == 1


async def test_tag_flip_bei_umkehr():
    """mag_X muss zu mag_nicht_X kippen, wenn die Reaktions-Ratio sich umkehrt."""
    prof = {
        "kategorie_reaktionen": {"Schmerz": {"positiv": 0, "neutral": 0, "negativ": 2}},
        "persoenlichkeit_tags": ["mag_Schmerz"],
    }
    captured = {}

    async def fake_patch(uid, fields):
        captured.update(fields)

    async def fake_grok(prompt, **kwargs):  # **kwargs: akzeptiert temperature=/reasoning= etc.
        return '{"stimmung":"abgelehnt","intensitaet":"niedrig","kategorie_reaktion":"mag_nicht"}'

    gefuehl.qdrant.get_user_profile = _aw(lambda uid: prof)
    gefuehl.qdrant.patch_profile_fields = fake_patch
    gefuehl.grok.simple = fake_grok

    await gefuehl._update_sklave_persoenlichkeit(
        "Nadeln", "nein danke", {"kategorie": "Schmerz", "qdrant_point_id": "p2"}
    )
    tags = captured["persoenlichkeit_tags"]
    assert "mag_nicht_Schmerz" in tags and "mag_Schmerz" not in tags, tags


async def test_klassifikation_keyword_und_fallback():
    """Keyword-Match direkt; sonst Grok-Fallback, hart gegen config validiert."""
    assert kategorie_logik.keyword_match("Trag den buttplug") == "Buttplug_Tragen"

    async def fake_grok(prompt, **kwargs):  # **kwargs: akzeptiert temperature= etc.
        return "Pet_Play"

    kategorie_logik.grok.simple = fake_grok
    r = await kategorie_logik.klassifiziere("Sei heute brav an meiner Seite")
    assert r in kategorie_logik.config.AUFGABEN_KATEGORIEN

    async def fake_grok_phantom(prompt, **kwargs):
        return "ErfundeneKategorie123"

    kategorie_logik.grok.simple = fake_grok_phantom
    r2 = await kategorie_logik.klassifiziere("vager text ohne jedes keyword qwertz")
    assert r2 == "allgemein", f"Phantom-Kategorie nicht abgefangen: {r2}"


def test_gewichtete_auswahl_schliesst_dislikes_aus():
    """Dislikes dürfen nie gezogen werden; Wunsch wird bevorzugt."""
    prof = {
        "wunsch_kategorien": ["Pegging"],
        "kategorie_reaktionen": {"Dienst": {"positiv": 0, "neutral": 0, "negativ": 4}},
    }
    from collections import Counter
    c = Counter()
    for _ in range(1000):
        c.update(kategorie_logik.gewichtete_auswahl(prof, letzte_kategorien=[], count=3))
    assert c.get("Dienst", 0) == 0, "Dislike-Kategorie wurde gezogen"
    assert c.get("Pegging", 0) > 0, "Wunsch-Kategorie nie gezogen"


def test_gewichtete_auswahl_mix_60_30_10():
    """Pro Slot ~60% Basis (Wunsch/Top), ~30% Exploration (Cluster-Nachbarn),
    ~10% Wildcard (Rest des Pools). Statistisch mit breiter Toleranz (±3σ ≈ ±0.03)."""
    prof = {"wunsch_kategorien": ["Pegging"]}
    nachbarn = kategorie_logik.KATEGORIE_NACHBARN["Pegging"]
    from collections import Counter
    c = Counter()
    n = 4000
    for _ in range(n):
        kat = kategorie_logik.gewichtete_auswahl(prof, letzte_kategorien=[], count=1)[0]
        if kat == "Pegging":
            c["basis"] += 1
        elif kat in nachbarn:
            c["exploration"] += 1
        else:
            c["wildcard"] += 1
    assert 0.54 < c["basis"] / n < 0.66, dict(c)
    assert 0.24 < c["exploration"] / n < 0.36, dict(c)
    assert 0.05 < c["wildcard"] / n < 0.15, dict(c)


def test_gewichtete_auswahl_ohne_vorlieben():
    """Ohne bekannte Vorlieben sind Basis und Exploration leer – die Auswahl läuft
    komplett über Wildcard (reine Exploration), ohne Duplikate."""
    out = kategorie_logik.gewichtete_auswahl({}, count=3)
    assert len(out) == 3 and len(set(out)) == 3, out
    assert all(k in kategorie_logik.config.AUFGABEN_KATEGORIEN for k in out), out


def test_progressive_level_logik():
    """begeistert/langweilig -> +1 (cap 3), überfordert -> -1 (floor 1), sonst gleich."""
    assert kategorie_logik.naechstes_level(2, "begeistert") == 3
    assert kategorie_logik.naechstes_level(2, "langweilig") == 3
    assert kategorie_logik.naechstes_level(3, "begeistert") == 3
    assert kategorie_logik.naechstes_level(2, "überfordert") == 1
    assert kategorie_logik.naechstes_level(1, "überfordert") == 1
    assert kategorie_logik.naechstes_level(2, "positiv") == 2
    assert kategorie_logik.kategorie_level({}, "X") == kategorie_logik.LEVEL_DEFAULT


async def test_gefuehl_schreibt_kategorie_level():
    """_update_sklave_persoenlichkeit muss kategorie_level fortschreiben (begeistert: 2->3)."""
    captured = {}

    async def fake_patch(uid, fields):
        captured.update(fields)

    async def fake_grok(prompt, **kwargs):  # **kwargs: akzeptiert temperature=/reasoning= etc.
        return '{"stimmung":"begeistert","intensitaet":"hoch","kategorie_reaktion":"mag_sehr"}'

    gefuehl.qdrant.get_user_profile = _aw(lambda uid: {"kategorie_reaktionen": {}, "persoenlichkeit_tags": [], "kategorie_level": {}})
    gefuehl.qdrant.patch_profile_fields = fake_patch
    gefuehl.grok.simple = fake_grok

    await gefuehl._update_sklave_persoenlichkeit(
        "Plug", "geil", {"kategorie": "Pegging", "qdrant_point_id": "p1"}
    )
    assert captured["kategorie_level"]["Pegging"] == 3, captured.get("kategorie_level")


def test_prompt_enthaelt_level_hinweis():
    """Der Tiny-Task-Prompt muss den Kategorie-Level-Hinweis durchreichen."""
    from bot.prompts import followup as _fp
    system, user = _fp.tiny_task_vorschlag(
        erfahrungsstand="erfahren", level=2, interessen=[], sklave_vorlieben=[],
        sklave_hard_limits=[], kategorie_level_hinweis="LEVELHINWEIS_XYZ",
    )
    assert "LEVELHINWEIS_XYZ" in user


async def test_adaptive_kette_vorschlag():
    """schlage_vor: generiert Anpassung, speichert sie, schickt der Domina Buttons -> True."""
    gespeichert = {}
    kette_adaptiv.qdrant.get_user_profile = _aw(lambda u: {"hard_limits": [], "grenzen": []})
    kette_adaptiv.qdrant.update_task = _aw(lambda tid, fields: gespeichert.update({tid: fields}))
    kette_adaptiv.grok.simple = AsyncMock(return_value="Eine schärfere Variante.")
    kette_adaptiv.limits_check.verletzungen = _aw(lambda t, a, b, **k: [])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    naechster = {"qdrant_point_id": "t2", "aufgabe": "Original", "kette_position": 2, "kette_gesamt": 3}
    res = await kette_adaptiv.schlage_vor(bot, naechster, "war langweilig", "langweilig")
    assert res is True
    assert gespeichert["t2"]["kette_anpass_vorschlag"] == "Eine schärfere Variante."
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.kwargs["reply_markup"] is not None


def _fake_callback(data):
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    upd = MagicMock()
    upd.callback_query = q
    return upd


async def _callback_setzt(action: str, erwartet: str):
    cap = {}
    kette_adaptiv.qdrant.get_task = _aw(lambda tid: {
        "qdrant_point_id": "t2", "aufgabe": "ALT", "kette_anpass_vorschlag": "NEU",
        "kette_position": 2, "kette_gesamt": 3,
        # Der Doppel-Tap-Guard (Review D8/M6) lässt nur noch wartende Glieder zu.
        "status": "kette_wartend",
    })
    kette_adaptiv.qdrant.update_task = _aw(lambda tid, fields: cap.update(fields))
    # kette_adaptiv.state IST das bot.state-Modul – Patch ohne Restore würde
    # set_followup_task für ALLE folgenden Tests lahmlegen (Review D5).
    _orig_sft = kette_adaptiv.state.set_followup_task
    kette_adaptiv.state.set_followup_task = lambda *a, **k: None
    kette_adaptiv.grok.simple = AsyncMock(return_value="Befehl")
    kette_adaptiv.telegram_helper.send_sklave = AsyncMock()
    ctx = MagicMock()
    ctx.bot = MagicMock()
    try:
        await kette_adaptiv.callback(_fake_callback(f"ketteanpass:{action}:t2"), ctx)
    finally:
        kette_adaptiv.state.set_followup_task = _orig_sft
    assert cap["aufgabe"] == erwartet and cap["status"] == "offen", cap


async def test_adaptive_kette_callbacks():
    await _callback_setzt("approve", "NEU")  # Anpassung übernehmen
    await _callback_setzt("keep", "ALT")     # Original behalten

    # Doppel-Tap-Guard (Review D8/M6): bereits entschiedenes Glied (status !=
    # kette_wartend) darf NICHT wieder auf offen gesetzt und erneut gesendet werden.
    cap = {}
    kette_adaptiv.qdrant.get_task = _aw(lambda tid: {
        "qdrant_point_id": "t2", "aufgabe": "ALT", "status": "erledigt",
    })
    kette_adaptiv.qdrant.update_task = _aw(lambda tid, fields: cap.update(fields))
    kette_adaptiv.telegram_helper.send_sklave = AsyncMock()
    await kette_adaptiv.callback(_fake_callback("ketteanpass:approve:t2"), MagicMock())
    assert not cap, f"Doppel-Tap setzte entschiedenes Glied zurück: {cap}"
    assert kette_adaptiv.telegram_helper.send_sklave.await_count == 0


def test_serie_variationen_parse():
    """Nummerierte/Bullet-Tagesaufgaben parsen; Mismatch -> None (Fallback)."""
    v = serie_handler._parse_variationen("1. Eins\n2. Zwei\n3. Drei", 3)
    assert v == ["Eins", "Zwei", "Drei"]
    assert serie_handler._parse_variationen("nur eine", 3) is None
    assert serie_handler._parse_variationen("- a\n- b", 2) == ["a", "b"]


def test_wochenplan_parse():
    """Wochenplan-Text in Tageseinträge zerlegen; Kategorie-Brackets entfernen."""
    plan = (
        "🗓 Wochenplan\n\n"
        "Montag – Pegging\nAufgabe: Trag den Plug.\nWarum: x.\n\n"
        "Dienstag – [Spanking]\nAufgabe: 20 Schläge.\n"
    )
    e = wochenplanung._parse_wochenplan(plan)
    assert len(e) == 2
    assert e[0]["aufgabe"] == "Trag den Plug."
    assert e[1]["kategorie"] == "Spanking"
    assert wochenplanung._normalisiere_kategorie(e[0]) == "Buttplug_Tragen"


async def test_llm_fallback():
    """_try_fallback: aus -> None; an -> Antwort mit ersetztem Modell; Fehler -> None."""
    payload = {"model": "grok", "messages": [], "temperature": 0.7}
    _config.FALLBACK_LLM_URL = ""
    assert await _grok._try_fallback(payload, "grok") is None

    _config.FALLBACK_LLM_URL = "http://fb/v1/chat/completions"
    _config.FALLBACK_LLM_KEY = ""
    _config.FALLBACK_LLM_MODEL = "llama3.1"
    erfasst = {}

    async def fake_post(url, headers, pl, timeout=None):
        erfasst["model"] = pl["model"]
        erfasst["timeout"] = timeout
        return "FB"

    _grok._post_chat = fake_post
    assert await _grok._try_fallback(payload, "grok") == "FB"
    assert erfasst["model"] == "llama3.1"
    # 16.08.2026: Der Fallback MUSS sein eigenes, groesseres Timeout mitgeben.
    # Erbte er das knappe LLM_TIMEOUT des Primaer-Providers, waere er bei genau
    # den grossen Prompts nutzlos, fuer die er gedacht ist (Wochenplan).
    assert erfasst["timeout"] == _config.FALLBACK_LLM_TIMEOUT

    async def boom(*a, **k):
        raise RuntimeError("down")

    _grok._post_chat = boom
    assert await _grok._try_fallback(payload, "grok") is None


def test_state_persistenz():
    """message_history + Pause-Flag überleben einen simulierten Neustart via STATE_FILE."""
    import tempfile
    _config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
    _state_mod._state.clear()
    _state_mod.add_message("777", "user", "hallo")
    _state_mod.add_message("777", "assistant", "hi")
    _state_mod.set_paused(True)
    assert os.path.exists(_config.STATE_FILE)

    _state_mod._state.clear()  # "Neustart"
    _state_mod.load_persisted()
    assert _state_mod.get_history("777") == [
        {"role": "user", "content": "hallo"},
        {"role": "assistant", "content": "hi"},
    ]
    assert _state_mod.is_paused() is True
    _state_mod._state.clear()
    _state_mod.set_paused(False)


def test_sklave_prompt_wissen():
    """Sklaven-Prompt bettet gelerntes Wissen + Dossier ein, Tags lesbar."""
    out = _sklave_p.get(
        hard_limits=["blut"], vorlieben=["lob"], domina_grenzen=[],
        persoenlichkeit_tags=["mag_Pegging", "mag_nicht_Dienst"],
        mag_kategorien=["Pegging"], dislike_kategorien=["Dienst"],
        wunsch_kategorien=["Toiletten_Sklave"], intensitaet_hinweis="Pegging: hoch",
        letzte_gefuehle=["Pegging: war intensiv"], stimmung="angespannt",
        streak=4, punkte=120, dossier="Er sehnt sich nach Kontrolle.",
    )
    assert "WAS DU ÜBER IHN WEISST" in out
    assert "mag Pegging" in out and "mag nicht Dienst" in out
    assert "Toiletten_Sklave" in out and "Streak 4" in out
    assert "Er sehnt sich nach Kontrolle." in out


def test_sklave_themen():
    assert "gefühl" in _sklave_h._themen("ich fühle mich geil und stolz")
    assert _sklave_h._wichtige_punkte("Eins. Zwei. Drei.") == ["Eins.", "Zwei."]


async def test_dossier_build():
    """Dossier wird aus Profil/Gefühlen synthetisiert und im Profil gespeichert."""
    _dossier.qdrant.get_user_profile = _aw(lambda u: {
        "vorlieben": ["lob"], "persoenlichkeit_tags": ["mag_Pegging"],
        "kategorie_reaktionen": {"Pegging": {"positiv": 3, "neutral": 0, "negativ": 0}},
        "kategorie_level": {"Pegging": 3}, "wunsch_kategorien": ["Toiletten_Sklave"],
    })
    _dossier.qdrant.get_tasks_by_status = _aw(lambda s, sort_by_datum=False: [
        {"kategorie": "Pegging", "gefuehl": "war intensiv"},
    ])
    _dossier.qdrant.get_latest_stimmung = _aw(lambda u: {"zusammenfassung": "zufrieden"})
    _dossier.qdrant.get_hybrid_conversation_context = _aw(lambda u, qv, limit=8: [])
    _dossier.emb.get_embedding = AsyncMock(return_value=[0.1] * 768)
    _dossier.grok.simple = AsyncMock(return_value="Er ist hingebungsvoll.")
    cap = {}

    async def fake_patch(uid, fields):
        cap.update(fields)

    _dossier.qdrant.patch_profile_fields = fake_patch
    txt = await _dossier.aktualisiere_dossier()
    assert txt == "Er ist hingebungsvoll." and cap.get("dossier") == txt

    _dossier.qdrant.get_user_profile = _aw(lambda u: {})
    _dossier.qdrant.get_tasks_by_status = _aw(lambda s, sort_by_datum=False: [])
    assert await _dossier.aktualisiere_dossier() is None


def test_namen_persona():
    """Bot-Name + Sklaven-Anrede aus persona_config landen in beiden Personas; ohne -> namenlos."""
    _pc._cache.update({"bot_name": "", "sklave_anrede": ""})
    p0 = _persona.fuer_sklaven_prompt()
    assert "namenlos" in p0 and "deine Herrin" in p0
    _pc._cache.update({"bot_name": "Herrin Elara", "sklave_anrede": "Spielzeug"})
    p1 = _persona.fuer_sklaven_prompt()
    assert "Herrin Elara" in p1 and 'als "Spielzeug"' in p1 and "namenlos" not in p1
    assert "Herrin Elara" in _coach_p.fuer_coach_prompt()
    assert "Herrin Elara" in _coach_p.fuer_aufgaben_vorschlag()
    _pc._cache.update({"bot_name": "", "sklave_anrede": ""})  # cleanup


def test_decay_reaktionen():
    r = {"Pegging": {"positiv": 3, "neutral": 0, "negativ": 0, "begeistert_count": 2},
         "Anal": {"positiv": 1, "neutral": 0, "negativ": 0}}
    d = kategorie_logik.decay_reaktionen(r, amount=1)
    assert d["Pegging"]["positiv"] == 2 and d["Pegging"]["begeistert_count"] == 1
    assert "Anal" not in d


async def test_offene_faeden():
    _dossier.emb.get_embedding = AsyncMock(return_value=[0.1] * 768)
    _dossier.qdrant.get_hybrid_conversation_context = _aw(lambda u, qv, limit=8: [{"zusammenfassung": "Sklave: Stress."}])
    _dossier.qdrant.get_tasks_by_status = _aw(lambda s, sort_by_datum=False: [{"kategorie": "Pegging", "gefuehl": "x"}])
    cap = {}

    async def fp(uid, fields):
        cap.update(fields)

    _dossier.qdrant.patch_profile_fields = fp
    _dossier.grok.simple = AsyncMock(return_value="Stress bei der Arbeit\nWunsch nach Pegging")
    faeden = await _dossier.aktualisiere_offene_faeden()
    assert faeden == ["Stress bei der Arbeit", "Wunsch nach Pegging"]
    assert cap.get("offene_faeden") == faeden


async def test_domina_dossier():
    _dossier.emb.get_embedding = AsyncMock(return_value=[0.1] * 768)
    _dossier.qdrant.get_user_profile = _aw(lambda u: {"interessen": ["X"], "ziele": "Y", "erfahrungsstand": "Z", "aktuelles_level": 3})
    _dossier.qdrant.get_bewertungs_kontext = _aw(lambda u: "")
    _dossier.qdrant.get_recent_task_kategorien = _aw(lambda u, limit=8: [])
    _dossier.qdrant.get_hybrid_conversation_context = _aw(lambda u, qv, limit=8: [])
    cap = {}

    async def fp(uid, fields):
        cap.update(fields)

    _dossier.qdrant.patch_profile_fields = fp
    _dossier.grok.simple = AsyncMock(return_value="Sie führt streng.")
    dt = await _dossier.aktualisiere_domina_dossier()
    assert dt == "Sie führt streng." and cap.get("domina_dossier") == dt


async def test_wunsch_erfassung():
    """erfasse_wunsch_aus_chat: gated über Signalwort, speichert, dedupliziert, hard-limit-gefiltert."""
    from bot.services import limits_check as _lc
    _lc.verletzungen = _aw(lambda t, a, b, **k: [])
    cap = {}

    async def fp(uid, fields):
        cap.update(fields)

    _dossier.qdrant.patch_profile_fields = fp
    _dossier.qdrant.get_user_profile = _aw(lambda u: ({"hard_limits": [], "entdeckte_wuensche": []} if u == "sklave" else {"grenzen": []}))

    # ohne Signalwort -> kein Grok, None
    _dossier.grok.simple = AsyncMock(side_effect=AssertionError("Grok darf nicht laufen"))
    assert await _dossier.erfasse_wunsch_aus_chat("schönes Wetter heute") is None

    # mit Signalwort -> extrahiert + gespeichert
    _dossier.grok.simple = AsyncMock(return_value="würde gern mal Wachs ausprobieren")
    w = await _dossier.erfasse_wunsch_aus_chat("ich würde gerne mal Wachs ausprobieren")
    assert w == "würde gern mal Wachs ausprobieren" and cap["entdeckte_wuensche"] == [w]

    # Grok 'KEINE' -> None
    _dossier.grok.simple = AsyncMock(return_value="KEINE")
    assert await _dossier.erfasse_wunsch_aus_chat("ich hätte gern Ruhe") is None


async def test_apply_profile_patch_limit_add():
    """limit_add: No-Go landet ADD-ONLY im Grenzen-Feld der Rolle; Vorliebe geht den
    normalen Patch-Pfad. Falsches Feld für limit_add wird ignoriert (nie geschrieben)."""
    from bot.services import qdrant as _q
    calls = {"limits": [], "patch": []}

    _q.get_user_profile = _aw(lambda uid: {"user_id": uid, "hard_limits": ["Atemkontrolle"], "vorlieben": ["X"]})

    async def fake_append(uid, feld, werte):
        calls["limits"].append((uid, feld, list(werte)))
        bestand = ["Atemkontrolle"]
        return [w for w in werte if w not in bestand]  # add-only Dedup

    async def fake_patch(uid, fields):
        calls["patch"].append((uid, dict(fields)))
        return "pid"

    _q.append_profile_limits = fake_append
    _q.patch_profile_fields = fake_patch

    patch = {"changes": [
        {"feld": "hard_limits", "operation": "limit_add", "wert": ["Nadeln", "Atemkontrolle"]},
        {"feld": "vorlieben", "operation": "list_add", "wert": ["langsame Steigerung"]},
    ]}
    bericht = await _q.apply_profile_patch("sklave", patch)
    assert calls["limits"], "limit_add muss append_profile_limits aufrufen"
    assert calls["limits"][0][1] == "hard_limits" and "Nadeln" in calls["limits"][0][2]
    assert any("hard_limits" in a for a in bericht["angewandt"]), bericht
    assert calls["patch"] and "vorlieben" in calls["patch"][0][1]

    # limit_add auf ein NICHT-Grenzen-Feld → ignoriert, append wird NICHT aufgerufen
    calls["limits"].clear()
    patch2 = {"changes": [{"feld": "vorlieben", "operation": "limit_add", "wert": ["x"]}]}
    bericht2 = await _q.apply_profile_patch("sklave", patch2)
    assert not calls["limits"], "limit_add darf nur das Grenzen-Feld der Rolle schreiben"
    assert any("nicht erlaubt" in i for i in bericht2["ignoriert"]), bericht2


async def test_praeferenz_detektor():
    """Detektor: Gating + Dedup-Helfer, und end-to-end Mapping Vorliebe→list_add,
    No-Go→limit_add mit Vorschlag an den Sklaven. Grenzverletzende Vorlieben raus."""
    from bot.services import praeferenz_detektor as pd

    # Gating
    assert pd._gated("ich hasse das")            # Signalwort
    assert pd._gated("a" * 120)                   # Längen-Fallback
    assert not pd._gated("ok danke")
    # Dedup
    assert pd._ist_neu("Nadeln", ["Atemkontrolle"])
    assert not pd._ist_neu("nadeln", ["Nadeln"])

    pd.grok.simple = _aw(lambda *a, **k: '{"vorlieben":["langsame Steigerung","Würgen"],"nogos":["Atemkontrolle"]}')
    pd.qdrant.get_user_profile = _aw(lambda uid: {"vorlieben": [], "hard_limits": []})
    # "Würgen" ist grenzverletzend → muss als Vorliebe rausgefiltert werden
    pd.limits_check.verletzungen = _aw(lambda v, a=None, b=None, **k: [{"limit": "x"}] if "würgen" in v.lower() else [])

    saved = {}
    async def fake_save(**kwargs):
        saved.update(kwargs)
        return "pid-1"
    pd.qdrant.save_coach_regel = fake_save

    from bot.services import telegram_helper as _th
    from bot.handlers import coach_regeln as _cr
    sent = {}
    _th.send_sklave = _aw(lambda bot, text, **k: sent.update(text=text))
    _th.send_domina = _aw(lambda bot, text, **k: sent.update(domina=text))
    _cr.vorschlag_buttons = lambda pid: {"btn": pid}

    ok = await pd.erkenne_und_schlage_vor(object(), "sklave",
                                          "ich steh total auf langsame Steigerung, aber atemkontrolle ist ein no-go")
    assert ok is True
    assert saved["profile_user"] == "sklave" and saved["quelle"] == "chat_praeferenz"
    felder = {(c["feld"], c["operation"]) for c in saved["profile_patch"]["changes"]}
    assert ("vorlieben", "list_add") in felder
    assert ("hard_limits", "limit_add") in felder
    # grenzverletzende Vorliebe "Würgen" darf NICHT im Patch sein
    vorlieben_werte = [w for c in saved["profile_patch"]["changes"] if c["feld"] == "vorlieben" for w in c["wert"]]
    assert "langsame Steigerung" in vorlieben_werte and "Würgen" not in vorlieben_werte
    # Vorschlag ging an den Sklaven, nicht die Domina (das alte
    # `assert ... or True` hier war eine Tautologie und prüfte nichts)
    assert "domina" not in sent and "langsame Steigerung" in sent["text"]

    # Nichts Neues (alles schon im Profil) → kein Vorschlag
    pd.qdrant.get_user_profile = _aw(lambda uid: {"vorlieben": ["langsame Steigerung"], "hard_limits": ["Atemkontrolle"]})
    pd.grok.simple = _aw(lambda *a, **k: '{"vorlieben":["langsame Steigerung"],"nogos":["Atemkontrolle"]}')
    saved.clear()
    ok2 = await pd.erkenne_und_schlage_vor(object(), "sklave", "ich mag langsame Steigerung")
    assert ok2 is False and not saved


def test_limits_check_ausnahme_annotation():
    """Ausnahme-Annotation (limit_refine): Basis-Begriff muss weiter matchen,
    Ausnahme-Wörter dürfen KEINE fremden Synonym-Listen aktivieren."""
    from bot.services import limits_check as lc

    t1 = lc._prufe_liste(lc._normalisiere("wir treffen uns im Park"),
                         ["Öffentlichkeit (Ausnahme: Plug tragen)"], "sklave_hard_limit")
    assert t1, "Basis-Begriff muss trotz Annotation weiter matchen"

    # Ohne Basis-Extraktion würde 'Analplug' den Synonym-Key 'anal' aktivieren
    # und ALLES Anale als Öffentlichkeits-Verletzung melden.
    t2 = lc._prufe_liste(lc._normalisiere("Analspiele zuhause auf dem Sofa"),
                         ["Öffentlichkeit (Ausnahme: Analplug)"], "sklave_hard_limit")
    assert not t2, "Ausnahme-Wörter dürfen keine fremden Synonyme aktivieren"


async def test_refine_profile_limit_und_patch():
    """limit_refine: nur Grenzen-Feld, Basis-Begriff muss erhalten bleiben,
    Eintrag wird in-place ersetzt; apply_profile_patch routet alt/neu-Paare."""
    from bot.services import qdrant as _q

    # Falsches Feld → ohne DB-Zugriff abgelehnt
    assert await _q.refine_profile_limit("sklave", "vorlieben", "a", "a (Ausnahme: b)") is False
    # Basis-Begriff nicht in `neu` enthalten → abgelehnt (Check VOR jedem DB-Zugriff)
    _q._aio = AsyncMock(side_effect=AssertionError("kein DB-Zugriff erwartet"))
    assert await _q.refine_profile_limit("sklave", "hard_limits", "Öffentlichkeit", "Plug tragen") is False

    # Happy Path: Eintrag wird in-place ersetzt (set_payload), Vektor separat
    # (update_vectors) – KEIN Full-Upsert (Lost-Update-Fix, Review Durchgang 4).
    punkt = MagicMock()
    punkt.id = "p1"
    punkt.payload = {"user_id": "sklave", "hard_limits": ["Blut", "Öffentlichkeit"]}
    rcalls = {"set": [], "vec": []}

    async def fake_aio(fn, **kwargs):
        if "scroll_filter" in kwargs:
            return ([punkt], None)
        (rcalls["set"] if "payload" in kwargs else rcalls["vec"]).append(kwargs)
        return None

    _q._aio = fake_aio
    _q.emb.get_embedding = AsyncMock(return_value=[0.0])
    ok = await _q.refine_profile_limit(
        "sklave", "hard_limits", "Öffentlichkeit", "Öffentlichkeit (Ausnahme: Plug tragen)")
    assert ok is True
    assert rcalls["set"][0]["payload"]["hard_limits"] == \
        ["Blut", "Öffentlichkeit (Ausnahme: Plug tragen)"]
    assert rcalls["vec"], "Vektor muss separat via update_vectors geschrieben werden"
    # `alt` nicht im Bestand → False
    assert await _q.refine_profile_limit("sklave", "hard_limits", "Nadeln", "Nadeln (Ausnahme: x)") is False

    # apply_profile_patch routet limit_refine; falsches Feld wird ignoriert
    calls = []

    async def fake_refine(uid, feld, alt, neu):
        calls.append((uid, feld, alt, neu))
        return True

    _q.refine_profile_limit = fake_refine
    _q.get_user_profile = _aw(lambda uid: {"user_id": uid})
    patch = {"changes": [
        {"feld": "hard_limits", "operation": "limit_refine",
         "wert": {"alt": "Öffentlichkeit", "neu": "Öffentlichkeit (Ausnahme: Plug)"}},
        {"feld": "vorlieben", "operation": "limit_refine", "wert": {"alt": "a", "neu": "a b"}},
    ]}
    bericht = await _q.apply_profile_patch("sklave", patch)
    assert calls == [("sklave", "hard_limits", "Öffentlichkeit", "Öffentlichkeit (Ausnahme: Plug)")]
    assert any("✏️" in a for a in bericht["angewandt"]), bericht
    assert any("nicht erlaubt" in i for i in bericht["ignoriert"]), bericht


async def test_patch_profile_fields_kein_full_upsert():
    """Re-Embed-Pfad: Payload via set_payload, Vektor via update_vectors – nie
    Full-Upsert mit stale Read (würde parallel gepatchte punkte/streak verlieren)."""
    import importlib
    from bot.services import qdrant as _q
    _q = importlib.reload(_q)  # frühere Tests ersetzen patch_profile_fields durch Fakes
    punkt = MagicMock()
    punkt.id = "p1"
    punkt.payload = {"user_id": "sklave", "vorlieben": ["X"], "punkte": 42}
    calls = {"set": [], "vec": [], "upsert": []}

    async def fake_aio(fn, **kwargs):
        name = getattr(fn, "__name__", "") or str(fn)
        if "scroll" in name:
            return ([punkt], None)
        if "set_payload" in name:
            calls["set"].append(kwargs)
        elif "update_vectors" in name:
            calls["vec"].append(kwargs)
        else:
            calls["upsert"].append(kwargs)
        return None

    _q._aio = fake_aio
    _q.emb.get_embedding = AsyncMock(return_value=[0.0])

    await _q.patch_profile_fields("sklave", {"vorlieben": ["X", "Neu"]})
    assert calls["set"] and calls["set"][0]["payload"] == {"vorlieben": ["X", "Neu"]}
    assert calls["vec"], "vorlieben ändert den Vektor → update_vectors erwartet"
    assert not calls["upsert"], "kein Full-Upsert im Re-Embed-Pfad"

    # Feld ohne Vektor-Relevanz → nur set_payload, kein Embedding/update_vectors
    calls["set"].clear(); calls["vec"].clear()
    await _q.patch_profile_fields("sklave", {"punkte": 43})
    assert calls["set"] and not calls["vec"] and not calls["upsert"]


def test_zeiten_fenster_validierung():
    """Kinderfreie Zeiten: leere Fenster abgelehnt, Über-Nacht-Fenster GÜLTIG
    (Review D5: '21:00-06:00' = Kinder schlafen ist der häufigste Fall; die
    Fenster werden nur als Prompt-Text genutzt, kein Code vergleicht Uhrzeiten)."""
    from bot.services import zeiten
    assert zeiten.parse_kinderfreie_zeiten("07:00-08:00") == ["07:00-08:00"]
    assert zeiten.parse_kinderfreie_zeiten("22:00-21:00") == ["22:00-21:00"]
    assert zeiten.parse_kinderfreie_zeiten("21:00-06:00") == ["21:00-06:00"]
    assert zeiten.parse_kinderfreie_zeiten("20:00-20:00") is None
    assert zeiten.parse_kinderfreie_zeiten("25:00-26:00") is None


def test_profil_parse_liste():
    """Listen-Edit: kopierte Nummerierungs-Labels werden abgestreift, Kommas in
    Klammern trennen nicht (Regression: zerbrochene vorlieben/hard_limits)."""
    from bot.handlers import profil as _profil
    assert _profil._parse_liste("1️⃣ Absolute Grenzen: Nadeln, Blut, Öffentlichkeit") == \
        ["Nadeln", "Blut", "Öffentlichkeit"]
    assert _profil._parse_liste("2. Vorlieben: Wachsspiel, Fesselspiele (Seil, Manschetten, Spreizstange), Rollenspiel") == \
        ["Wachsspiel", "Fesselspiele (Seil, Manschetten, Spreizstange)", "Rollenspiel"]
    # Ohne Nummerierung bleibt ein Doppelpunkt-Eintrag unangetastet
    assert _profil._parse_liste("Wachs: nur warm, Blut") == ["Wachs: nur warm", "Blut"]


async def test_praeferenz_detektor_ausnahmen():
    """Detektor: explizite Ausnahme zu bestehendem No-Go → limit_refine-Vorschlag;
    unbekannte Grenze → annotiertes limit_add; schon annotiert → kein Vorschlag."""
    from bot.services import praeferenz_detektor as pd
    from bot.services import telegram_helper as _th
    from bot.handlers import coach_regeln as _cr

    pd.grok.simple = _aw(lambda *a, **k:
        '{"vorlieben":[],"nogos":[],"ausnahmen":[{"grenze":"Öffentlichkeit","ausnahme":"Plug tragen"}]}')
    pd.limits_check.verletzungen = _aw(lambda v, a=None, b=None, **k: [])
    saved, sent = {}, {}

    async def fake_save(**kwargs):
        saved.update(kwargs)
        return "pid-2"
    pd.qdrant.save_coach_regel = fake_save
    _th.send_sklave = _aw(lambda bot, text, **k: sent.update(text=text))
    _cr.vorschlag_buttons = lambda pid: None

    # Grenze existiert → limit_refine mit alt/neu
    pd.qdrant.get_user_profile = _aw(lambda uid:
        {"vorlieben": [], "hard_limits": ["Öffentlichkeit", "Blut"]} if uid == "sklave" else {"grenzen": []})
    ok = await pd.erkenne_und_schlage_vor(
        object(), "sklave", "öffentlichkeit ist ein no go aber ein plug tragen wäre ok")
    assert ok is True
    refines = [c for c in saved["profile_patch"]["changes"] if c["operation"] == "limit_refine"]
    assert refines and refines[0]["feld"] == "hard_limits"
    assert refines[0]["wert"] == [{"alt": "Öffentlichkeit", "neu": "Öffentlichkeit (Ausnahme: Plug tragen)"}]
    assert "präzisiert" in sent["text"]

    # Grenze noch nicht im Profil → direkt annotiertes limit_add
    pd.qdrant.get_user_profile = _aw(lambda uid:
        {"vorlieben": [], "hard_limits": []} if uid == "sklave" else {"grenzen": []})
    saved.clear()
    ok2 = await pd.erkenne_und_schlage_vor(
        object(), "sklave", "öffentlichkeit ist tabu, aber plug tragen wäre ok")
    adds = [c for c in saved["profile_patch"]["changes"] if c["operation"] == "limit_add"]
    assert ok2 is True and adds and adds[0]["wert"] == ["Öffentlichkeit (Ausnahme: Plug tragen)"]

    # Ausnahme steht schon im Eintrag → nichts Neues, kein Vorschlag
    pd.qdrant.get_user_profile = _aw(lambda uid:
        {"vorlieben": [], "hard_limits": ["Öffentlichkeit (Ausnahme: Plug tragen)"]} if uid == "sklave"
        else {"grenzen": []})
    saved.clear()
    ok3 = await pd.erkenne_und_schlage_vor(
        object(), "sklave", "öffentlichkeit ist no-go, aber plug tragen ok")
    assert ok3 is False and not saved


async def test_skill_kontext_block():
    """Wissens-Briefe in Generator-Prompts: kurzfassung bevorzugt, ⚠️-Sektion als
    Fallback, gezielte Kategorien vs. alle, best-effort (wirft nie)."""
    from bot.prompts import coach_persona as cp
    from bot.services import qdrant as _q

    skills = {
        "Pegging": {"kategorie": "Pegging", "kurzfassung": "Nie ohne Gleitgel; Stufen: Finger→Plug→Strapon",
                    "inhalt": "x"},
        "Wachs": {"kategorie": "Wachs", "kurzfassung": "",
                  "inhalt": "🧠 *Worum es geht*\nIntro\n\n⚠️ *Sicherheit & rote Linien*\n"
                            "- Nur Paraffin\n- Nie ins Gesicht\n\n📈 *Progression*\nStufen"},
    }
    _q.get_skill = _aw(lambda k: skills.get(k))
    _q.list_skills = _aw(lambda: list(skills.values()))

    block = await cp.skill_kontext_block(["Pegging", "Unbekannt"])
    assert "Pegging: Nie ohne Gleitgel" in block and "Unbekannt" not in block

    # Alt-Eintrag ohne kurzfassung → ⚠️-Sektion bis zur nächsten Brief-Überschrift
    block2 = await cp.skill_kontext_block(["Wachs"])
    assert "Nur Paraffin" in block2 and "Stufen" not in block2 and "Intro" not in block2

    # Ohne kategorien → alle vorhandenen Einträge (Arc/Wochenplan/Inspiration)
    block3 = await cp.skill_kontext_block()
    assert "Pegging" in block3 and "Wachs" in block3

    # Kein Eintrag / DB-Fehler → leerer String, blockiert keine Generierung
    _q.get_skill = _aw(lambda k: None)
    assert await cp.skill_kontext_block(["Pegging"]) == ""

    async def boom(k):
        raise RuntimeError("db down")
    _q.get_skill = boom
    assert await cp.skill_kontext_block(["Pegging"]) == ""


async def test_skill_kurzfassung_best_effort():
    """Kurzfassung wird beim Speichern erzeugt; Grok-Fehler ergibt leeren String
    (Speichern darf nie an der Kondensierung scheitern)."""
    from bot.handlers import skill as _skill
    _skill.grok.simple = AsyncMock(return_value="  Sicherheit: nie X; Progression: a→b  ")
    assert await _skill._kurzfassung("Wachs", "inhalt") == "Sicherheit: nie X; Progression: a→b"
    _skill.grok.simple = AsyncMock(side_effect=RuntimeError("down"))
    assert await _skill._kurzfassung("Wachs", "inhalt") == ""


async def test_kategorien_pool():
    """alle_kategorien: Katalog zuerst (Nummern-Stabilität) + eigene Kategorien
    dedupliziert hinten; Klassifikation akzeptiert eigene Kategorien."""
    from bot.services import kategorie_logik as kl

    profil = {"eigene_kategorien": ["Cuckold", "anal", "  "]}
    pool = kl.alle_kategorien(profil)
    assert pool[:len(_config.AUFGABEN_KATEGORIEN)] == list(_config.AUFGABEN_KATEGORIEN)
    assert "Cuckold" in pool
    assert "anal" not in pool and "Anal" in pool  # case-insensitive Dedupe gegen Katalog
    assert kl.alle_kategorien(None) == list(_config.AUFGABEN_KATEGORIEN)

    # klassifiziere: Grok nennt die eigene Kategorie → wird gegen den Pool validiert
    import bot.services.qdrant as _q
    _q.get_user_profile = _aw(lambda uid: profil)
    kl.grok.simple = _aw(lambda *a, **k: "Cuckold")
    kat = await kl.klassifiziere("ein ganz spezielles Szenario zu zweit")
    assert kat == "Cuckold"
    # Phantom-Kategorie bleibt draußen
    kl.grok.simple = _aw(lambda *a, **k: "Phantasie_Kategorie_XYZ")
    assert await kl.klassifiziere("ein ganz spezielles Szenario zu zweit") == "allgemein"


def test_wunschkat_parse_auswahl():
    """/wunschkategorien: Nummern wählen aus dem Pool, bekannter Freitext mappt auf
    Pool-Schreibweise, unbekannter Freitext wird als eigene Kategorie angelegt."""
    from bot.handlers import wunschkategorien as wk
    pool = ["Anal", "Pegging", "Cuckold"]

    gew, neue, err = wk._parse_auswahl("1, cuckold, Fuß Massage", pool)
    assert err is None
    assert gew == ["Anal", "Cuckold", "Fuß_Massage"]
    assert neue == ["Fuß_Massage"]

    gew2, neue2, err2 = wk._parse_auswahl("99", pool)
    assert err2 == 99 and not gew2 and not neue2

    # Duplikate (Nummer + gleicher Freitext) nur einmal
    gew3, neue3, err3 = wk._parse_auswahl("3, Cuckold; cuckold", pool)
    assert err3 is None and gew3 == ["Cuckold"] and not neue3


async def test_followup_callback_ja():
    """Button ✅ Erledigt: Task -> gefuehl_pending, Gefühl-Frage wird gesendet."""
    from bot import state as _st
    _st.set_mode("222", "chat")
    _st.get("222")["followup_task_id"] = None
    cap = {}
    _fur.qdrant.get_task = _aw(lambda tid: {"qdrant_point_id": tid, "aufgabe": "Plug tragen", "status": "gefragt"})
    _fur.qdrant.update_task = _aw(lambda tid, fields: cap.update(fields))
    _fur.grok.simple = AsyncMock(return_value="Wie war's?")
    gesendet = {}

    msg = MagicMock()
    msg.chat_id = "222"
    msg.reply_text = AsyncMock(side_effect=lambda t, **k: gesendet.update(text=t))
    q = MagicMock()
    q.data = "followup:ja:t1"
    q.answer = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    q.message = msg
    upd = MagicMock()
    upd.callback_query = q
    await _fur.callback(upd, MagicMock())
    assert cap.get("status") == "gefuehl_pending"
    assert gesendet.get("text") == "Wie war's?"


async def test_followup_callback_status_guard():
    """Review D5: alter ✅/❌-Button (Duplikat via /meineaufgaben) auf einem bereits
    erledigten/pausierten Task wird entwertet, statt den Status erneut umzuschalten
    (sonst doppelte Punkte bzw. falscher Streak-Reset + Bestrafungsvorschlag)."""
    from bot import state as _st
    from bot.messages import t as _t
    _st.set_mode("222", "chat")
    _st.get("222")["followup_task_id"] = None
    cap = {}
    _fur.qdrant.get_task = _aw(lambda tid: {"qdrant_point_id": tid, "aufgabe": "X", "status": "erledigt"})
    _fur.qdrant.update_task = _aw(lambda tid, fields: cap.update(fields))
    gesendet = {}
    msg = MagicMock()
    msg.chat_id = "222"
    msg.reply_text = AsyncMock(side_effect=lambda txt, **k: gesendet.update(text=txt))
    q = MagicMock()
    q.data = "followup:nein:t9"
    q.answer = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    q.message = msg
    upd = MagicMock()
    upd.callback_query = q
    await _fur.callback(upd, MagicMock())
    assert not cap, "erledigter Task darf nicht erneut umgeschaltet werden"
    assert gesendet.get("text") == _t("MEINEAUFGABEN_NICHT_OFFEN")


def test_limits_check_praefix_formen():
    """Review D5 (sicherheitskritisch): deutsche Präfix-Verben/Partizipien der
    Limit-Begriffe werden erkannt (auspeitschen/anspucken/gefesselt/abgewürgt/
    vollgepisst), ohne neue False Positives (Schulter, hundert, Vorschlag,
    verwundert, Entschuldigung)."""
    from bot.services.limits_check import _prufe_liste, _normalisiere

    def hits(text, limits):
        return [tr["limit"] for tr in _prufe_liste(_normalisiere(text), limits, "test")]

    # Präfix-Formen müssen matchen (waren vorher False Negatives):
    assert hits("Heute werde ich dich auspeitschen lassen.", ["Peitsche"])
    assert hits("Ich werde dich anspucken und bespucken.", ["Spucken"])
    assert hits("Du wirst ans Bett gefesselt.", ["Fesseln"])
    assert hits("Ich werde dich abwürgen.", ["Würgen"])
    assert hits("Du wirst vollgepisst.", ["Piss"])
    # Bestehende Treffer bleiben:
    assert hits("Es wird blutig.", ["Blut"])
    assert hits("In der Schule.", ["Kinder"])
    assert hits("Der Hund bellt.", ["Tiere"])
    # Keine False Positives:
    assert not hits("Massiere ihre Schultern.", ["Kinder"])
    assert not hits("Er hat Schuld daran.", ["Kinder"])
    assert not hits("Mach hundert Kniebeugen.", ["Tiere"])
    assert not hits("Ich habe einen Vorschlag.", ["Schlagen"])
    assert not hits("Das ist wunderbar, er war verwundert.", ["Blut"])
    assert not hits("Entschuldigung angenommen.", ["Kinder"])


async def test_schwierigkeit_effektiv_reihenfolge():
    """Review D5: Score unter der NIEDRIG-Schwelle ergibt 'niedrig' für JEDE
    Basis – vorher fing der hoch→normal-Zweig Basis 'hoch' zuerst ab und der
    niedrigste Score führte bei der höchsten Basis zur höheren Schwierigkeit."""
    from bot.scheduler import followup as _sched
    from bot import config as _cfg
    alt = _sched.privileg_effekte.aktiver_easy_mode
    _sched.privileg_effekte.aktiver_easy_mode = _aw(lambda: False)
    try:
        tief = _cfg.VERTRAUEN_SCHWELLE_NIEDRIG - 1
        mittel = _cfg.VERTRAUEN_SCHWELLE_SENKEN - 1
        assert await _sched._schwierigkeit_effektiv({"aufgaben_schwierigkeit": "hoch"}, tief) == "niedrig"
        assert await _sched._schwierigkeit_effektiv({"aufgaben_schwierigkeit": "normal"}, tief) == "niedrig"
        assert await _sched._schwierigkeit_effektiv({"aufgaben_schwierigkeit": "hoch"}, mittel) == "normal"
        assert await _sched._schwierigkeit_effektiv({"aufgaben_schwierigkeit": "normal"}, mittel) == "normal"
    finally:
        _sched.privileg_effekte.aktiver_easy_mode = alt


def test_set_followup_task_gibt_bool():
    """Review D5: set_followup_task meldet per bool, ob der State wirklich gesetzt
    wurde – restore_state setzt den DB-Status 'gefragt' nur noch bei True (sonst
    Status-Drift: Task 'gefragt', Frage nie gestellt)."""
    from bot import state as _st
    cid = "testchat-sft"
    _st.set_mode(cid, "chat")
    assert _st.set_followup_task(cid, "task-a") is True
    assert _st.get_mode(cid) == "followup"
    # blockierter Mode → False, Zuordnung bleibt unangetastet
    assert _st.set_followup_task(cid, "task-b") is False
    assert _st.get(cid).get("followup_task_id") == "task-a"
    _st.set_mode(cid, "chat")


async def test_generate_mit_limit_retry():
    """End-to-End (Review D5, Test-Lücke): erster Output verletzt ein Limit →
    genau EINE verschärfte Re-Generierung; verletzt auch die → None."""
    import importlib
    from bot.services import limits_check as _lc
    from bot.services import grok as _gk
    # Frühere Tests ersetzen _lc.verletzungen durch Fakes (ohne Teardown) –
    # reload stellt die echte Implementierung wieder her.
    _lc = importlib.reload(_lc)
    antworten = ["Ich werde dich auspeitschen.", "Etwas ganz Harmloses."]
    calls = {"n": 0}

    async def fake_simple(prompt, system="", **kw):
        calls["n"] += 1
        return antworten[min(calls["n"] - 1, len(antworten) - 1)]

    orig = _gk.simple
    _gk.simple = fake_simple
    try:
        res = await _lc.generate_mit_limit_retry("prompt", ["Peitsche"], [], system="s")
        assert res == "Etwas ganz Harmloses." and calls["n"] == 2
        calls["n"] = 0
        antworten[:] = ["auspeitschen!", "wieder auspeitschen!"]
        assert await _lc.generate_mit_limit_retry("prompt", ["Peitsche"], [], system="s") is None
    finally:
        _gk.simple = orig


async def test_pause_guard_blockt_callbacks_und_commands():
    """Review D5 (Safeword-Lücke): der zentrale Pause-Guard blockt während der
    Pause Callbacks, Commands und Medien; normale Texte laufen durch (Resume-Wort
    wird im Text-Pfad von safeword.check_and_handle verarbeitet)."""
    from bot import main as _main, state as _st, config as _cfg

    class _Stop(Exception):
        pass

    alt_stop = _main.ApplicationHandlerStop
    _main.ApplicationHandlerStop = _Stop
    _st.set_paused(True)
    try:
        # 1) Callback während Pause → Alert + Stop
        q = MagicMock()
        q.answer = AsyncMock()
        upd = MagicMock()
        upd.callback_query = q
        upd.effective_message = None
        upd.effective_chat.id = _cfg.DOMINA_CHAT_ID
        try:
            await _main.pause_guard(upd, MagicMock())
            raise AssertionError("Callback hätte gestoppt werden müssen")
        except _Stop:
            pass
        assert q.answer.await_count == 1

        # 2) Command während Pause → Hinweis + Stop
        msg = MagicMock()
        msg.text = "/wuerfel"
        msg.reply_text = AsyncMock()
        upd2 = MagicMock()
        upd2.callback_query = None
        upd2.effective_message = msg
        upd2.effective_chat.id = _cfg.SKLAVE_CHAT_ID
        try:
            await _main.pause_guard(upd2, MagicMock())
            raise AssertionError("Command hätte gestoppt werden müssen")
        except _Stop:
            pass
        assert msg.reply_text.await_count == 1

        # 3) Normaler Text läuft durch (Resume-Pfad)
        msg3 = MagicMock()
        msg3.text = "weiter"
        upd3 = MagicMock()
        upd3.callback_query = None
        upd3.effective_message = msg3
        upd3.effective_chat.id = _cfg.SKLAVE_CHAT_ID
        await _main.pause_guard(upd3, MagicMock())  # darf NICHT raisen

        # 4) Ohne Pause: alles läuft durch
        _st.set_paused(False)
        await _main.pause_guard(upd, MagicMock())
    finally:
        _st.set_paused(False)
        _main.ApplicationHandlerStop = alt_stop


def test_messages_konsistenz():
    """Jeder t("KEY", ...)-Aufruf im Code: Key existiert, übergebene kwargs decken
    die {platzhalter} des Templates exakt ab, und keine Funktion, die t() aufruft,
    bindet selbst eine Variable `t` (UnboundLocalError-Falle). Fängt jede künftige
    Key-/Platzhalter-/Shadowing-Regression."""
    import ast as _ast
    import glob as _glob
    import re as _re
    from bot.messages import _MESSAGES

    # t()-Aufrufe, die NICHT mit einem String-Literal-Key arbeiten (dynamische Keys
    # wie t(nachricht_key, ...)) – die Keys hier explizit als bekannt eintragen.
    dynamische_keys = {"TINYTASK_PREFIX_TINY", "TINYTASK_PREFIX_AUSFUEHRLICH"}

    def _comp_targets(node):
        own = set()
        for sub in _ast.walk(node):
            if isinstance(sub, (_ast.GeneratorExp, _ast.ListComp, _ast.SetComp, _ast.DictComp)):
                for g in sub.generators:
                    for n in _ast.walk(g.target):
                        if isinstance(n, _ast.Name):
                            own.add(id(n))
        return own

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fehler = []
    for f in _glob.glob(os.path.join(root, "bot", "**", "*.py"), recursive=True):
        tree = _ast.parse(open(f).read())
        # Nur Dateien, die t aus messages importieren, auf Shadowing prüfen.
        importiert_t = any(
            isinstance(n, _ast.ImportFrom) and n.module == "bot.messages"
            and any(a.name == "t" for a in n.names)
            for n in _ast.walk(tree)
        )
        for node in _ast.walk(tree):
            if importiert_t and isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                ct = _comp_targets(node)
                binds = [s for s in _ast.walk(node)
                         if isinstance(s, _ast.Name) and s.id == "t"
                         and isinstance(s.ctx, _ast.Store) and id(s) not in ct]
                calls = [s for s in _ast.walk(node)
                         if isinstance(s, _ast.Call) and isinstance(s.func, _ast.Name)
                         and s.func.id == "t"]
                if binds and calls:
                    fehler.append(f"{os.path.relpath(f, root)}:{node.lineno} schattet t in {node.name}")
            if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name) and node.func.id == "t":
                if node.args and isinstance(node.args[0], _ast.Constant) and isinstance(node.args[0].value, str):
                    key = node.args[0].value
                    if key in dynamische_keys:
                        continue
                    if key not in _MESSAGES:
                        fehler.append(f"{os.path.relpath(f, root)}:{node.lineno} unbekannter Key {key!r}")
                        continue
                    platzhalter = set(_re.findall(r"\{(\w+)\}", _MESSAGES[key]))
                    kwargs = {kw.arg for kw in node.keywords if kw.arg}
                    if kwargs and platzhalter != kwargs:
                        fehler.append(f"{os.path.relpath(f, root)}:{node.lineno} {key}: Template {sorted(platzhalter)} != kwargs {sorted(kwargs)}")
                    elif platzhalter and not kwargs:
                        fehler.append(f"{os.path.relpath(f, root)}:{node.lineno} {key}: Platzhalter {sorted(platzhalter)} ohne kwargs")
    # Alle dynamischen Keys müssen existieren
    for k in dynamische_keys:
        if k not in _MESSAGES:
            fehler.append(f"dynamischer Key {k!r} fehlt in _MESSAGES")
    assert not fehler, "messages.py-Inkonsistenzen:\n  " + "\n  ".join(fehler)


def test_prompt_builder_vertraege():
    """Alle öffentlichen Prompt-Builder, die auf (system, user)-Tupel umgestellt
    wurden, müssen ein 2-Tupel aus zwei Strings zurückgeben – sonst entpackt
    grok.simple/generate_mit_limit_retry falsch."""
    from bot.prompts import followup as fpb, bestrafung as bpb, sklave as spb
    aufrufe = [
        fpb.followup_frage("Aufgabe"),
        fpb.aufgabe_an_sklaven("Aufgabe"),
        fpb.reaktion_auf_gefuehl("Aufgabe", "geil"),
        fpb.serie_variationen("Aufgabe", 3, "Pegging"),
        fpb.kette_anpassung("Naechste", "langweilig", "langweilig"),
        fpb.reaktion_auf_stimmung("müde"),
        fpb.reaktion_auf_nicht_erledigt("Aufgabe"),
        fpb.gefuehl_abfragen("Aufgabe"),
        fpb.bericht_erledigt("Aufgabe", "geil"),
        fpb.bericht_nicht_erledigt("Aufgabe"),
        fpb.tiny_task_vorschlag(erfahrungsstand="x", level=1, interessen=[],
                                sklave_vorlieben=[], sklave_hard_limits=[]),
        fpb.ausfuehrlicher_task_vorschlag(erfahrungsstand="x", level=1, interessen=[],
                                          sklave_vorlieben=[], sklave_hard_limits=[]),
        bpb.bestrafungsvorschlag("Aufgabe", 3),
        bpb.eskalations_vorschlag("Aufgabe", 3),
        spb.abzeichen_vorschlag("Test", "🏅"),
    ]
    for i, r in enumerate(aufrufe):
        assert isinstance(r, tuple) and len(r) == 2, f"Builder #{i} gibt kein 2-Tupel: {type(r)}"
        assert isinstance(r[0], str) and isinstance(r[1], str), f"Builder #{i}: (system, user) nicht beide str"


def test_parse_wochenplan_edges():
    """Robustheit von _parse_wochenplan: Doppelpunkt-Trenner, **Bold**-Tag,
    Tag ohne Aufgabe (verworfen)."""
    plan = (
        "**Montag**: Pegging\nAufgabe: Plug tragen.\n\n"
        "Dienstag – Spanking\nWarum: nur Begründung, keine Aufgabe.\n\n"
        "Mittwoch: Ritual\n**Aufgabe**: Knien.\n"
    )
    e = wochenplanung._parse_wochenplan(plan)
    aufgaben = {x["tag"]: x["aufgabe"] for x in e}
    assert aufgaben.get("Montag") == "Plug tragen."
    assert "Dienstag" not in aufgaben, "Tag ohne Aufgabe muss verworfen werden"
    assert aufgaben.get("Mittwoch") == "Knien."


def test_safeword_pause_schuetzt_und_blockt():
    """Safeword-Pause: Mode 'pausiert' ist gegen den Stale-Guard geschützt,
    is_paused() greift, _flow_aktiv blockt während der Pause."""
    from bot import state as _state
    from bot.scheduler import followup as _sched
    import time as _time
    _state._state.clear()
    cid = "999"
    _state.set_mode(cid, "pausiert")
    # Mode-Since künstlich weit in die Vergangenheit – Stale-Guard dürfte NICHT greifen.
    _state.get(cid)["mode_since"] = _time.time() - 999999
    assert _state.clear_if_stale(cid) is False, "pausiert darf nicht stale-resettet werden"
    assert _state.get_mode(cid) == "pausiert"
    # Globale Pause blockt _flow_aktiv für jeden Chat.
    _state.set_paused(True)
    try:
        assert _state.is_paused() is True
        assert _sched._flow_aktiv("12345", "Test-Job") is True, "während Pause muss _flow_aktiv blocken"
    finally:
        _state.set_paused(False)
    assert _state.is_paused() is False


def test_heuristik_label():
    """B1: Kurzlabel-Heuristik streift Floskel-Opener, Markdown, Nummerierung und
    Begründungs-/Abschluss-Sätze ab und kürzt an der Wortgrenze."""
    from bot.services import labels
    text = ("Hey, wie wär's mit einem *Eiswürfel-Spiel* beim Duschen? "
            "Das passt genau zu ihm, weil er Temperaturreize mag. Klingt das machbar?")
    lbl = labels.heuristik_label(text)
    assert "Eiswürfel-Spiel" in lbl, lbl
    assert "wie wär" not in lbl.lower() and "machbar" not in lbl and "passt genau" not in lbl, lbl
    # typografischer Apostroph (U+2019) wie im echten LLM-Output
    lbl_typo = labels.heuristik_label("Hey, wie wär’s mit einer kurzen Prostatamassage heute Abend: Du schnallst den Gurt um.")
    assert not lbl_typo.lower().startswith("hey") and "wie wär" not in lbl_typo.lower(), lbl_typo
    lbl2 = labels.heuristik_label("3. **Sinnesspiel** – Augenbinde und Feder. Warum gut: Vertrauen.")
    assert lbl2.startswith("Sinnesspiel"), lbl2
    assert "Warum gut" not in lbl2, lbl2
    lang = labels.heuristik_label("Wort " * 40)
    assert len(lang) <= labels.MAX_LEN + 1 and lang.endswith("…"), lang
    assert labels.heuristik_label("") == ""


def test_gewichtete_auswahl_cross_info():
    """B3: mit_cross_info liefert (liste, cross_pick); der Cross-Slot liegt
    außerhalb der Basis-Cluster und ist Teil der Auswahl."""
    prof = {"wunsch_kategorien": ["Analtraining"], "kategorie_reaktionen": {}}
    pool = kategorie_logik.alle_kategorien(prof)
    basis = {k for k in ("Analtraining",) if k in pool}
    for _ in range(20):
        kats, cross = kategorie_logik.gewichtete_auswahl(
            prof, letzte_kategorien=[], count=3, mit_cross_info=True)
        assert isinstance(kats, list) and len(kats) <= 3
        if basis and kategorie_logik._cluster_von(basis):
            assert cross is not None and cross in kats
            assert not (kategorie_logik.KATEGORIE_ZU_CLUSTER.get(cross, set())
                        & kategorie_logik._cluster_von(basis)), (cross, kats)
    # Default-Aufruf (ohne Flag) bleibt eine flache Liste (Alt-Verhalten)
    flach = kategorie_logik.gewichtete_auswahl(prof, letzte_kategorien=[], count=2)
    assert isinstance(flach, list)


def test_aufgaben_kontext_b3_b4_b7():
    """B3/B4/B7 im Prompt-Builder: Cross-Slot markiert, Dienst-Konflikt wird EIN
    Spannungs-Hinweis (raus aus NIEMALS), Domina-Präferenzen sichtbar, und das
    Formel-Verbot (B1) steht im System-Prompt."""
    from bot.prompts import followup as fpb
    system, user = fpb.tiny_task_vorschlag(
        erfahrungsstand="x", level=1, interessen=[],
        sklave_vorlieben=[], sklave_hard_limits=[],
        sklave_dislike_kategorien=["Dienst", "CBT"],
        bewertungs_kontext="Aufgaben die der Domina gut gefielen (4-5★): Dienst\n",
        gewaehlte_kategorien=["Analtraining", "Feminisierung"],
        cross_kategorie="Feminisierung",
        domina_kategorie_praeferenzen={
            "Orgasmuskontrolle": {"positiv": 2, "negativ": 0},
            "Kniebeugen": {"positiv": 0, "negativ": 1},
        },
    )
    assert "SPANNUNGSFELD" in user and "Dienst" in user, user
    assert "❌ Dienst" not in user, "Konflikt-Kategorie darf nicht in der NIEMALS-Liste bleiben"
    assert "❌ CBT" in user, "unkonfliktige Dislikes müssen in der NIEMALS-Liste bleiben"
    assert "Feminisierung ← frisches Thema" in user, user
    assert "gern übernommen: Orgasmuskontrolle" in user, user
    assert "eher abgelehnt: Kniebeugen" in user, user
    assert "Klingt das machbar?" in system, "Formel-Verbot fehlt im System-Prompt"


def test_dossier_gekuerzt_satzgrenze():
    """B5: Dossier wird an der Satzgrenze gekürzt, nicht mitten im Wort."""
    from bot.prompts import coach_persona as cp
    out = cp.dossier_gekuerzt("Erster Satz ist hier. " * 20, limit=100)
    assert out.endswith(".") and len(out) <= 100, out
    assert cp.dossier_gekuerzt("kurz.", limit=100) == "kurz."
    # kein Satzende in der ersten Hälfte → Wortgrenze + Ellipse
    out2 = cp.dossier_gekuerzt("Wort " * 50, limit=100)
    assert out2.endswith("…") and len(out2) <= 102, out2


def test_format_context_dedup():
    """B6: wichtige Punkte, die schon in der Zusammenfassung stehen, werden
    nicht doppelt gerendert."""
    from bot.prompts import domina_coach as dc
    e = {
        "datum": "2026-06-22T10:00:00+00:00",
        "zusammenfassung": "Domina: Erster Satz. Zweiter Satz.\nCoach: ok",
        "wichtige_punkte": ["Erster Satz.", "Ganz anderer Punkt"],
        "themen": ["idee"],
    }
    out = dc.format_context([e])
    assert out.count("Erster Satz.") == 1, out
    assert "Ganz anderer Punkt" in out, out


def test_eintrag_alter_tage():
    """B6: Altersfilter-Helper – ISO-Datum wird korrekt gealtert, Müll → 0."""
    from datetime import datetime, timedelta, timezone
    from bot.scheduler import followup as _sched
    alt = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    assert _sched._eintrag_alter_tage(alt) >= 8
    assert _sched._eintrag_alter_tage("kaputt") == 0
    assert _sched._eintrag_alter_tage("") == 0


async def test_tinyfb_klassifikation():
    """B2: Freitext im Feedback-Modus wird klassifiziert – ein anderes Anliegen
    geht an den Domina-Chat (Feedback bleibt offen), ein echter Ablehnungsgrund
    wird wie bisher gespeichert."""
    from bot.handlers import tiny_task_feedback as tfb
    from bot.handlers import domina as _dom
    from bot.services import telegram_helper as _th
    cid = _config.DOMINA_CHAT_ID
    orig = (tfb.qdrant.get_tiny_task_by_id, tfb.qdrant.mark_tiny_task_status,
            tfb.grok.simple, _dom.handle, tfb.kategorie_logik.record_domina_praeferenz,
            tfb._vorschlag_aus_ablehnung, _th.reply_markdown_safe)
    markiert, praef, chat_calls = {}, [], []
    tfb.qdrant.get_tiny_task_by_id = _aw(lambda pid: {"inhalt": "Vorschlag X", "kategorien": ["Dienst"]})
    tfb.qdrant.mark_tiny_task_status = _aw(lambda pid, s, grund="": markiert.update({pid: (s, grund)}))
    tfb.kategorie_logik.record_domina_praeferenz = _aw(lambda kats, sig: praef.append((tuple(kats), sig)))
    tfb._vorschlag_aus_ablehnung = _aw(lambda *a, **k: None)
    _th.reply_markdown_safe = AsyncMock()

    async def _fake_dom(update, context):
        chat_calls.append(update.message.text)
    _dom.handle = _fake_dom

    def _mk_update(text):
        upd = MagicMock()
        upd.effective_chat.id = cid
        upd.message.text = text
        upd.message.reply_text = AsyncMock()
        return upd

    try:
        # Fall 1: anderes Anliegen → Domina-Chat, nichts gespeichert, Mode bleibt
        _state_mod._state.clear()
        _state_mod.get(cid)["tiny_task_feedback_id"] = "p1"
        _state_mod.set_mode(cid, "tiny_task_feedback")
        tfb.grok.simple = AsyncMock(return_value="ANDERES")
        await tfb.handle(_mk_update("Kannst du ihm den Wochenplan senden?"), MagicMock())
        assert chat_calls == ["Kannst du ihm den Wochenplan senden?"], chat_calls
        assert not markiert and not praef
        assert _state_mod.get_mode(cid) == "tiny_task_feedback", "Feedback muss offen bleiben"

        # Fall 2: echter Ablehnungsgrund → wie bisher speichern + Signal
        tfb.grok.simple = AsyncMock(return_value="ABLEHNUNG")
        await tfb.handle(_mk_update("zu langweilig, sowas hatten wir oft"), MagicMock())
        assert markiert.get("p1", ("", ""))[0] == "abgelehnt", markiert
        assert praef == [(("Dienst",), "abgelehnt")], praef
        assert _state_mod.get_mode(cid) == "chat"
    finally:
        (tfb.qdrant.get_tiny_task_by_id, tfb.qdrant.mark_tiny_task_status,
         tfb.grok.simple, _dom.handle, tfb.kategorie_logik.record_domina_praeferenz,
         tfb._vorschlag_aus_ablehnung, _th.reply_markdown_safe) = orig


def _run():
    asyncio.run(test_lernkern_schreibt_profil())
    asyncio.run(test_tag_flip_bei_umkehr())
    asyncio.run(test_klassifikation_keyword_und_fallback())
    test_gewichtete_auswahl_schliesst_dislikes_aus()
    test_gewichtete_auswahl_mix_60_30_10()
    test_gewichtete_auswahl_ohne_vorlieben()
    test_progressive_level_logik()
    asyncio.run(test_gefuehl_schreibt_kategorie_level())
    test_prompt_enthaelt_level_hinweis()
    asyncio.run(test_adaptive_kette_vorschlag())
    asyncio.run(test_adaptive_kette_callbacks())
    test_serie_variationen_parse()
    test_wochenplan_parse()
    asyncio.run(test_llm_fallback())
    test_state_persistenz()
    test_sklave_prompt_wissen()
    test_sklave_themen()
    asyncio.run(test_dossier_build())
    test_namen_persona()
    test_decay_reaktionen()
    asyncio.run(test_offene_faeden())
    asyncio.run(test_domina_dossier())
    asyncio.run(test_wunsch_erfassung())
    asyncio.run(test_apply_profile_patch_limit_add())
    asyncio.run(test_praeferenz_detektor())
    test_limits_check_ausnahme_annotation()
    asyncio.run(test_refine_profile_limit_und_patch())
    asyncio.run(test_patch_profile_fields_kein_full_upsert())
    test_zeiten_fenster_validierung()
    test_profil_parse_liste()
    asyncio.run(test_praeferenz_detektor_ausnahmen())
    asyncio.run(test_skill_kontext_block())
    asyncio.run(test_skill_kurzfassung_best_effort())
    asyncio.run(test_kategorien_pool())
    test_wunschkat_parse_auswahl()
    asyncio.run(test_followup_callback_ja())
    asyncio.run(test_followup_callback_status_guard())
    test_limits_check_praefix_formen()
    asyncio.run(test_schwierigkeit_effektiv_reihenfolge())
    test_set_followup_task_gibt_bool()
    asyncio.run(test_generate_mit_limit_retry())
    asyncio.run(test_pause_guard_blockt_callbacks_und_commands())
    test_messages_konsistenz()
    test_prompt_builder_vertraege()
    test_parse_wochenplan_edges()
    test_safeword_pause_schuetzt_und_blockt()
    test_heuristik_label()
    test_gewichtete_auswahl_cross_info()
    test_aufgaben_kontext_b3_b4_b7()
    test_dossier_gekuerzt_satzgrenze()
    test_format_context_dedup()
    test_eintrag_alter_tage()
    asyncio.run(test_tinyfb_klassifikation())
    print("✅ Alle Lern-System-Tests bestanden")


if __name__ == "__main__":
    _run()
