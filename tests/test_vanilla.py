"""
Tests für den Vanilla-Modus (gleichberechtigt, neutrale dritte Stimme),
Umbau 05.11.2026 Etappe 2: prompts/vanilla.py, handlers/vanilla.py,
Stimmung für beide, Job-Gate (VANILLA_JOBS), Command-Gate, Menüs, t_modus,
Onboarding-Wartegate, coachregel-Rollen-Gate.

Leitplanke wie in test_modi: ohne Modus (= BDSM) bleibt alles beim
Bestandsverhalten – die Prompt-Baseline (tests/render_baseline.py) muss
byte-identisch bleiben.

    python3 tests/test_vanilla.py
"""
import asyncio
import contextlib
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402
stubs.aktivieren()

from bot import config  # noqa: E402
config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import commands_katalog as ck, state  # noqa: E402
from bot.messages import t, t_modus  # noqa: E402
from bot.prompts import persona_presets, vanilla as vp  # noqa: E402
from bot.services import modi, paare, persona_config  # noqa: E402
from bot.services.db import profile as db_profile, coach_regeln as db_cr  # noqa: E402
from bot.scheduler import basis  # noqa: E402
from bot.app import gates, routing  # noqa: E402
from bot.handlers import stimmung, vanilla as vh, onboarding, coach_regeln, einstellungen  # noqa: E402


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


# ---------------------------------------------------------------- modi / presets

def test_modus_und_strafen():
    with _modus(""):
        assert modi.strafen_aktiv() and not modi.gleichberechtigt()
    with _modus("kink"):
        assert modi.strafen_aktiv() and not modi.gleichberechtigt()
    with _modus("vanilla"):
        assert modi.ist_vanilla() and modi.gleichberechtigt() and not modi.strafen_aktiv()
        assert modi.thema() == "Paar-Intimität"
    assert modi.WAEHLBAR == ("bdsm", "kink", "vanilla")


def test_presets_vanilla():
    keys = list(persona_presets.presets_fuer_modus("vanilla"))
    assert keys == ["vanilla_freundin", "vanilla_freund", "vanilla_neutral"]
    for k in keys:
        p = persona_presets.PRESETS[k]
        assert p["modus"] == "vanilla" and p["label"] and p["stimme_ich"]
        assert "Befehlston" in p["stil_fuss"]
        assert "Herrin" not in p["stil_kopf"] and "Sklav" not in p["stil_kopf"]
    assert persona_presets.PRESETS["standard"]["stimme_ich"] == ""   # BDSM-Presets unberührt
    with _modus("vanilla"):
        assert persona_presets.default_key() == "vanilla_freundin"
        assert persona_presets.stil_key_fuer_eingabe("2") == "vanilla_freund"
        assert "Freundin" in persona_presets.stil_hinweis() and "sadistisch" not in persona_presets.stil_hinweis()
    with _modus("vanilla", persona_stil="streng"):          # fremder Key → Vanilla-Standard
        assert persona_presets.aktuelles_label() == "Vertraute Freundin (Standard)"
    with _modus("vanilla", persona_stil="vanilla_neutral"):
        assert "ohne Geschlecht" in persona_presets.aktuelles_preset()["stimme_ich"]


# ---------------------------------------------------------------- Prompts

_DOM = {"interessen": ["Massagen", "Zeit zu zweit"], "grenzen": ["Dritte"], "onboarding_abgeschlossen": True}
_SUB = {"vorlieben": ["Dessous"], "hard_limits": ["Fotos"], "onboarding_abgeschlossen": True}


