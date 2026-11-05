"""
Tests für Vanilla Etappe 3 (Spaß + Planung, 05.11.2026): services/vanilla_punkte,
handlers/vanilla_spiele (Nachfrage-Callbacks, Würfel-Gate, Wunsch-Relay, Gutscheine,
Termin, Quiz-Mode), Modus-Router in app/registrierung, Routing der Vanilla-Modes,
Scheduler-Jobs im Job-Gate.

    python3 tests/test_vanilla_spiele.py
"""
import asyncio
import contextlib
import os
import sys
import tempfile
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402
stubs.aktivieren()

from bot import config  # noqa: E402
config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.messages import t  # noqa: E402
from bot.prompts import vanilla as vp  # noqa: E402
from bot.services import paare, persona_config, vanilla_punkte as vpkt  # noqa: E402
from bot.services.db import profile as db_profile, tasks as db_tasks, wuensche as db_wuensche  # noqa: E402
from bot.services.db import progress as db_progress  # noqa: E402
from bot.handlers import vanilla_spiele as vs  # noqa: E402
from bot.app import registrierung, routing  # noqa: E402


@contextlib.contextmanager
def _modus(wert: str, **extra):
    alt = dict(persona_config._cache)
    persona_config._cache["modus"] = wert
    persona_config._cache.update(extra)
    try:
        yield
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)


class _Profile:
    """Patcht get_user_profile/patch_profile_fields auf ein In-Memory-Dict je Slot."""
    def __init__(self, **profile):
        self.daten = {k: dict(v) for k, v in profile.items()}
        self.orig = (db_profile.get_user_profile, db_profile.patch_profile_fields)

    async def get(self, uid):
        return dict(self.daten.get(uid, {}))

    async def patch(self, uid, felder, **k):
        self.daten.setdefault(uid, {}).update(felder)
        return "ok"

    def __enter__(self):
        db_profile.get_user_profile = self.get
        db_profile.patch_profile_fields = self.patch
        return self

    def __exit__(self, *a):
        db_profile.get_user_profile, db_profile.patch_profile_fields = self.orig


def _msg(chat, text="x"):
    u = MagicMock()
    u.effective_chat.id = chat
    u.message.text = text
    u.message.chat_id = chat
    u.message.reply_text = AsyncMock()
    u.message.reply_dice = AsyncMock(side_effect=RuntimeError("kein dice"))
    return u


def _query(chat, data):
    u = MagicMock()
    u.effective_chat.id = chat
    u.callback_query.data = data
    u.callback_query.answer = AsyncMock()
    u.callback_query.edit_message_reply_markup = AsyncMock()
    u.callback_query.message.chat_id = chat
    u.callback_query.message.reply_text = AsyncMock()
    return u


# ---------------------------------------------------------------- vanilla_punkte

def test_rang_und_gutscheine():
    assert vpkt.rang(0) == "Funke" and vpkt.rang(120) == "Flamme" and vpkt.rang(5000) == "Unzertrennlich"
    assert vpkt.naechster_rang(120) == (250, "Glut") and vpkt.naechster_rang(5000) is None
    assert vpkt.gutschein("massage")["kosten"] == 60 and vpkt.gutschein("x") is None
    assert len({g["id"] for g in vpkt.GUTSCHEINE}) == len(vpkt.GUTSCHEINE)


async def test_erledigt_streak_und_wette():
    heute = date.today().strftime("%Y-%m-%d")
    gestern = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    with _Profile(domina={"punkte": 20, "streak": 4, "letzter_streak_tag": gestern,
                          "vanilla_wette": {"einsatz": 10}}) as p:
        e = await vpkt.erledigt(paare.ROLLE_DOM, {"quelle": "vanilla_wuerfel"})
        assert e["streak"] == 5 and e["wette_gewinn"] == 20
        # Basis 10 + Streak 5 + Würfel 5 + Wette 20
        assert e["punkte"] == 60 and p.daten["domina"]["vanilla_wette"] is None
        assert p.daten["domina"]["letzter_streak_tag"] == heute
        # zweiter Task am selben Tag: Streak bleibt
        e2 = await vpkt.erledigt(paare.ROLLE_DOM, {})
        assert e2["streak"] == 5 and e2["punkte"] == 75
    with _Profile(sklave={"punkte": 30, "vanilla_wette": {"einsatz": 25}}) as p:
        assert await vpkt.nicht_erledigt(paare.ROLLE_SUB) == 25
        assert p.daten["sklave"]["punkte"] == 30            # kein Abzug – Strafen aus
        assert await vpkt.wette_setzen(paare.ROLLE_SUB, 25) == {"einsatz": 25, "rest": 5}
        assert await vpkt.wette_setzen(paare.ROLLE_SUB, 10) is None   # läuft schon
    with _Profile(sklave={"punkte": 5}):
        assert await vpkt.wette_setzen(paare.ROLLE_SUB, 10) is None   # zu wenig
    with _Profile(domina={"punkte": 10}) as p:
        b = await vpkt.buchen(paare.ROLLE_DOM, -60, "test")
        assert b["neu"] == 0 and b["delta"] == -10


