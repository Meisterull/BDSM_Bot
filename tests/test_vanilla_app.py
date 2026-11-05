"""
Tests für die Mini-App im Vanilla-Modus (Etappe 4, 05.11.2026):
services/miniapp/vanilla (_uebersicht_vanilla, _aktion), Fassaden-Einträge,
Kern-Funktionen aus handlers/vanilla_spiele, die App und Chat teilen.

    python3 tests/test_vanilla_app.py
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

from bot import state  # noqa: E402
from bot.services import miniapp, paare, persona_config  # noqa: E402
from bot.services.miniapp import vanilla as mv, kern  # noqa: E402
from bot.services.db import profile as db_profile, tasks as db_tasks, progress as db_progress  # noqa: E402
from bot.services.db import vorschlaege as db_vorschlaege  # noqa: E402
from bot.handlers import vanilla_spiele as vs  # noqa: E402
from bot.scheduler import vanilla as sv  # noqa: E402


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


_PROFILE = {
    "domina": {"punkte": 120, "streak": 3, "streak_max": 7, "interessen": ["Massagen"], "grenzen": ["Dritte"],
               "gutscheine_eingeloest": 1, "vanilla_wette": {"einsatz": 10}},
    "sklave": {"punkte": 40, "streak": 1, "vorlieben": ["Dessous"], "hard_limits": ["Fotos"]},
}
_TASKS = {
    "domina": [{"qdrant_point_id": "a1", "aufgabe": "Füße hoch", "status": "offen", "erteilt_am": "2026-11-05T10:00:00+00:00",
                "quelle": "vanilla_impuls", "user_id": "domina"},
               {"qdrant_point_id": "a2", "aufgabe": "Alt", "status": "erledigt", "erteilt_am": "2026-11-01T10:00:00+00:00",
                "quelle": "vanilla_wuerfel", "user_id": "domina"}],
    "sklave": [{"qdrant_point_id": "b1", "aufgabe": "Kino", "status": "geplant", "termin_datum": "2026-11-12",
                "quelle": "vanilla_termin", "user_id": "sklave"}],
}


async def _get_tasks(status_list, limit=100, sort_by_datum=False, user_id="sklave", quelle=None):
    out = [x for x in _TASKS.get(user_id, []) if x.get("status") in status_list]
    if quelle:
        out = [x for x in out if x.get("quelle") == quelle]
    return out


def _patch_db():
    orig = (db_profile.get_user_profile, db_tasks.get_tasks_by_status, db_progress.get_completed_task_count,
            db_vorschlaege.get_latest_stimmung, sv._letzte_wochenplaene)
    db_profile.get_user_profile = AsyncMock(side_effect=lambda uid: dict(_PROFILE.get(uid, {})))
    db_tasks.get_tasks_by_status = _get_tasks
    db_progress.get_completed_task_count = AsyncMock(return_value=5)
    db_vorschlaege.get_latest_stimmung = AsyncMock(side_effect=lambda uid, max_stunden=None: {"zusammenfassung": "müde"} if uid == "sklave" else None)
    sv._letzte_wochenplaene = AsyncMock(return_value=["Dienstag: Massage"])

    def restore():
        (db_profile.get_user_profile, db_tasks.get_tasks_by_status, db_progress.get_completed_task_count,
         db_vorschlaege.get_latest_stimmung, sv._letzte_wochenplaene) = orig
    return restore


async def test_uebersicht_vanilla_beide_seiten():
    restore = _patch_db()
    try:
        with _modus("vanilla", sklave_anrede="Jonas"):
            d = await mv._uebersicht_vanilla("1", paare.ROLLE_DOM)
            assert d["modus"] == "vanilla" and d["rolle"] == "domina" and d["ich"] == ""
            assert d["partner_name"] == "Jonas" and d["punkte"] == 120 and d["rang"]["titel"] == "Flamme"
            assert d["rang"]["naechster"] == "Glut" and d["wette_einsatz"] == 10
            assert d["partner"] == {"punkte": 40, "rang": "Funke", "streak": 1, "stimmung": "müde"}
            assert [x["id"] for x in d["offene_tasks"]] == ["a1"] and d["letzte_tasks"][0]["status"] == "erledigt"
            assert d["termine"] == [{"text": "Kino", "datum": "2026-11-12", "von": "partner"}]
            assert d["wochenplan"] == "Dienstag: Massage" and d["stimmung"] == ""
            assert set(d["profil_listen"]) == {"interessen", "grenzen", "inventar", "inventar_wunsch"}
            assert [g["id"] for g in d["gutscheine"] if g["moeglich"]] == ["kuschelstunde", "fruehstueck", "massage", "freie_wahl", "date_abend"]
            assert d["wett_einsaetze"] == [10, 25, 50] and d["stimme"] == "Vertraute Freundin (Standard)"
            d2 = await mv._uebersicht_vanilla("1", paare.ROLLE_SUB)
            assert d2["ich"] == "Jonas" and d2["partner_name"] == "deine Partnerin"
            assert d2["offene_tasks"] == [] and d2["termine"][0]["von"] == "ich"
            assert d2["stimmung"] == "müde" and d2["partner"]["stimmung"] == ""
            assert d2["wett_einsaetze"] == [10, 25] and set(d2["profil_listen"]) >= {"vorlieben", "hard_limits"}
    finally:
        restore()


async def test_aktion_dispatch():
    orig = (vs.aufgabe_abschliessen, vs.wuerfel_idee, vs.wunsch_senden, vs.gutschein_einloesen,
            vs.wette_platzieren, vs.termin_anlegen, sv.wochenplan_senden, kern._BOT)
    vs.aufgabe_abschliessen = AsyncMock(return_value={"ok": True, "zeile": "+10"})
    vs.wuerfel_idee = AsyncMock(return_value={"ok": True, "text": "Idee"})
    vs.wunsch_senden = AsyncMock(return_value={"ok": True})
    vs.gutschein_einloesen = AsyncMock(return_value={"ok": True})
    vs.wette_platzieren = AsyncMock(return_value={"ok": True})
    vs.termin_anlegen = AsyncMock(return_value={"ok": True})
    sv.wochenplan_senden = AsyncMock(return_value=False)
    kern._BOT = "BOT"
    try:
        with _modus("vanilla"):
            assert (await mv._aktion("1", "sklave", "erledigt", {"task_id": "a1", "ja": False}))["ok"]
            assert vs.aufgabe_abschliessen.await_args.args == ("BOT", "sklave", "a1", False)
            assert (await mv._aktion("1", "sklave", "erledigt", {}))["fehler"]
            assert (await mv._aktion("1", "domina", "wuerfel", {}))["text"] == "Idee"
            await mv._aktion("1", "domina", "wunsch", {"text": "Massage"})
            assert vs.wunsch_senden.await_args.args == ("BOT", "domina", "Massage")
            await mv._aktion("1", "domina", "gutschein", {"id": "massage"})
            assert vs.gutschein_einloesen.await_args.args[2] == "massage"
            await mv._aktion("1", "domina", "wette", {"einsatz": "25"})
            assert vs.wette_platzieren.await_args.args[2] == 25
            await mv._aktion("1", "domina", "wette", {"einsatz": "x"})
            assert vs.wette_platzieren.await_args.args[2] == 0
            await mv._aktion("1", "sklave", "termin", {"text": "Samstag Kino"})
            assert vs.termin_anlegen.await_args.args[2] == "Samstag Kino"
            assert (await mv._aktion("1", "sklave", "wochenplan", {"thema": "Nähe"}))["fehler"]
            assert sv.wochenplan_senden.await_args.kwargs["thema"] == "Nähe"
            assert (await mv._aktion("1", "sklave", "quatsch", {}))["fehler"] == "unbekannte Aktion"
    finally:
        (vs.aufgabe_abschliessen, vs.wuerfel_idee, vs.wunsch_senden, vs.gutschein_einloesen,
         vs.wette_platzieren, vs.termin_anlegen, sv.wochenplan_senden, kern._BOT) = orig


def test_fassade_und_aktionen():
    assert miniapp._uebersicht_vanilla is mv._uebersicht_vanilla
    assert miniapp._aktion is mv._aktion
    assert set(mv.AKTIONEN) == {"erledigt", "wuerfel", "wunsch", "gutschein", "wette", "termin", "wochenplan"}
    # die Seite kennt dieselben Aktionen
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "webapp", "index.html"), encoding="utf-8").read()
    for name in mv.AKTIONEN:
        assert f'"{name}"' in html, name
    assert 'd.modus === "vanilla"' in html and 'id="vanilla"' in html


async def test_wette_kern_braucht_offene_idee():
    orig = (db_tasks.get_tasks_by_status, db_profile.get_user_profile, db_profile.patch_profile_fields,
            vs.telegram_helper.send_an, vs.grok.simple)
    db_tasks.get_tasks_by_status = AsyncMock(return_value=[])
    db_profile.get_user_profile = AsyncMock(return_value={"punkte": 50})
    db_profile.patch_profile_fields = AsyncMock()
    vs.telegram_helper.send_an = AsyncMock()
    vs.grok.simple = AsyncMock(return_value="Los!")
    try:
        with _modus("vanilla"):
            assert (await vs.wette_platzieren("BOT", "sklave", 25))["fehler"]
            db_tasks.get_tasks_by_status = AsyncMock(return_value=[{"qdrant_point_id": "x", "status": "offen", "erteilt_am": ""}])
            e = await vs.wette_platzieren("BOT", "sklave", 25)
            assert e["ok"] and e["rest"] == 25 and vs.telegram_helper.send_an.await_count == 2
            assert (await vs.wette_platzieren("BOT", "sklave", 7))["fehler"]   # kein gültiger Einsatz
    finally:
        (db_tasks.get_tasks_by_status, db_profile.get_user_profile, db_profile.patch_profile_fields,
         vs.telegram_helper.send_an, vs.grok.simple) = orig


def main():
    sync = [test_fassade_und_aktionen]
    async_ = [test_uebersicht_vanilla_beide_seiten, test_aktion_dispatch, test_wette_kern_braucht_offene_idee]
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
    print("FEHLER" if fehler else "✅ Alle Vanilla-App-Tests bestanden")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