def test_chat_prompt_beide_seiten():
    with _modus("vanilla", sklave_anrede="Jonas", dom_anrede="Anna"):
        p_dom = vp.chat(paare.ROLLE_DOM, _DOM, _SUB, stimmung="müde")
        p_sub = vp.chat(paare.ROLLE_SUB, _SUB, _DOM)
    for p in (p_dom, p_sub):
        for verboten in ("Herrin", "Sklav", "Domina", "der Coach", "Coach-", "Creampie", "Strapon", "[AUFGABE"):
            assert verboten not in p.replace("Kein [AUFGABE: ...] Tag", ""), verboten
        assert "gleichberechtigt" in p and "DISKRETION" in p and "KEIN Machtgefälle" in p
    assert "Du sprichst direkt mit Anna" in p_dom
    assert "Jonas (ein Mann) ist ihr Partner" in p_dom
    assert "No-Gos von Anna: Dritte" in p_dom and "No-Gos von Jonas: Fotos" in p_dom
    assert "- Massagen" in p_dom and "- Dessous" in p_dom         # eigene + Partner-Vorlieben (Hintergrund)
    assert '"""müde"""' in p_dom
    assert "Du sprichst direkt mit Jonas" in p_sub and "Anna (eine Frau) ist seine Partnerin" in p_sub
    assert "No-Gos von Jonas: Fotos" in p_sub and "No-Gos von Anna: Dritte" in p_sub
    assert "Dein Name ist" not in p_sub
    with _modus("vanilla", bot_name="Mira"):
        assert "Dein Name ist Mira" in vp.chat(paare.ROLLE_SUB, _SUB, _DOM)


def test_prompt_ohne_anreden_dekliniert():
    with _modus("vanilla"):
        p = vp.chat(paare.ROLLE_DOM, _DOM, _SUB)
        assert "mit der Partnerin" in p and "Ihr Partner ist ein Mann" in p
        assert "No-Gos von der Partnerin" in p and "No-Gos von ihrem Partner" in p
        k = vp.kurz(paare.ROLLE_SUB, _SUB, _DOM)
        assert "mit dem Partner" in k and "Seine Partnerin ist eine Frau" in k
    with _modus("vanilla", dom_geschlecht="mann", sub_geschlecht="mann"):
        p = vp.chat(paare.ROLLE_SUB, _SUB, _DOM)
        assert "mit dem Partner" in p and "Sein Partner ist ein Mann" in p
        assert "Partnerin" not in p


def test_stimmung_und_impuls_prompts():
    with _modus("vanilla", sklave_anrede="Jonas", dom_anrede="Anna"):
        s, u = vp.stimmung_frage(paare.ROLLE_SUB, vermeiden=["Wie war dein Tag heute so?"],
                                 richtung="wo der Kopf heute steht")
        assert "Frag Jonas kurz" in s and "wo der Kopf heute steht" in s and '"Wie war dein"' in s
        s, u = vp.stimmung_reaktion(paare.ROLLE_DOM, "gut drauf")
        assert "Anna hat dir gerade" in s and "gut drauf" in u
        s, u = vp.stimmung_hinweis(paare.ROLLE_SUB, "müde")
        assert "Jonas hat dir gerade gesagt, wie es ihm geht. Sag Anna" in s and "müde" in u
        s, u = vp.impuls(paare.ROLLE_SUB, ["Massage", "Date_Night"], _SUB, _DOM,
                         letzte_impulse=["Koch ihr was"], stimmung="müde")
        assert "Schick Jonas EINEN kleinen Impuls" in s and "Massage, Date Night" in s
        assert "Eine Einladung, kein Befehl" in s
        assert "No-Gos von Jonas (NIEMALS): Fotos" in u and "No-Gos von Anna (NIEMALS): Dritte" in u
        assert "Koch ihr was" in u and "müde" in u


def test_platzhalter_regeln():
    with _modus("vanilla"):
        pl = vp.platzhalter(paare.ROLLE_SUB)
        assert pl["{ich_nom}"] == "er" and pl["{partner_nom}"] == "seine Partnerin"
        assert pl["{partner_dat}"] == "seiner Partnerin" and pl["{ich_akk_poss}"] == "seinen"
    with _modus("vanilla", dom_anrede="Anna"):
        assert vp.platzhalter(paare.ROLLE_SUB)["{partner_nom}"] == "Anna"


