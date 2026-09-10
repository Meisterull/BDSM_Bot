"""
Regressions-Tests für das Inventar 🧰 (/inventar, 2026-09-10):
Listen-Bereinigung, Eingabe-Parser, Pflege-Operationen (Cache + Persistenz),
Ausstattungs-Impuls (Sperrliste, Chance, Abwesenheit) und die Prompt-Bausteine
(Wissen + Verbot, kompakt/voll, Wunschliste) samt Einbau in Aufgaben-Kontext,
Bestrafung und Herrin-Persona – plus Struktur-Guards (Router, Katalog, Locales).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_inventar.py
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DOMINA_CHAT_ID"] = "111"
os.environ["SKLAVE_CHAT_ID"] = "222"

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

from bot.services import inventar as inv  # noqa: E402
from bot.services import persona_config as pc  # noqa: E402
from bot.prompts import coach_persona, bestrafung, persona  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Persistenz stubben: patch_profile_fields nur mitschreiben
_gepatcht: list[tuple[str, dict]] = []


async def _fake_patch(user_id, fields, erlaube_geschuetzt=False):
    _gepatcht.append((user_id, dict(fields)))
    return "id"


inv.qdrant.patch_profile_fields = _fake_patch


def _reset(vorhanden=(), wuensche=(), zuletzt=()):
    cache = inv._aktueller_cache()
    cache["vorhanden"] = list(vorhanden)
    cache["wunsch"] = list(wuensche)
    cache["zuletzt"] = list(zuletzt)
    _gepatcht.clear()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Listen + Parser
# ---------------------------------------------------------------------------

def test_saubere_liste():
    roh = ["  Gerte (hart) ", "gerte (hart)", "", 5, "Plug", "x" * 200, "Plug"]
    out = inv.saubere_liste(roh, 10)
    assert out[:2] == ["Gerte (hart)", "Plug"], out
    assert len(out) == 3 and len(out[2]) == inv.MAX_LAENGE
    assert inv.saubere_liste([f"e{i}" for i in range(50)], 40) == [f"e{i}" for i in range(40)]


def test_name():
    assert inv.name("Gerte (hart, nur Po)") == "Gerte"
    assert inv.name("  Plug ") == "Plug"
    assert inv.name("(nur Klammer)") == "(nur Klammer)"


def test_parse_eingabe():
    p = inv.parse_eingabe
    assert p("+ Gerte (hart, nur Po)") == ("add", "Gerte (hart, nur Po)")
    assert p("+Gerte") == ("add", "Gerte")
    assert p("+ wunsch: Käfig") == ("add_wunsch", "Käfig")
    assert p("+ Wunsch - Käfig") == ("add_wunsch", "Käfig")
    assert p("wunsch: Käfig") == ("add_wunsch", "Käfig")
    assert p("+ w: Käfig") == ("add_wunsch", "Käfig")
    assert p("- 3") == ("del", "3")
    assert p("-w2") == ("del", "w2")
    assert p("– 1") == ("del", "1")          # Gedankenstrich (Handy-Autokorrektur)
    assert p("da w1") == ("da", "w1")
    assert p("gekauft w 2") == ("da", "w2")
    # keine Pflege-Eingabe
    assert p("") is None and p("+") is None and p("+ wunsch:") is None
    assert p("- abc") is None and p("da") is None and p("Hallo Herrin") is None


def test_hinzufuegen_und_entfernen():
    _reset()
    assert _run(inv.hinzufuegen("Gerte (hart)")) == (True, "")
    assert _run(inv.hinzufuegen("gerte (HART)")) == (False, "doppelt")
    assert _run(inv.hinzufuegen("   ")) == (False, "leer")
    assert _run(inv.hinzufuegen("Käfig", wunsch=True)) == (True, "")
    assert inv.vorhanden() == ["Gerte (hart)"] and inv.wuensche() == ["Käfig"]
    # Persistenz: Dom-Profil des Legacy-Paars, korrekte Felder
    assert _gepatcht[0][0] == "domina" and _gepatcht[0][1] == {inv.FELD_VORHANDEN: ["Gerte (hart)"]}
    assert _gepatcht[-1][1] == {inv.FELD_WUNSCH: ["Käfig"]}
    # Entfernen per Nummer / w-Nummer; unbekannt → None
    assert _run(inv.entfernen("9")) is None and _run(inv.entfernen("w2")) is None
    assert _run(inv.entfernen("w1")) == "Käfig" and inv.wuensche() == []
    assert _run(inv.entfernen("1")) == "Gerte (hart)" and inv.vorhanden() == []


def test_deckel():
    _reset(vorhanden=[f"e{i}" for i in range(inv.MAX_VORHANDEN)],
           wuensche=[f"w{i}" for i in range(inv.MAX_WUNSCH)])
    assert _run(inv.hinzufuegen("neu")) == (False, "voll")
    assert _run(inv.hinzufuegen("neu", wunsch=True)) == (False, "voll")
    assert _run(inv.angeschafft("w1")) == (None, "voll")


def test_angeschafft_und_wunsch_abraeumen():
    _reset(vorhanden=["Plug"], wuensche=["Käfig (Metall)", "Gerte"])
    assert _run(inv.angeschafft("1")) == (None, "kein_wunsch")
    assert _run(inv.angeschafft("w5")) == (None, "unbekannt")
    assert _run(inv.angeschafft("w1")) == ("Käfig (Metall)", "")
    assert inv.vorhanden() == ["Plug", "Käfig (Metall)"] and inv.wuensche() == ["Gerte"]
    # Ein neu als vorhanden eingetragener Gegenstand räumt den gleichnamigen Wunsch ab
    assert _run(inv.hinzufuegen("Gerte (hart, nur Po)")) == (True, "")
    assert inv.wuensche() == []
    assert any(inv.FELD_WUNSCH in f for _, f in _gepatcht)


def test_anzeige():
    v, w = inv.anzeige(["Gerte"], [])
    assert v == "1. Gerte" and w == "–"
    v, w = inv.anzeige([], ["Käfig", "Seil"])
    assert v == "–" and w == "w1. Käfig\nw2. Seil"


# ---------------------------------------------------------------------------
# Ausstattungs-Impuls
# ---------------------------------------------------------------------------

def test_waehle_impuls_sperrliste():
    liste = ["a", "b", "c"]
    # gesperrte fliegen raus
    for _ in range(20):
        assert inv.waehle_impuls(liste, ["a", "b"]) == "c"
    # alle gesperrt → nur der allerletzte bleibt ausgeschlossen
    for _ in range(20):
        assert inv.waehle_impuls(liste, ["c", "a", "b"]) in ("a", "b")
    assert inv.waehle_impuls([], []) is None
    assert inv.waehle_impuls(["nur"], ["nur"]) == "nur"


def test_impuls_wahl_gates_und_rotation():
    _reset(vorhanden=["a", "b", "c", "d", "e", "f"])
    original = pc.ist_abwesend
    try:
        pc.ist_abwesend = lambda: False
        assert _run(inv.impuls_wahl(chance=0)) is None
        assert inv.zuletzt() == []
        pc.ist_abwesend = lambda: True
        assert _run(inv.impuls_wahl(chance=1.0)) is None, "während Abwesenheit kein Impuls"
        pc.ist_abwesend = lambda: False
        # 6 Züge mit Chance 1: die ersten 5 sind paarweise verschieden (Sperrfenster 5)
        zuege = [_run(inv.impuls_wahl(chance=1.0)) for _ in range(6)]
        assert all(zuege)
        assert len(set(zuege[:5])) == 5, zuege
        assert len(inv.zuletzt()) == inv.ZULETZT_MERKEN
        assert inv.zuletzt()[0] == zuege[-1]
        assert _gepatcht[-1][1] == {inv.FELD_ZULETZT: inv.zuletzt()}
        # leere Liste → nie ein Impuls
        _reset()
        assert _run(inv.impuls_wahl(chance=1.0)) is None
    finally:
        pc.ist_abwesend = original


def test_impuls_wahl_persistenz_best_effort():
    """Qdrant-Fehler beim Sperrlisten-Schreiben darf den Impuls nicht verhindern."""
    _reset(vorhanden=["a", "b"])
    original_patch, original_abw = inv.qdrant.patch_profile_fields, pc.ist_abwesend

    async def _kaputt(*a, **k):
        raise RuntimeError("qdrant weg")

    try:
        inv.qdrant.patch_profile_fields = _kaputt
        pc.ist_abwesend = lambda: False
        wahl = _run(inv.impuls_wahl(chance=1.0))
        assert wahl in ("a", "b") and inv.zuletzt() == [wahl]
    finally:
        inv.qdrant.patch_profile_fields = original_patch
        pc.ist_abwesend = original_abw


# ---------------------------------------------------------------------------
# Prompt-Bausteine
# ---------------------------------------------------------------------------

def test_inventar_block_leer_und_voll():
    _reset()
    assert coach_persona.inventar_block() == ""
    assert coach_persona.inventar_block(kompakt=True) == ""
    _reset(vorhanden=["Gerte (hart, nur Po)", "Plug (Größe M)"], wuensche=["Käfig"])
    voll = coach_persona.inventar_block(perspektive="coach")
    assert "AUSSTATTUNG ZU HAUSE" in voll and "bei den beiden" in voll
    assert "  - Gerte (hart, nur Po)" in voll and "  - Plug (Größe M)" in voll
    assert "nichts verlangen, das hier fehlt" in voll and "Alltagsgegenstände" in voll
    assert "NOCH NICHT VORHANDEN" in voll and "  - Käfig" in voll and "NIE für eine Aufgabe voraussetzen" in voll
    assert "Geschenk" not in voll
    # Coach-Chat: Wunschliste als Geschenkidee markiert
    coach = coach_persona.inventar_block(perspektive="coach", geschenkidee=True)
    assert "Geschenk-/Belohnungsidee" in coach
    # Herrin-Perspektive + kompakt: nur Namen, keine Klammer-Notizen
    kompakt = coach_persona.inventar_block(perspektive="herrin", kompakt=True)
    assert "bei euch" in kompakt and "Gerte, Plug" in kompakt and "nur Po" not in kompakt
    assert "Verlange kein Spielzeug" in kompakt
    # ohne Wünsche keine Wunsch-Sektion
    assert "NOCH NICHT" not in coach_persona.inventar_block(mit_wuenschen=False)
    # nur Wünsche, nichts vorhanden → kein Verbot (fail-open)
    _reset(wuensche=["Käfig"])
    nur_wunsch = coach_persona.inventar_block()
    assert "NOCH NICHT VORHANDEN" in nur_wunsch and "nichts verlangen" not in nur_wunsch


def test_inventar_impuls_block():
    assert coach_persona.inventar_impuls_block("") == ""
    b = coach_persona.inventar_impuls_block("Gerte (hart, nur Po)")
    assert "AUSSTATTUNGS-IMPULS" in b and "„Gerte (hart, nur Po)“" in b and "Pflicht-Kategorie" not in b
    assert "Pflicht-Kategorie" in coach_persona.inventar_impuls_block("Gerte", kategorie_gebunden=True)


def test_sklaven_kontext_block_und_aufgaben_kontext():
    _reset(vorhanden=["Gerte (hart, nur Po)"], wuensche=["Käfig"])
    block = coach_persona.sklaven_kontext_block({"vorlieben": ["Fußmassage"], "hard_limits": []})
    assert "AUSSTATTUNG ZU HAUSE" in block and "Gerte (hart, nur Po)" in block
    basis = dict(erfahrungsstand="Anfänger", level=2, interessen=["Kontrolle"],
                 sklave_vorlieben=["Fußmassage"], sklave_hard_limits=["Blut"],
                 gewaehlte_kategorien=["Anbetung"])
    ohne = fp._aufgaben_kontext(**basis)
    assert "AUSSTATTUNG ZU HAUSE" in ohne and "AUSSTATTUNGS-IMPULS" not in ohne
    assert "MUSS aus mindestens einer dieser Kategorien" in ohne
    mit = fp._aufgaben_kontext(inventar_impuls="Gerte (hart, nur Po)", **basis)
    assert "AUSSTATTUNGS-IMPULS (heute)" in mit and "„Gerte (hart, nur Po)“" in mit
    # Pflicht-Kategorie wird bei aktivem Impuls zur Inspiration (Kombi-Lektion)
    assert "AUSSTATTUNGS-IMPULS unten Vorrang" in mit
    assert "MUSS aus mindestens einer dieser Kategorien" not in mit
    # Kombi hat Vorrang vor dem Inventar-Impuls im Pflicht-Satz
    beides = fp._aufgaben_kontext(inventar_impuls="Gerte", kombi_vorlieben=["a", "b"], **basis)
    assert "KOMBI-IMPULS unten Vorrang" in beides
    # Leeres Inventar → kein Block, keine leere Zeile
    _reset()
    leer = fp._aufgaben_kontext(**basis)
    assert "AUSSTATTUNG" not in leer


def test_bestrafung_und_persona():
    _reset(vorhanden=["Gerte (hart, nur Po)"])
    system, user = bestrafung.bestrafungsvorschlag("Aufgabe X", 3, ["Blut"],
                                                   inventar_impuls="Gerte (hart, nur Po)")
    assert "AUSSTATTUNG ZU HAUSE" in user and "AUSSTATTUNGS-IMPULS" in user
    _, user2 = bestrafung.bestrafungsvorschlag("Aufgabe X", 0, [])
    assert "AUSSTATTUNGS-IMPULS" not in user2 and "AUSSTATTUNG ZU HAUSE" in user2
    # Herrin-Persona: kompakt (Kurz-Prompts) vs. voll (Chat)
    kompakt = persona.fuer_sklaven_prompt()
    assert "AUSSTATTUNG" in kompakt and "nur Po" not in kompakt
    voll = persona.fuer_sklaven_prompt(inventar_voll=True)
    assert "Gerte (hart, nur Po)" in voll
    _reset()
    assert "AUSSTATTUNG" not in persona.fuer_sklaven_prompt()


# ---------------------------------------------------------------------------
# Struktur-Guards
# ---------------------------------------------------------------------------

def test_router_katalog_locales():
    main_src = open(os.path.join(ROOT, "bot", "main.py"), encoding="utf-8").read()
    # Routing VOR der Rollen-Verzweigung (beide Rollen pflegen dieselben Listen)
    pos_route = main_src.find("if mode == inventar.MODE:")
    pos_rollen = main_src.find("if rolle == paare.ROLLE_DOM:\n        if mode == \"wochenplanung_thema\":")
    assert 0 < pos_route < pos_rollen, "Inventar-Routing muss vor der Rollen-Verzweigung stehen"
    assert 'ck.aliases("inventar")' in main_src
    assert "inventar_service.load()" in main_src
    from bot import commands_katalog as ck
    assert "inventar" in ck.alle_domina_commands() and "inventar" in ck.alle_sklave_commands()
    from bot.locales import de, en, commands_en
    for key in ("INVENTAR_ANZEIGE", "INVENTAR_HINZUGEFUEGT", "INVENTAR_WUNSCH_HINZUGEFUEGT",
                "INVENTAR_ENTFERNT", "INVENTAR_ANGESCHAFFT", "INVENTAR_DOPPELT", "INVENTAR_VOLL",
                "INVENTAR_UNBEKANNTE_NUMMER", "INVENTAR_KEIN_WUNSCH", "INVENTAR_UNVERSTANDEN",
                "INVENTAR_FERTIG"):
        assert key in de.MESSAGES and key in en.MESSAGES, key
    assert "inventar" in commands_en.ALIASES and "inventar" in commands_en.BESCHREIBUNGEN
    assert "{inventar}" in de.MESSAGES["PROFIL_SKLAVE"] and "{inventar_wunsch}" in en.MESSAGES["PROFIL_SKLAVE"]


def test_anzeige_template_markdownv2():
    """Das Anzeige-Template ist MarkdownV2: außerhalb von Code-Spans dürfen keine
    unescapten Sonderzeichen stehen (sonst Parse-Fehler → Liste kommt nie an)."""
    from bot.locales import de, en
    for loc in (de, en):
        text = loc.MESSAGES["INVENTAR_ANZEIGE"].replace("{vorhanden}", "").replace("{wuensche}", "")
        ohne_code = re.sub(r"`[^`]*`", "", text)
        ohne_escapes = re.sub(r"\\.", "", ohne_code)
        # erlaubte Marker: * (Fett) paarweise; alles andere aus der V2-Sonderliste verboten
        assert ohne_escapes.count("*") % 2 == 0
        verboten = set("_[]()~>#+-=|{}.!")
        rest = [c for c in ohne_escapes if c in verboten]
        assert not rest, f"unescapte MarkdownV2-Zeichen: {rest}"


if __name__ == "__main__":
    fehler = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✅ {name}")
            except AssertionError as e:
                fehler += 1
                print(f"  ❌ {name}: {e}")
            except Exception as e:  # noqa: BLE001
                fehler += 1
                print(f"  💥 {name}: {type(e).__name__}: {e}")
    sys.exit(1 if fehler else 0)