# ---------------------------------------------------------------- Handler-Gates

async def test_darf_nur_vanilla_und_paar():
    with _modus(""):
        assert vs._darf(_msg("111")) is None
    with _modus("vanilla"):
        assert vs._darf(_msg("111")) == paare.ROLLE_DOM
        assert vs._darf(_msg("222")) == paare.ROLLE_SUB
        assert vs._darf(_msg("999")) is None
    # /termin im BDSM-Modus: Hinweis statt Funktion
    with _modus(""):
        u = _msg("111")
        await vs.termin(u, MagicMock(args=[]))
        assert u.message.reply_text.await_args.args[0] == t("COMMON_NUR_VANILLA")


# ---------------------------------------------------------------- Nachfrage-Callbacks

async def test_follow_callback_ja_und_nein():
    tasks = {"t1": {"qdrant_point_id": "t1", "user_id": "domina", "status": "gefragt",
                    "aufgabe": "Koch ihm was", "quelle": "vanilla_impuls"},
             "t2": {"qdrant_point_id": "t2", "user_id": "sklave", "status": "offen",
                    "aufgabe": "Massage", "quelle": "vanilla_wuerfel"}}
    orig = (db_tasks.get_task, db_tasks.update_task, vs.grok.simple)
    db_tasks.get_task = AsyncMock(side_effect=lambda pid: tasks.get(pid))
    db_tasks.update_task = AsyncMock(side_effect=lambda pid, upd: tasks[pid].update(upd))
    vs.grok.simple = AsyncMock(return_value="Schön!")
    try:
        with _modus("vanilla"), _Profile(domina={"punkte": 0}, sklave={"punkte": 10, "vanilla_wette": {"einsatz": 10}}) as p:
            # Dom bestätigt eigene Aufgabe
            u = _query("111", "vfollow:ja:t1")
            await vs.callback(u, MagicMock())
            assert tasks["t1"]["status"] == "erledigt" and p.daten["domina"]["punkte"] == 10
            antwort = u.callback_query.message.reply_text.await_args_list[0].args[0]
            assert "Schön!" in antwort and "+10" in antwort
            # fremde Aufgabe (Sub-Task vom Dom-Chat): nichts passiert
            u = _query("111", "vfollow:ja:t2")
            await vs.callback(u, MagicMock())
            assert tasks["t2"]["status"] == "offen"
            # Sub: nicht erledigt → Wette verloren, keine Punkte weg
            u = _query("222", "vfollow:nein:t2")
            await vs.callback(u, MagicMock())
            assert tasks["t2"]["status"] == "nicht_erledigt" and p.daten["sklave"]["punkte"] == 10
            assert "10 Punkte" in u.callback_query.message.reply_text.await_args.args[0]
            # Doppel-Tap: Status nicht mehr offen
            u = _query("222", "vfollow:ja:t2")
            await vs.callback(u, MagicMock())
            assert u.callback_query.message.reply_text.await_args.args[0] == t("MEINEAUFGABEN_NICHT_OFFEN")
        with _modus(""):                       # BDSM: Dispatcher tut nichts
            u = _query("111", "vfollow:ja:t1")
            await vs.callback(u, MagicMock())
            assert u.callback_query.message.reply_text.await_count == 0
    finally:
        db_tasks.get_task, db_tasks.update_task, vs.grok.simple = orig


# ---------------------------------------------------------------- Wunsch-Relay