# ---------------------------------------------------------------- t_modus

def test_t_modus():
    with _modus(""):
        assert t_modus("ONBOARDING_SKLAVE_SCHRITT_2") == t("ONBOARDING_SKLAVE_SCHRITT_2")
    with _modus("kink"):
        assert t_modus("ONBOARDING_SKLAVE_SCHRITT_2") == t("ONBOARDING_SKLAVE_SCHRITT_2")  # keine Kink-Variante
    with _modus("vanilla"):
        assert t_modus("ONBOARDING_SKLAVE_SCHRITT_2") == t("ONBOARDING_SKLAVE_SCHRITT_2_VANILLA")
        assert "Herrin" not in t_modus("ONBOARDING_SKLAVE_BEGRUESSUNG")
        assert t_modus("COMMON_ABGEBROCHEN") == t("COMMON_ABGEBROCHEN")   # ohne Variante → Basis
        assert "Stimmung" in t_modus("ONBOARDING_DOMINA_ZUSAMMENFASSUNG", sprache="", modus="", rollen="",
                                     stil="", erfahrungsstand="", interessen="", grenzen="", ziele="",
                                     tempo="", zeiten="")


# ---------------------------------------------------------------- Menüs / Gates

def test_menues_und_commands():
    with _modus(""):
        assert ck.menue_fuer(paare.ROLLE_DOM) == ck.domina_menue()
        assert ck.menue_fuer(paare.ROLLE_SUB) == ck.sklave_menue()
    with _modus("vanilla"):
        m = ck.menue_fuer(paare.ROLLE_DOM)
        assert m == ck.menue_fuer(paare.ROLLE_SUB) == ck.vanilla_menue()
        namen = [c for c, _ in m]
        assert "stimmung" in namen and "einstellungen" in namen and "aufgaben" not in namen
    cmds = ck.alle_vanilla_commands()
    assert {"stimmung", "mood", "einstellungen", "hilfe", "abbrechen"} <= cmds
    assert "wuerfel" not in cmds and "roulette" not in cmds and "strafen" not in cmds


async def test_command_gate_vanilla():
    class _Stop(Exception):
        pass
    alt = gates.ApplicationHandlerStop
    gates.ApplicationHandlerStop = _Stop

    def _upd(text, chat):
        msg = MagicMock()
        msg.text = text
        msg.reply_text = AsyncMock()
        u = MagicMock()
        u.callback_query = None
        u.effective_message = msg
        u.effective_chat.id = chat
        return u, msg
    try:
        with _modus("vanilla"):
            u, msg = _upd("/wuerfel", "111")
            try:
                await gates.pause_guard(u, MagicMock())
                raise AssertionError("Dom-Command hätte im Vanilla-Modus stoppen müssen")
            except _Stop:
                pass
            assert msg.reply_text.await_args.args[0] == t("COMMON_NICHT_IM_VANILLA")
            for erlaubt in ("/stimmung", "/mood@bot", "/einstellungen", "/start", "hallo"):
                u, msg = _upd(erlaubt, "222")
                await gates.pause_guard(u, MagicMock())
                assert msg.reply_text.await_count == 0
            u, msg = _upd("/wuerfel", "999")        # fremder Chat: kein Hinweis (kein Leak)
            await gates.pause_guard(u, MagicMock())
            assert msg.reply_text.await_count == 0
        with _modus(""):
            u, msg = _upd("/wuerfel", "111")        # BDSM: unverändert durch
            await gates.pause_guard(u, MagicMock())
            assert msg.reply_text.await_count == 0
    finally:
        gates.ApplicationHandlerStop = alt


