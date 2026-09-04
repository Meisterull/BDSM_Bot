"""
Regressions-Tests für die Spaß-Features (2026-07-03): Zeitfenster-Check
(Blitzaufgaben), geheime Abzeichen, Wett-Auflösung, Würfel-Kandidaten.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_spass.py
"""
import asyncio
import contextlib
import os
import sys
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

from bot.services import zeiten, punkte, qdrant  # noqa: E402


# --------------------------------------------------------------------------
# zeiten.ist_im_fenster (Blitz-Fenster + kinderfreie Zeiten)
# --------------------------------------------------------------------------

def _um(stunde: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 3, stunde, minute)


def test_ist_im_fenster():
    assert zeiten.ist_im_fenster(_um(12), ["09:00-21:00"]) is True
    assert zeiten.ist_im_fenster(_um(8, 59), ["09:00-21:00"]) is False
    assert zeiten.ist_im_fenster(_um(21, 1), ["09:00-21:00"]) is False
    # Über-Mitternacht-Fenster (Kinder schlafen)
    assert zeiten.ist_im_fenster(_um(23), ["21:00-06:00"]) is True
    assert zeiten.ist_im_fenster(_um(5), ["21:00-06:00"]) is True
    assert zeiten.ist_im_fenster(_um(12), ["21:00-06:00"]) is False
    # Leere Liste = immer frei; kaputte Einträge erlauben nichts zusätzlich
    assert zeiten.ist_im_fenster(_um(3), []) is True
    assert zeiten.ist_im_fenster(_um(12), ["quatsch"]) is False
    assert zeiten.ist_im_fenster(_um(12), ["quatsch", "11:00-13:00"]) is True


# --------------------------------------------------------------------------
# Geheime Abzeichen (reine Prüf-Funktionen)
# --------------------------------------------------------------------------

def test_geheime_abzeichen_bedingungen():
    nachts = datetime(2026, 7, 3, 3, 30)
    tags = datetime(2026, 7, 3, 15, 0)
    frisch = {"erteilt_am": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()}
    alt = {"erteilt_am": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()}

    ids = {a["id"] for a in punkte._prüfe_geheime_abzeichen(
        task=frisch, gefuehl_text="x" * 500, jetzt=nachts, tasks_heute=3, vorhandene=set())}
    assert ids == {"nachtaktiv", "romanautor", "blitz", "dreifach"}

    ids = {a["id"] for a in punkte._prüfe_geheime_abzeichen(
        task=alt, gefuehl_text="kurz", jetzt=tags, tasks_heute=1, vorhandene=set())}
    assert ids == set()

    # Bereits vorhandene werden nicht erneut vergeben
    ids = {a["id"] for a in punkte._prüfe_geheime_abzeichen(
        task=frisch, gefuehl_text="x" * 500, jetzt=nachts, tasks_heute=3,
        vorhandene={"nachtaktiv", "romanautor", "blitz", "dreifach"})}
    assert ids == set()

    # Kaputter Timestamp crasht nicht
    assert punkte._innerhalb_stunde_erledigt({"erteilt_am": "quatsch"}) is False
    assert punkte._innerhalb_stunde_erledigt({}) is False


def test_format_abzeichen_geheim_marker():
    text = punkte.format_abzeichen(["erster_task", "nachtaktiv"])
    assert "Erster Schritt" in text
    assert "Nachtaktiv" in text and "🤫" in text
    # Unverdiente geheime tauchen nicht auf
    assert "Romanautor" not in text