async def test_wunsch_relay():
    gespeichert = {}
    orig = (db_wuensche.save_wunsch, db_wuensche.get_wunsch, db_wuensche.update_wunsch, vs.telegram_helper.send_an)

    async def _save(uid, data):
        gespeichert["w1"] = {**data, "user_id": uid}
        return "w1"
    db_wuensche.save_wunsch = _save
    db_wuensche.get_wunsch = AsyncMock(side_effect=lambda pid: gespeichert.get(pid))
    db_wuensche.update_wunsch = AsyncMock(side_effect=lambda pid, upd: gespeichert[pid].update(upd))
    vs.telegram_helper.send_an = AsyncMock()
    try:
        with _modus("vanilla", sklave_anrede="Jonas"):
            u = _msg("111")
            await vs.wunsch(u, MagicMock())
            assert state.get_mode("111") == vs.MODE_WUNSCH
            assert "Jonas" in u.message.reply_text.await_args.args[0]
            u = _msg("111", "Massier mir mal den Rücken")
            await vs.wunsch_text(u, MagicMock())
            assert state.get_mode("111") == "chat" and gespeichert["w1"]["an"] == "sklave"
            ziel = vs.telegram_helper.send_an.await_args.args[2]
            assert ziel == paare.ROLLE_SUB and "Rücken" in vs.telegram_helper.send_an.await_args.args[3]
            # Dom darf den eigenen Wunsch nicht beantworten
            q = _query("111", "vwunsch:gern:w1")
            await vs.callback(q, MagicMock())
            assert gespeichert["w1"]["status"] == "offen"
            q = _query("222", "vwunsch:gern:w1")
            await vs.callback(q, MagicMock())
            assert gespeichert["w1"]["status"] == "gern"
            assert vs.telegram_helper.send_an.await_args.args[2] == paare.ROLLE_DOM
            assert "gern" in vs.telegram_helper.send_an.await_args.args[3]
    finally:
        state._state.clear()
        (db_wuensche.save_wunsch, db_wuensche.get_wunsch, db_wuensche.update_wunsch,
         vs.telegram_helper.send_an) = orig


# ---------------------------------------------------------------- Gutscheine

async def test_gutschein_einloesen():
    orig = (vs.telegram_helper.send_an, vs.grok.simple, vs.telegram_helper.reply_markdown_safe)
    vs.telegram_helper.send_an = AsyncMock()
    vs.grok.simple = AsyncMock(return_value="Mach was draus.")
    vs.telegram_helper.reply_markdown_safe = AsyncMock()
    try:
        with _modus("vanilla"), _Profile(sklave={"punkte": 70}) as p:
            u = _msg("222")
            await vs.gutscheine(u, MagicMock())
            markup = vs.telegram_helper.reply_markdown_safe.await_args.kwargs["reply_markup"]
            nonce = state.get("222")["vgut_nonce"]
            q = _query("222", f"vgut:massage:{nonce}")
            await vs.callback(q, MagicMock())
            assert p.daten["sklave"]["punkte"] == 10 and p.daten["sklave"]["gutscheine_eingeloest"] == 1
            assert vs.telegram_helper.send_an.await_args.args[2] == paare.ROLLE_DOM
            assert "Massage" in vs.telegram_helper.send_an.await_args.args[3]
            # alter Nonce → nichts
            q = _query("222", f"vgut:massage:{nonce}")
            await vs.callback(q, MagicMock())
            assert p.daten["sklave"]["punkte"] == 10
    finally:
        state._state.clear()
        vs.telegram_helper.send_an, vs.grok.simple, vs.telegram_helper.reply_markdown_safe = orig


# ---------------------------------------------------------------- Termin

async def test_termin_anlegen():
    angelegt = []
    orig = (db_tasks.erstelle_task, vs.telegram_helper.send_an)

    async def _erstelle(aufgabe, kategorie, level, **kw):
        angelegt.append({"aufgabe": aufgabe, **kw})
        return "p1"
    db_tasks.erstelle_task = _erstelle
    vs.telegram_helper.send_an = AsyncMock()
    try:
        with _modus("vanilla"):
            morgen = (date.today() + timedelta(days=1)).strftime("%d.%m.")
            u = _msg("111")
            await vs.termin(u, MagicMock(args=[morgen, "Kino", "zu", "zweit"]))
            assert angelegt and angelegt[0]["quelle"] == "vanilla_termin" and angelegt[0]["user_id"] == "domina"
            assert angelegt[0]["status"] == "geplant" and angelegt[0]["aufgabe"] == "Kino zu zweit"
            assert vs.telegram_helper.send_an.await_args.args[2] == paare.ROLLE_SUB
            # ohne Datum → Rückfrage, ohne erkanntes Datum → Hinweis
            u = _msg("222")
            await vs.termin(u, MagicMock(args=[]))
            assert state.get_mode("222") == vs.MODE_TERMIN
            u = _msg("222", "irgendwann mal essen")
            await vs.termin_text(u, MagicMock())
            assert state.get_mode("222") == "chat" and len(angelegt) == 1
            assert u.message.reply_text.await_args.args[0] == t("VANILLA_TERMIN_UNKLAR")
    finally:
        state._state.clear()
        db_tasks.erstelle_task, vs.telegram_helper.send_an = orig