async def test_job_guard_vanilla():
    laeufe = []

    @basis._job_guard
    async def followup_job(bot):
        laeufe.append("followup")

    @basis._job_guard
    async def stimmung_job(bot):
        laeufe.append("stimmung")

    @basis._job_guard
    @basis._nachholbar(max_stunden=5)
    async def vanilla_impuls_job(bot):
        laeufe.append("impuls")

    with _modus("vanilla"):
        await followup_job(None)
        await stimmung_job(None)
        await vanilla_impuls_job(None)
    assert laeufe == ["stimmung", "impuls"]
    laeufe.clear()
    with _modus(""):
        await followup_job(None)
        await stimmung_job(None)
    assert laeufe == ["followup", "stimmung"]
    assert "vanilla_impuls_job" in basis.VANILLA_JOBS and "stimmung_job" in basis.VANILLA_JOBS


# ---------------------------------------------------------------- Routing / Stimmung

async def test_routing_vanilla_beide_seiten():
    orig = (routing.safeword.check_and_handle, vh.handle, routing.stimmung.handle_antwort,
            routing.domina.handle, routing.sklave.handle)
    routing.safeword.check_and_handle = AsyncMock(return_value=False)
    vh.handle = AsyncMock()
    routing.stimmung.handle_antwort = AsyncMock()
    routing.domina.handle = AsyncMock()
    routing.sklave.handle = AsyncMock()

    def _upd(chat):
        u = MagicMock()
        u.message.text = "hallo du"
        u.effective_chat.id = chat
        return u
    try:
        with _modus("vanilla"):
            await routing.handle_message(_upd("111"), MagicMock())
            await routing.handle_message(_upd("222"), MagicMock())
            assert [c.args[2] for c in vh.handle.await_args_list] == [paare.ROLLE_DOM, paare.ROLLE_SUB]
            assert routing.domina.handle.await_count == 0 and routing.sklave.handle.await_count == 0
            # offene Stimmungsfrage im Dom-Chat → Antwort-Pfad, nicht Chat
            stimmung._merke_offen("111")
            await routing.handle_message(_upd("111"), MagicMock())
            assert routing.stimmung.handle_antwort.await_count == 1 and vh.handle.await_count == 2
        with _modus(""):
            await routing.handle_message(_upd("111"), MagicMock())
            assert routing.domina.handle.await_count == 1 and vh.handle.await_count == 2
    finally:
        state._state.clear()
        (routing.safeword.check_and_handle, vh.handle, routing.stimmung.handle_antwort,
         routing.domina.handle, routing.sklave.handle) = orig


async def test_stimmung_frage_an_beide():
    orig = (stimmung._frage_text, stimmung._merke_frage, config.STIMMUNG_ENABLED,
            stimmung.InlineKeyboardButton, stimmung.InlineKeyboardMarkup)
    stimmung._frage_text = AsyncMock(return_value=("Wie geht's?", "r"))
    stimmung._merke_frage = AsyncMock()
    stimmung.InlineKeyboardButton = MagicMock()
    stimmung.InlineKeyboardMarkup = MagicMock()
    config.STIMMUNG_ENABLED = True
    bot = MagicMock()
    bot.send_message = AsyncMock()
    try:
        with _modus("vanilla"):
            await stimmung.frage_stellen(bot)
            chats = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
            assert chats == ["111", "222"]
            assert [c.args[0] for c in stimmung._frage_text.await_args_list] == [paare.ROLLE_DOM, paare.ROLLE_SUB]
            assert stimmung.wartet_auf_antwort("111") and stimmung.wartet_auf_antwort("222")
            assert stimmung._darf("111") and stimmung._darf("222")
        state._state.clear()
        bot.send_message.reset_mock()
        with _modus(""):
            await stimmung.frage_stellen(bot)
            assert [c.kwargs["chat_id"] for c in bot.send_message.await_args_list] == ["222"]
            assert not stimmung._darf("111") and stimmung._darf("222")
    finally:
        state._state.clear()
        (stimmung._frage_text, stimmung._merke_frage, config.STIMMUNG_ENABLED,
         stimmung.InlineKeyboardButton, stimmung.InlineKeyboardMarkup) = orig