# --------------------------------------------------------------------------
# Wette: Gewinn in task_erledigt, Verlust in task_nicht_erledigt
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _fake_qdrant(profil: dict):
    """Ersetzt die von punkte genutzten qdrant-Funktionen durch Fakes."""
    patches = {}
    alt = {}

    async def get_user_profile(_uid):
        return profil

    async def patch_profile_fields(_uid, felder):
        patches.update(felder)

    async def _null(*_a, **_k):
        return 0

    async def _leer(*_a, **_k):
        return set()

    ersatz = {
        "get_user_profile": get_user_profile,
        "patch_profile_fields": patch_profile_fields,
        "get_completed_task_count": _null,
        "get_completed_kategorien_set": _leer,
        "get_completed_count_by_kategorie": lambda *_a, **_k: _hoch(),
    }

    async def _hoch():
        return 99

    for name, fn in ersatz.items():
        alt[name] = getattr(qdrant, name)
        setattr(qdrant, name, fn)
    try:
        yield patches
    finally:
        for name, fn in alt.items():
            setattr(qdrant, name, fn)


def test_wette_gewonnen_bei_erledigt():
    profil = {"punkte": 100, "wette": {"einsatz": 25}, "abzeichen": []}
    with _fake_qdrant(profil) as patches:
        ergebnis = asyncio.run(punkte.task_erledigt({"kategorie": ""}))
    namen = [n for n, _ in ergebnis["boni"]]
    assert any("Wette gewonnen" in n for n in namen)
    gewinn = dict(ergebnis["boni"])["Wette gewonnen 🎰"]
    assert gewinn == 50
    assert patches["wette"] == {}  # Wette aufgelöst


def test_wette_bleibt_bei_blitz():
    profil = {"punkte": 100, "wette": {"einsatz": 25}, "abzeichen": []}
    with _fake_qdrant(profil) as patches:
        ergebnis = asyncio.run(punkte.task_erledigt({"kategorie": "", "quelle": "blitz"}))
    namen = [n for n, _ in ergebnis["boni"]]
    assert not any("Wette" in n for n in namen)
    assert any("Blitz" in n for n in namen)
    assert "wette" not in patches  # Wette unangetastet


def test_wette_verloren_bei_nicht_erledigt():
    profil = {"punkte": 100, "streak": 4, "wette": {"einsatz": 25}}
    with _fake_qdrant(profil) as patches:
        verloren = asyncio.run(punkte.task_nicht_erledigt())
    assert verloren == 25
    assert patches["wette"] == {}
    assert patches["streak"] == 0

    # Ohne Wette: 0, kein wette-Patch
    with _fake_qdrant({"punkte": 100, "streak": 4}) as patches:
        verloren = asyncio.run(punkte.task_nicht_erledigt())
    assert verloren == 0
    assert "wette" not in patches


# --------------------------------------------------------------------------
# Event-Arcs: Datums-Parsing
# --------------------------------------------------------------------------

def test_event_parse_datum():
    from datetime import date
    from bot.handlers import event_arc
    heute = event_arc._heute()

    d = event_arc.parse_datum("24.12.2030")
    assert d == date(2030, 12, 24)
    # Ohne Jahr: nächstes Vorkommen (nie in der Vergangenheit)
    d = event_arc.parse_datum("24.12.")
    assert d is not None and d >= heute and (d.day, d.month) == (24, 12)
    gestern = heute - timedelta(days=1)
    d = event_arc.parse_datum(f"{gestern.day:02d}.{gestern.month:02d}.")
    assert d is not None and d.year == heute.year + 1
    # Murks
    assert event_arc.parse_datum("32.13.") is None
    assert event_arc.parse_datum("morgen") is None
    assert event_arc.parse_datum("") is None


# --------------------------------------------------------------------------
# Kategorien-Filter nach Rollen-Konstellation
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _konstellation(dom: str, sub: str):
    from bot.services import persona_config
    alt = dict(persona_config._cache)
    persona_config._cache["dom_geschlecht"] = dom
    persona_config._cache["sub_geschlecht"] = sub
    try:
        yield
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)


