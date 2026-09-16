"""
Regressions-Tests Währung ⭐ (16.09.2026, services/waehrung + handlers/waehrung,
Bauplan vom Owner abgenommen):

  - Ränge (Stufe/Titel/nächster, Wechsel auf/ab), Schwellen (nur aufwärts),
    Buchung nie unter 0, Wett-Frist aus dem Ideen-Text
  - Inventar: Wunsch-Preise (nur für bestehende Wünsche), Sparziel = günstigster
    bepreister Wunsch, gewähren (Liste + Preis weg, Gewährt-Liste), Hook für neue
    Wünsche, Anzeige mit Preis/🎯
  - Handler: Rang-Meldung auf/ab, Schwellen-Push mit den zwei teuersten
    leistbaren Privilegien (einmal pro Schwelle), Abzug-Buttons (einmalig),
    Wunschpreis-Frage/-Antwort inkl. Sparziel-Frage, gewähren/später (Ruhe,
    24-h-Gate), Herrin-Wette (Start, Urteils-Job, Urteil, verfallen, Einspruch)
  - Shop: Pause-Tag/Easy Mode raus (aber per id auflösbar), Session nach Wunsch
    → Aufgabe mit 7-Tage-Nachfrage nach Bestätigung

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_waehrung.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
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

from bot import state  # noqa: E402
from bot.services import waehrung as ws  # noqa: E402
from bot.services import inventar as inv  # noqa: E402
from bot.services import qdrant  # noqa: E402
from bot.handlers import waehrung as wh  # noqa: E402
from bot.handlers import privileg  # noqa: E402
from bot.messages import t  # noqa: E402

DOM, SUB = "111", "222"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self):
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.replies.append((text, reply_markup))


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = _Msg()
        self.markup_entfernt = False

    async def answer(self):
        pass

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_entfernt = True


class _Welt:
    """Profile im Speicher, Sends mitschreiben, Inventar-Cache setzen."""

    def __init__(self, punkte=0, wuensche=(), preise=None, dom_extra=None, sub_extra=None):
        self.profile = {
            "sklave": {"punkte": punkte, "wunsch_kategorien": ["Pegging"], **(sub_extra or {})},
            "domina": {"aktuelles_level": 2, **(dom_extra or {})},
        }
        self.patches: list[tuple[str, dict]] = []
        self.dom_sends: list[tuple[str, object]] = []
        self.sub_sends: list[tuple[str, object]] = []
        self.tasks: list[dict] = []
        cache = inv._aktueller_cache()
        cache["vorhanden"], cache["wunsch"], cache["zuletzt"] = [], list(wuensche), []
        cache["preise"], cache["gewaehrt"] = dict(preise or {}), []
        inv.NEUER_WUNSCH_HOOK = None
        for cid in (DOM, SUB):
            s = state.get(cid)
            for k in ("abzug_offen", "wunschpreis_offen", "sparziel_frage"):
                s.pop(k, None)

    async def get_profile(self, uid):
        return dict(self.profile[uid])

    async def patch(self, uid, fields, **kw):
        self.patches.append((uid, dict(fields)))
        self.profile[uid].update(fields)

    async def send_domina(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.dom_sends.append((text, reply_markup))

    async def send_sklave(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.sub_sends.append((text, reply_markup))

    async def erstelle_task(self, aufgabe, kategorie, level, **kw):
        self.tasks.append({"aufgabe": aufgabe, "kategorie": kategorie, "level": level, **kw})
        return "task-1"

    def install(self):
        qdrant.get_user_profile = self.get_profile
        qdrant.patch_profile_fields = self.patch
        qdrant.erstelle_task = self.erstelle_task
        wh.telegram_helper.send_domina = self.send_domina
        wh.telegram_helper.send_sklave = self.send_sklave
        wh.sticker_reaktionen.sende_sklave = AsyncMock(return_value=True)
        ws.frist_iso = lambda tage: (datetime.now(timezone.utc) + timedelta(days=tage)).isoformat()
        wh._BOT = object()
        return self

    @property
    def punkte(self):
        return self.profile["sklave"]["punkte"]


def _press(fn, data, chat):
    q = _Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(chat)))
    _run(fn(u, SimpleNamespace(bot=object())))
    return q


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------

def test_raenge_und_schwellen():
    assert ws.rang(0) == (0, "Frischling") and ws.rang(99)[1] == "Frischling"
    assert ws.rang(100)[1] == "Fußschemel" and ws.rang(490)[1] == "Laufbursche"
    assert ws.rang(500)[1] == "Diener" and ws.rang(5000)[1] == "Ihr Eigentum"
    assert ws.naechster_rang(490) == (500, "Diener") and ws.naechster_rang(2000) is None
    assert ws.rang_wechsel(490, 510) == "auf" and ws.rang_wechsel(510, 490) == "ab"
    assert ws.rang_wechsel(300, 310) is None
    assert ws.schwelle_ueberschritten(490, 510) == 500
    assert ws.schwelle_ueberschritten(95, 215) == 200      # zwei übersprungen → höchste
    assert ws.schwelle_ueberschritten(500, 590) is None    # keine neue volle Hundert
    assert ws.schwelle_ueberschritten(510, 490) is None    # abwärts nie
    assert ws.schwelle_ueberschritten(0, 100) == 100 and ws.schwelle_ueberschritten(0, 50) is None


def test_buchen_nie_unter_null():
    w = _Welt(punkte=30).install()
    b = _run(ws.buchen(-50, "test"))
    assert b == {"alt": 30, "neu": 0, "delta": -30} and w.punkte == 0
    b = _run(ws.buchen(50, "test"))
    assert b["neu"] == 50 and w.punkte == 50
    n = len(w.patches)
    assert _run(ws.buchen(-0, "nichts"))["delta"] == 0 and len(w.patches) == n


def test_wett_frist_tage():
    heute = date(2026, 9, 16)  # Mittwoch
    assert ws.wett_frist_tage("Wer in den nächsten zwei Tagen öfter …", heute) == 2
    assert ws.wett_frist_tage("Bis morgen Abend durchhalten", heute) == 1
    assert ws.wett_frist_tage("3 Tage lang den Plug tragen", heute) == 3
    assert ws.wett_frist_tage("die nächsten 24 Stunden", heute) == 1
    assert ws.wett_frist_tage("bis übermorgen", heute) == 2
    assert ws.wett_frist_tage("Bis Freitag zehn Liegestütze", heute) == 2
    assert ws.wett_frist_tage("Wer zuerst lacht, verliert", heute) == ws.WETT_FRIST_DEFAULT_TAGE
    assert ws.wett_frist_tage("in 30 Tagen", heute) == ws.WETT_FRIST_MAX_TAGE
    assert ws.wett_frist_tage("", heute) == ws.WETT_FRIST_DEFAULT_TAGE


# --------------------------------------------------------------------------
# Inventar: Preise + Sparziel
# --------------------------------------------------------------------------

def test_inventar_preise_sparziel_gewaehren():
    w = _Welt(punkte=0, wuensche=["Käfig", "Augenbinde aus Seide", "Peitsche"]).install()
    assert inv.sparziel() is None and inv.preis("Käfig") is None
    assert _run(inv.setze_preis("käfig", 500)) == "Käfig"
    assert _run(inv.setze_preis("Augenbinde aus Seide", 200)) == "Augenbinde aus Seide"
    assert _run(inv.setze_preis("gibt es nicht", 300)) is None
    assert inv.sparziel() == ("Augenbinde aus Seide", 200)
    assert inv.preis("KÄFIG") == 500
    # Anzeige: Preis + Zielmarker, unbepreist ohne Zusatz
    _, wz = inv.anzeige([], inv.wuensche(), inv.preise(), inv.sparziel()[0], [])
    assert "w1. Käfig · 500 P" in wz and "w2. Augenbinde aus Seide · 200 P 🎯" in wz and "w3. Peitsche\n" in wz + "\n"
    # Wunsch löschen räumt den Preis ab
    assert _run(inv.entfernen("w1")) == "Käfig"
    assert inv.preis("Käfig") is None and inv.sparziel() == ("Augenbinde aus Seide", 200)
    # gewähren: von der Wunschliste, Preis weg, Gewährt-Liste
    assert _run(inv.gewaehren("augenbinde aus seide")) == "Augenbinde aus Seide"
    assert inv.wuensche() == ["Peitsche"] and inv.gewaehrte() == ["Augenbinde aus Seide"]
    assert inv.sparziel() is None
    _, wz = inv.anzeige([], inv.wuensche(), inv.preise(), None, inv.gewaehrte())
    assert "✅ Augenbinde aus Seide (gewährt)" in wz
    # neu vorhanden räumt den gewährten Wunsch ab
    _run(inv.setze(vorhanden_neu=["Augenbinde aus Seide (Herrin)"]))
    assert inv.gewaehrte() == []
    # Hook nur für NEUE Wünsche
    neu: list = []

    async def hook(text):
        neu.append(text)
    inv.NEUER_WUNSCH_HOOK = hook
    _run(inv.setze(wuensche_neu=["Peitsche", "Käfig"]))
    _run(inv.setze(wuensche_neu=["Käfig"]))
    assert neu == ["Käfig"]
    inv.NEUER_WUNSCH_HOOK = None
    # saubere_preise verwirft Fremdes
    assert inv.saubere_preise({"käfig": "300", "weg": 200, "Käfig": "x"}, ["Käfig"]) == {"Käfig": 300}


# --------------------------------------------------------------------------
# Handler: Rang, Schwelle, Abzug
# --------------------------------------------------------------------------

def test_rang_und_schwellen_push():
    w = _Welt(punkte=510, wuensche=["Käfig"], preise={"Käfig": 500}).install()
    w.profile["domina"][wh.FELD_FRAGE_AM] = datetime.now(timezone.utc).isoformat()  # Sparziel-Frage gerade gestellt
    _run(wh.nach_punkteaenderung(None, 490, 510))
    texte = [s for s, _ in w.sub_sends]
    assert any("Diener" in x and "steigst" in x for x in texte), texte
    push = [(s, m) for s, m in w.sub_sends if s.startswith("⭐")]
    assert len(push) == 1 and "500" in push[0][0] and "Käfig" in push[0][0] and push[0][1] is not None
    assert w.profile["sklave"][wh.FELD_SCHWELLE] == 500
    assert w.dom_sends == [], "24-h-Gate: keine Sparziel-Frage"
    # dieselbe Schwelle nie zweimal, Abwärts nur Rang-Meldung, kein Push
    w.sub_sends.clear()
    _run(wh.nach_punkteaenderung(None, 505, 520))
    assert not any(s.startswith("⭐") for s, _ in w.sub_sends)
    _run(wh.nach_punkteaenderung(None, 520, 480, push=False))
    assert any("fällst" in s and "Laufbursche" in s for s, _ in w.sub_sends)
    assert not any(s.startswith("⭐") for s, _ in w.sub_sends)


def test_abzug_buttons_einmalig():
    w = _Welt(punkte=100).install()
    wh.abzug_anbieten("strafe-1")
    assert "strafe-1" in state.get(DOM)["abzug_offen"]
    q = _press(wh.callback_punkteabzug, "punkteabzug:50:strafe-1", DOM)
    assert q.markup_entfernt and w.punkte == 50
    assert any("50" in r[0] and "abgezogen" in r[0] for r in q.message.replies)
    assert w.sub_sends and "50" in w.sub_sends[0][0]
    assert any("fällst" in s for s, _ in w.sub_sends), "100 → 50 = Rang ab"
    q = _press(wh.callback_punkteabzug, "punkteabzug:25:strafe-1", DOM)
    assert w.punkte == 50 and any("nicht mehr möglich" in r[0] for r in q.message.replies)
    _press(wh.callback_punkteabzug, "punkteabzug:99:strafe-2", DOM)
    assert w.punkte == 50
    # fester Abzug nicht-erledigt, nie unter 0
    b = _run(wh.abzug_nicht_erledigt())
    assert b["delta"] == -ws.ABZUG_NICHT_ERLEDIGT and w.punkte == 50 - ws.ABZUG_NICHT_ERLEDIGT
    w.profile["sklave"]["punkte"] = 5
    assert _run(wh.abzug_nicht_erledigt())["neu"] == 0


# --------------------------------------------------------------------------
# Handler: Sparziel
# --------------------------------------------------------------------------

def test_wunschpreis_und_sparziel_flow():
    w = _Welt(punkte=490, wuensche=["Käfig"]).install()
    _run(wh.frage_wunschpreis("Käfig"))
    assert len(w.dom_sends) == 1 and "Käfig" in w.dom_sends[0][0] and w.dom_sends[0][1] is not None
    kennung = next(iter(state.get(DOM)["wunschpreis_offen"]))
    # 300 → Preis gesetzt, Sub erfährt Sparziel, Stand 490 ≥ 300 → Frage an die Dom-Seite
    q = _press(wh.callback_wunschpreis, f"wunschpreis:300:{kennung}", DOM)
    assert q.markup_entfernt and inv.preis("Käfig") == 300
    assert any("300" in r[0] for r in q.message.replies)
    assert any("Sparziel" in s and "300" in s for s, _ in w.sub_sends)
    frage = [x for x in w.dom_sends if x[0].startswith("🎯")]
    assert len(frage) == 1 and frage[0][1] is not None
    assert state.get(DOM)["sparziel_frage"]["wunsch"] == "Käfig"
    # zweite Prüfung: offene Frage → keine zweite
    assert _run(wh.sparziel_pruefen(None, 490)) is False
    fk = state.get(DOM)["sparziel_frage"]["kennung"]
    # später → Ruhe, keine neue Frage
    q = _press(wh.callback_wunschziel, f"wunschziel:spaeter:{fk}", DOM)
    assert any("frühestens" in r[0] for r in q.message.replies)
    assert "käfig" in w.profile["domina"][wh.FELD_RUHE]
    w.profile["domina"].pop(wh.FELD_FRAGE_AM, None)
    assert _run(wh.sparziel_pruefen(None, 490)) is False, "14 Tage Ruhe"
    # Ruhe abgelaufen → Frage erneut, gewähren bucht ab
    w.profile["domina"][wh.FELD_RUHE] = {"käfig": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()}
    assert _run(wh.sparziel_pruefen(None, 490)) is True
    fk = state.get(DOM)["sparziel_frage"]["kennung"]
    q = _press(wh.callback_wunschziel, f"wunschziel:gewaehren:{fk}", DOM)
    assert w.punkte == 190 and inv.wuensche() == [] and inv.gewaehrte() == ["Käfig"]
    assert any("Gewährt" in r[0] and "190" in r[0] for r in q.message.replies)
    assert any("erspart" in s for s, _ in w.sub_sends)
    assert any("fällst" in s for s, _ in w.sub_sends), "490 → 190 = Rang ab"
    # veraltete Kennung
    q = _press(wh.callback_wunschziel, f"wunschziel:gewaehren:{fk}", DOM)
    assert any("nicht mehr aktuell" in r[0] for r in q.message.replies) and w.punkte == 190
    # Preis-Antwort mit fremder Kennung / ungültigem Preis
    q = _press(wh.callback_wunschpreis, "wunschpreis:300:deadbeef", DOM)
    assert any("nicht mehr aktuell" in r[0] for r in q.message.replies)


def test_sparziel_zu_wenig_und_zeilen():
    w = _Welt(punkte=100, wuensche=["Käfig"], preise={"Käfig": 300}).install()
    state.get(DOM)["sparziel_frage"] = {"kennung": "k1", "wunsch": "Käfig"}
    q = _press(wh.callback_wunschziel, "wunschziel:gewaehren:k1", DOM)
    assert w.punkte == 100 and any("nicht mehr genug" in r[0] for r in q.message.replies)
    assert wh.sparziel_zeile(100) == "Käfig · 100/300 P" and wh.sparziel_zeile(900) == "Käfig · 300/300 P"
    assert "Laufbursche" in wh.rang_zeile(490) and "Diener" in wh.rang_zeile(490)
    assert "höchster" in wh.rang_zeile(2500)


# --------------------------------------------------------------------------
# Handler: Herrin-Wette
# --------------------------------------------------------------------------

def test_herrin_wette_kompletter_lauf():
    w = _Welt(punkte=200).install()
    assert not wh.wette_laeuft(w.profile["sklave"])
    tage = _run(wh.wette_starten("Wer in den nächsten zwei Tagen …", "Ansage", "abc12345"))
    assert tage == 2 and wh.wette_laeuft(w.profile["sklave"])
    hw = w.profile["sklave"][wh.FELD_WETTE]
    assert hw["einsatz"] == 50 and hw["status"] == "laeuft"
    # Job vor der Frist: nichts
    _run(wh.wette_urteil_job(None))
    assert w.sub_sends == []
    # Frist um → Urteils-Frage mit Buttons
    hw["frist"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _run(wh.wette_urteil_job(None))
    assert len(w.sub_sends) == 1 and w.sub_sends[0][1] is not None and "Frist" in w.sub_sends[0][0]
    assert w.profile["sklave"][wh.FELD_WETTE]["status"] == "gefragt"
    _run(wh.wette_urteil_job(None))
    assert len(w.sub_sends) == 1, "Frage nicht doppelt"
    # Sub meldet gewonnen → +50, Dom bekommt Ergebnis mit Einspruch-Button
    q = _press(wh.callback_wetteurteil, "wetteurteil:gewonnen:abc12345", SUB)
    assert q.markup_entfernt and w.punkte == 250
    assert any("Gewonnen" in s for s, _ in w.sub_sends)
    assert w.dom_sends and "gewonnen" in w.dom_sends[-1][0] and w.dom_sends[-1][1] is not None
    assert w.profile["sklave"][wh.FELD_WETTE]["status"] == "entschieden"
    # Doppel-Tap → veraltet
    q = _press(wh.callback_wetteurteil, "wetteurteil:verloren:abc12345", SUB)
    assert w.punkte == 250 and any("schon entschieden" in r[0] for r in q.message.replies)
    # Einspruch kippt: +50 → −50 (Umbuchung 100)
    q = _press(wh.callback_wetteeinspruch, "wetteeinspruch:abc12345", DOM)
    assert w.punkte == 150 and w.profile["sklave"][wh.FELD_WETTE]["status"] == "gekippt"
    assert any("verloren" in r[0] for r in q.message.replies)
    assert any("Einspruch" in s and "verloren" in s for s, _ in w.sub_sends)
    q = _press(wh.callback_wetteeinspruch, "wetteeinspruch:abc12345", DOM)
    assert w.punkte == 150 and any("nicht mehr möglich" in r[0] for r in q.message.replies)
    assert not wh.wette_laeuft(w.profile["sklave"]), "entschiedene Wette blockiert die nächste nicht"


def test_herrin_wette_verfaellt_und_einspruch_frist():
    w = _Welt(punkte=200).install()
    _run(wh.wette_starten("egal", "Ansage", "k2"))
    hw = w.profile["sklave"][wh.FELD_WETTE]
    hw.update({"status": "gefragt",
               "gefragt_am": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()})
    _run(wh.wette_urteil_job(None))
    assert w.punkte == 150 and w.profile["sklave"][wh.FELD_WETTE]["ergebnis"] == "verloren"
    assert any("Keine Meldung" in s for s, _ in w.sub_sends)
    assert "automatisch" in w.dom_sends[-1][0]
    # Einspruch nach 24 h nicht mehr
    w.profile["sklave"][wh.FELD_WETTE]["einspruch_bis"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    q = _press(wh.callback_wetteeinspruch, "wetteeinspruch:k2", DOM)
    assert w.punkte == 150 and any("nicht mehr möglich" in r[0] for r in q.message.replies)


# --------------------------------------------------------------------------
# Shop
# --------------------------------------------------------------------------

def test_shop_katalog_und_session():
    ids = [p["id"] for p in privileg.PRIVILEGIEN]
    assert "pause_tag" not in ids and "easy_mode" not in ids and "session_wunsch" in ids
    assert privileg._privileg_by_id("pause_tag")["kosten"] == 50, "alte Einlösungen bleiben auflösbar"
    assert privileg._privileg_by_id("session_wunsch")["kosten"] == 150
    assert "session_wunsch" in privileg._SOFORT_ANWEISUNG
    w = _Welt(punkte=0).install()
    assert _run(wh.session_wunsch_task()) == "task-1"
    tk = w.tasks[0]
    assert tk["quelle"] == "privileg" and tk["followup_in_tagen"] == 7 and tk["status"] == "offen"
    assert tk["kategorie"] == "Pegging" and "Pegging" in tk["aufgabe"] and tk["level"] == 2


def test_locale_keys():
    for key in ("RANG_AUF", "RANG_AB", "SCHWELLE_KOPF", "ABZUG_SUB", "WUNSCHPREIS_FRAGE",
                "SPARZIEL_ERREICHT", "WETTE_URTEIL_FRAGE", "WETTE_ERGEBNIS_DOM",
                "SESSION_WUNSCH_AUFGABE", "COACH_WETTIDEE_LAEUFT"):
        assert key in __import__("bot.locales.de", fromlist=["MESSAGES"]).MESSAGES
    assert "50" in t("COACH_WETTIDEE_GESENDET", tage=2, einsatz=50)


def _run_alle():
    test_raenge_und_schwellen()
    test_buchen_nie_unter_null()
    test_wett_frist_tage()
    test_inventar_preise_sparziel_gewaehren()
    test_rang_und_schwellen_push()
    test_abzug_buttons_einmalig()
    test_wunschpreis_und_sparziel_flow()
    test_sparziel_zu_wenig_und_zeilen()
    test_herrin_wette_kompletter_lauf()
    test_herrin_wette_verfaellt_und_einspruch_frist()
    test_shop_katalog_und_session()
    test_locale_keys()
    print("✅ Alle Währungs-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