# ---------------------------------------------------------------- Quiz-Mode + Routing + Router

async def test_quiz_und_routing_modes():
    orig = (routing.safeword.check_and_handle, vs.quiz_antwort, vs.wunsch_text, vs.termin_text, vs.wochenplan_thema)
    routing.safeword.check_and_handle = AsyncMock(return_value=False)
    vs.quiz_antwort = AsyncMock()
    vs.wunsch_text = AsyncMock()
    vs.termin_text = AsyncMock()
    vs.wochenplan_thema = AsyncMock()
    try:
        with _modus("vanilla"):
            for mode, fn in ((vs.MODE_QUIZ, vs.quiz_antwort), (vs.MODE_WUNSCH, vs.wunsch_text),
                             (vs.MODE_TERMIN, vs.termin_text), (vs.MODE_WOCHENPLAN, vs.wochenplan_thema)):
                state.set_mode("222", mode)
                await routing.handle_message(_msg("222", "antwort"), MagicMock())
                assert fn.await_count == 1, mode
    finally:
        state._state.clear()
        (routing.safeword.check_and_handle, vs.quiz_antwort, vs.wunsch_text, vs.termin_text,
         vs.wochenplan_thema) = orig
    # Quiz-Antwort bucht Punkte und räumt den Mode
    orig2 = vs.grok.simple
    vs.grok.simple = AsyncMock(side_effect=["RICHTIG", "Sitzt."])
    try:
        with _modus("vanilla"), _Profile(sklave={"punkte": 0}) as p:
            s = state.get("222")
            s["quiz_frage"], s["quiz_musterantwort"] = "Was mag sie?", "Massagen"
            state.set_mode("222", vs.MODE_QUIZ)
            u = _msg("222", "Massagen")
            await vs.quiz_antwort(u, MagicMock())
            assert state.get_mode("222") == "chat" and p.daten["sklave"]["punkte"] == vpkt.QUIZ_RICHTIG
    finally:
        state._state.clear()
        vs.grok.simple = orig2


async def test_je_modus_router():
    bdsm, van = AsyncMock(), AsyncMock()
    fn = registrierung._je_modus(bdsm, van)
    with _modus("vanilla"):
        await fn(1, 2)
    with _modus(""):
        await fn(1, 2)
    with _modus("kink"):
        await fn(1, 2)
    assert van.await_count == 1 and bdsm.await_count == 2


def test_prompts_etappe3():
    with _modus("vanilla", sklave_anrede="Jonas", dom_anrede="Anna"):
        s, u = vp.followup_frage(paare.ROLLE_SUB, "Koch ihr was", 1, 4)
        assert "Jonas gestern" in s and "4 Tage in Folge" in s and "Koch ihr was" in u
        s, u = vp.reaktion_nicht_erledigt(paare.ROLLE_DOM, "X")
        assert "keine Strafe" in s and "Anna hat" in s
        s, u = vp.quiz_frage(paare.ROLLE_DOM, "- Mag: Dessous", ["Alte Frage?"])
        assert "kennt sie Jonas" in s and "Alte Frage?" in s and "Daten über Jonas" in u
        s, u = vp.gutschein_hinweis(paare.ROLLE_SUB, vpkt.gutschein("massage"))
        assert "Jonas hat gerade" in s and "Anna dran" in s and "Massage" in u
        s, u = vp.wochenplan({"interessen": ["Massagen"], "grenzen": ["Dritte"]}, {"vorlieben": ["Dessous"]}, "Zärtlichkeit")
        assert "Anna und Jonas" in s and "ihr-Form" in s and "Dritte" in u and "Zärtlichkeit" in u
        for text in (s, u):
            assert "Herrin" not in text and "Sklav" not in text


def main():
    sync = [test_rang_und_gutscheine, test_prompts_etappe3]
    async_ = [test_erledigt_streak_und_wette, test_darf_nur_vanilla_und_paar, test_follow_callback_ja_und_nein,
              test_wunsch_relay, test_gutschein_einloesen, test_termin_anlegen, test_quiz_und_routing_modes,
              test_je_modus_router]
    fehler = 0
    for fn in sync + async_:
        try:
            asyncio.run(fn()) if fn in async_ else fn()
            print(f"  ✅ {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            fehler += 1
            import traceback
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print("FEHLER" if fehler else "✅ Alle Vanilla-Spiele-Tests bestanden")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