def test_kategorien_konstellations_filter():
    from bot import config
    from bot.services import kategorie_logik as kl

    # F/M (Default): kompletter Katalog, unverändert (Bestandsverhalten)
    with _konstellation("", ""):
        assert kl.katalog_kategorien() == list(config.AUFGABEN_KATEGORIEN)
        assert kl.alle_kategorien({}) == list(config.AUFGABEN_KATEGORIEN)

    with _konstellation("mann", "frau"):
        pool = set(kl.katalog_kategorien())
        for raus in ("Muschianbetung", "Pegging", "Strap_on", "Prostatamassage",
                     "Sissy_Training", "Feminisierung"):
            assert raus not in pool, raus
        assert {"Creampie_Cleanup", "Sperma_Schlucken", "Anal", "Spanking"} <= pool

    with _konstellation("frau", "frau"):
        pool = set(kl.katalog_kategorien())
        for raus in ("Pegging", "Prostatamassage", "Sissy_Training", "Feminisierung",
                     "Creampie_Cleanup", "Sperma_Schlucken"):
            assert raus not in pool, raus
        assert {"Muschianbetung", "Strap_on", "Facesitting"} <= pool

    with _konstellation("mann", "mann"):
        pool = set(kl.katalog_kategorien())
        for raus in ("Muschianbetung", "Pegging", "Strap_on"):
            assert raus not in pool, raus
        assert {"Prostatamassage", "Sissy_Training", "Creampie_Cleanup"} <= pool

    # Eigene Kategorien bleiben ungefiltert
    with _konstellation("frau", "frau"):
        pool = kl.alle_kategorien({"eigene_kategorien": ["Mein_Spezialding"]})
        assert "Mein_Spezialding" in pool


# --------------------------------------------------------------------------
# Anti-Spiegel-Anfang (Sklave-Chat, Live-Befund 04.07.)
# --------------------------------------------------------------------------

def test_ist_spiegel_anfang():
    from bot.handlers import sklave as sk

    # Nachgestellte Fälle (Struktur wie Live-Befund 04.07., Inhalte synthetisch):
    # Antwort beginnt mit Echo seiner Worte
    assert sk._ist_spiegel_anfang(
        "Gelangweilt ohne Seil, Kleine Maus? Dann hol es und übe die Knoten.",
        "Eher gelangweilt, es fehlt das seil", []) is True
    assert sk._ist_spiegel_anfang(
        "Ziemlich hektisch also, Kleine Maus. Gut, dass du gleich kniest.",
        "Es geht, ziemlich hektisch", []) is True

    # Anfangs-Template-Recycling gegen letzte Antworten
    assert sk._ist_spiegel_anfang(
        "Langeweile also, Kleine Maus? Dann bekommst du etwas zu tun.",
        "mir ist fad",
        ["Leere also, Kleine Maus? Dann hol das Seil und übe die Knoten."]) is True

    # Gute Antworten (Führung statt Spiegel) lösen NICHT aus
    assert sk._ist_spiegel_anfang(
        "Dann gebe ich dir jetzt etwas zu tun. Langeweile gibt es bei mir nicht.",
        "Eher gelangweilt, es fehlt das seil", []) is False
    assert sk._ist_spiegel_anfang(
        "Ich habe an dich gedacht heute. Zieh dich aus und warte fünf Minuten kniend.",
        "Es geht, ziemlich hektisch",
        ["Dann gebe ich dir jetzt etwas zu tun. Langeweile gibt es bei mir nicht."]) is False


def test_dauermotive():
    from bot.handlers import sklave as sk
    # Nachgestellt (Muster wie Live-Befund: ein Requisit + eine Schlussformel in
    # fast jeder Antwort, Inhalte synthetisch)
    antworten = [
        "Leer also? Dann hol das Seil und binde die Handgelenke – es bleibt dran, bis meine Hand in deinem Nacken liegt.",
        "Sieben Runden? Dann bleibt das Seil jetzt noch länger dran, bis meine Hand in deinem Nacken liegt.",
        "Ein kurzes also? Dann binde das Seil jetzt fest und lass es dran, bis meine Hand später in deinem Nacken liegt.",
        "Gelangweilt ohne Seil? Dann hol es und binde es fest, bis die Unruhe weg ist.",
    ]
    motive = sk._dauermotive(antworten, "hallo, wie gehts dir heute?", anrede="Kleine Maus")
    assert "seil" in motive, motive
    # Spricht ER das Motiv selbst an, wird es NICHT gesperrt
    motive = sk._dauermotive(antworten, "wo soll das seil hin?", anrede="Kleine Maus")
    assert "seil" not in motive, motive
    # Anrede wird nie zum Dauermotiv
    assert "maus" not in sk._dauermotive(
        ["Kleine Maus, gut.", "Kleine Maus, brav.", "Kleine Maus, weiter.", "Kleine Maus, los."],
        "hi", anrede="Kleine Maus")
    # Zu wenig Verlauf → keine Sperre
    assert sk._dauermotive(antworten[:2], "hi", anrede="") == []