# ---------------------------------------------------------------- Onboarding / Einstellungen / coachregel

async def test_onboarding_sub_wartet_auf_einrichter():
    orig = db_profile.get_user_profile
    db_profile.get_user_profile = AsyncMock(return_value=None)
    u = MagicMock()
    u.effective_chat.id = "222"
    u.message.reply_text = AsyncMock()
    try:
        with _modus("vanilla"):
            assert await onboarding.start_if_needed(u, MagicMock(), "sklave") is False
            assert u.message.reply_text.await_args.args[0] == t("ONBOARDING_WARTE_AUF_EINRICHTER")
            assert state.get_mode("222") != "onboarding"
            # Dom-Seite startet normal
            u2 = MagicMock()
            u2.effective_chat.id = "111"
            u2.message.reply_text = AsyncMock()
            assert await onboarding.start_if_needed(u2, MagicMock(), "domina") is False
            assert state.get_mode("111") == "onboarding"
    finally:
        state._state.clear()
        db_profile.get_user_profile = orig


def test_einstellungen_menue_vanilla():
    with _modus("vanilla", dom_anrede="Anna"):
        text = einstellungen._menu_text()
        assert "Stimme:" in text and "Anreden: Anna / —" in text
        assert "Wer soll der Bot" in einstellungen._stil_hinweis()
        assert "beide Anreden" in einstellungen._anrede_hinweis()
    with _modus(""):
        text = einstellungen._menu_text()
        assert "Persönlichkeits\\-Stil" in text and "Anreden:" not in text


async def test_coachregel_rollen_gate():
    orig = (db_cr.get_coach_regel, db_cr.set_coach_regel_status)
    db_cr.set_coach_regel_status = AsyncMock()

    def _q(chat):
        u = MagicMock()
        u.callback_query.data = "coachregel:nein:p1"
        u.callback_query.answer = AsyncMock()
        u.callback_query.edit_message_reply_markup = AsyncMock()
        u.callback_query.edit_message_text = AsyncMock()
        u.effective_chat.id = chat
        return u
    try:
        # Profil-Vorschlag fürs Sklaven-Profil: Sub darf (vorher vom Gate verworfen), Dom nicht
        db_cr.get_coach_regel = AsyncMock(return_value={"typ": "profil_update", "profile_user": "sklave",
                                                        "status": "pending"})
        await coach_regeln.callback_bestaetigen(_q("111"), MagicMock())
        assert db_cr.set_coach_regel_status.await_count == 0
        await coach_regeln.callback_bestaetigen(_q("222"), MagicMock())
        assert db_cr.set_coach_regel_status.await_count == 1
        # Coach-Regel: nur Dom
        db_cr.get_coach_regel = AsyncMock(return_value={"typ": "regel", "status": "pending"})
        await coach_regeln.callback_bestaetigen(_q("222"), MagicMock())
        assert db_cr.set_coach_regel_status.await_count == 1
        await coach_regeln.callback_bestaetigen(_q("111"), MagicMock())
        assert db_cr.set_coach_regel_status.await_count == 2
        assert not any(p == "coachregel:" for p, _ in gates._CALLBACK_ROLLEN)
    finally:
        db_cr.get_coach_regel, db_cr.set_coach_regel_status = orig


def main():
    sync = [test_modus_und_strafen, test_presets_vanilla, test_chat_prompt_beide_seiten,
            test_prompt_ohne_anreden_dekliniert, test_stimmung_und_impuls_prompts,
            test_platzhalter_regeln, test_t_modus, test_menues_und_commands,
            test_einstellungen_menue_vanilla]
    async_ = [test_command_gate_vanilla, test_job_guard_vanilla, test_routing_vanilla_beide_seiten,
              test_stimmung_frage_an_beide, test_onboarding_sub_wartet_auf_einrichter,
              test_coachregel_rollen_gate]
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
    print("FEHLER" if fehler else "✅ Alle Vanilla-Tests bestanden")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