def test_wiederholte_phrase_und_frage():
    from bot.handlers import sklave as sk
    # Nachgestellt (Muster wie Live-Befund Runde 3, Inhalte synthetisch):
    # Binnen-Phrase wortgleich recycelt
    alt = ["Zäher Tag und nun auch noch Unruhe? Interessiert mich wirklich, wie es dir damit geht."]
    neu = "Interessiert mich wirklich, wie es dir heute geht, wenn du sagst gut. Hol das Seil raus."
    assert sk._wiederholte_phrase(neu, alt) is True
    # Gleiches Thema, neue Worte → kein Trigger
    assert sk._wiederholte_phrase(
        "Gut gelaunt gefällst du mir. Heute Abend zeige ich dir, wofür ich das nutze.", alt) is False
    # Frage-Erkennung
    assert sk._ist_frage("was könnte den passieren?") is True
    assert sk._ist_frage("kannst du mich aufmuntern") is True
    assert sk._ist_frage("mir ist langweilig") is False
    assert sk._ist_frage("gut") is False


# --------------------------------------------------------------------------
# Spiel-Impuls: Wett-Lage + Job-Gate
# --------------------------------------------------------------------------

def test_wette_angebots_lage():
    from bot.handlers import wette

    async def _tasks_regulaer(*_a, **_k):
        return [{"quelle": "wochenplan"}]

    async def _tasks_nur_blitz(*_a, **_k):
        return [{"quelle": "blitz"}]

    alt = qdrant.get_tasks_by_status
    try:
        qdrant.get_tasks_by_status = _tasks_regulaer
        assert asyncio.run(wette._angebots_lage({"punkte": 50})) == "ok"
        assert asyncio.run(wette._angebots_lage(
            {"punkte": 50, "wette": {"einsatz": 10}})) == "aktiv"
        assert asyncio.run(wette._angebots_lage({"punkte": 5})) == "zu_wenig"
        qdrant.get_tasks_by_status = _tasks_nur_blitz
        assert asyncio.run(wette._angebots_lage({"punkte": 50})) == "keine_aufgabe"
    finally:
        qdrant.get_tasks_by_status = alt


def test_spiel_impuls_gate():
    """Env-Gate aus → Job kehrt sofort um (und die Verdrahtung importiert sauber)."""
    from bot import config
    from bot.scheduler import followup
    alt = config.SPIEL_IMPULS
    try:
        config.SPIEL_IMPULS = False
        asyncio.run(followup.spiel_impuls_job(None))
    finally:
        config.SPIEL_IMPULS = alt


def _run():
    test_ist_im_fenster()
    test_event_parse_datum()
    test_kategorien_konstellations_filter()
    test_ist_spiegel_anfang()
    test_dauermotive()
    test_wiederholte_phrase_und_frage()
    test_geheime_abzeichen_bedingungen()
    test_format_abzeichen_geheim_marker()
    test_wette_gewonnen_bei_erledigt()
    test_wette_bleibt_bei_blitz()
    test_wette_verloren_bei_nicht_erledigt()
    test_wette_angebots_lage()
    test_spiel_impuls_gate()
    print("✅ Alle Spaß-Feature-Tests bestanden")


if __name__ == "__main__":
    _run()
