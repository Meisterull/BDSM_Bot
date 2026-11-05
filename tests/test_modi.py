"""
Tests für den Paar-Modus (BDSM / Kink / Vanilla), Umbau 05.11.2026 Etappe 1:
services/modi.py, services/kataloge.py, Modus-Map in kategorie_logik,
Rollen-Wortwahl in prompts/rollen.py, Presets je Modus.

Leitplanke: ohne gesetzten Modus (= BDSM) bleibt ALLES beim Bestandsverhalten –
die Prompt-Baseline (tests/render_baseline.py) muss byte-identisch bleiben.

    python3 tests/test_modi.py
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402
stubs.aktivieren()

from bot import config  # noqa: E402
from bot.prompts import persona, persona_presets, rollen  # noqa: E402
from bot.services import kataloge, kategorie_logik, modi, persona_config  # noqa: E402


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


# ---------------------------------------------------------------- modi.py

def test_default_ist_bdsm():
    with _modus(""):
        assert modi.aktuell() == "bdsm" and modi.ist_bdsm()
    with _modus("quatsch"):
        assert modi.aktuell() == "bdsm"
    with _modus("KINK "):
        assert modi.ist_kink() and modi.thema() == "Kink"


def test_eingabe_und_hinweis():
    assert modi.fuer_eingabe("1") == "bdsm"
    assert modi.fuer_eingabe("2") == "kink"
    assert modi.fuer_eingabe("kink") == "kink"
    assert modi.fuer_eingabe("3") == "vanilla"   # seit Etappe 2 wählbar
    assert modi.fuer_eingabe("vanilla") == "vanilla"
    assert modi.fuer_eingabe("4") is None
    assert modi.fuer_eingabe("x") is None
    assert "1️⃣" in modi.hinweis() and "Kink" in modi.hinweis() and "Vanilla" in modi.hinweis()


# ---------------------------------------------------------------- Kataloge

def test_bdsm_katalog_unveraendert():
    with _modus(""):
        assert kategorie_logik.katalog_kategorien() == config.AUFGABEN_KATEGORIEN
        assert kategorie_logik.keywords() is kategorie_logik._KEYWORDS
        assert kategorie_logik.nachbarn() is kategorie_logik.KATEGORIE_NACHBARN
        assert kategorie_logik.zu_cluster() is kategorie_logik.KATEGORIE_ZU_CLUSTER
        assert kategorie_logik.keyword_match("Buttplug tragen") == "Buttplug_Tragen"


def test_kink_katalog():
    with _modus("kink"):
        pool = kategorie_logik.katalog_kategorien()
        assert pool == kataloge.KINK_KATEGORIEN
        assert "Toiletten_Sklave" not in pool and "Fisting" not in pool
        assert kategorie_logik.keyword_match("Heute Abend mit Augenbinde") == "Sinnesspiel"
        assert kategorie_logik.keyword_match("Schick mir ein Foto") == "Fotos_Fuereinander"
        assert kategorie_logik.keyword_match("something with a blindfold") == "Sinnesspiel"
        # Nachbarn/Cluster aus den Kink-Tabellen, nicht aus BDSM
        assert "Erotische_Massage" in kategorie_logik.nachbarn()["Sinnesspiel"]
        assert "Pegging" not in kategorie_logik.zu_cluster()
        assert all(k in kategorie_logik.zu_cluster() for k in pool)
        # Konstellation: Kink hat keine anatomischen Voraussetzungen
        with _modus("kink", dom_geschlecht="frau", sub_geschlecht="frau"):
            assert kategorie_logik.katalog_kategorien() == kataloge.KINK_KATEGORIEN


def test_vanilla_katalog_definiert():
    with _modus("vanilla"):
        pool = kategorie_logik.katalog_kategorien()
        assert pool == kataloge.VANILLA_KATEGORIEN
        assert kategorie_logik.keyword_match("Lass uns kuscheln") == "Naehe_Kuscheln"
        assert kategorie_logik.keyword_match("Date night am Freitag") == "Date_Night"


def test_keywords_ohne_substring_fallen():
    harmlos = ["Ich war gestern in Köln", "Das Update ist installiert", "Moralisch gesehen",
               "Die Reise war schön", "Der Preis stimmt", "Wir fahren nach Europa",
               "Ich bin im Hospital", "password vergessen"]
    for m in ("kink", "vanilla"):
        with _modus(m):
            for satz in harmlos:
                assert kategorie_logik.keyword_match(satz) == "allgemein", (m, satz)


def test_alle_kategorien_mit_eigenen():
    with _modus("kink"):
        pool = kategorie_logik.alle_kategorien({"eigene_kategorien": ["Tanzen", "sinnesspiel"]})
        assert pool[:len(kataloge.KINK_KATEGORIEN)] == kataloge.KINK_KATEGORIEN
        assert "Tanzen" in pool and pool.count("Sinnesspiel") == 1


# ---------------------------------------------------------------- Rollen

def test_rollen_bdsm_unveraendert():
    with _modus(""):
        assert rollen.dom()["label"] == "Herrin" and rollen.sub()["label"] == "Sklave"
        assert rollen.dom_poss_aus_sub_sicht() == "seiner Herrin"
        assert rollen.kombi_label("frau", "mann") == "Herrin & Sklave"
        assert "(seine echte Domina)" in rollen.platzhalter()["{dom_real_klammer}"]
        assert "– die Herrin." in rollen.grundierung()
    with _modus("", dom_geschlecht="mann", sub_geschlecht="frau"):
        assert rollen.dom_poss_aus_sub_sicht() == "ihres Herrn"
    with _modus("", dom_geschlecht="mann", sub_geschlecht="mann"):
        assert rollen.dom_poss_aus_sub_sicht() == "seines Herrn"
        assert "du der Herr, er der Sklave" in rollen.grundierung()


def test_rollen_kink_partnerwoerter():
    with _modus("kink"):
        assert rollen.dom()["label"] == "Partnerin" and rollen.dom()["anrede"] == "deine Partnerin"
        assert rollen.sub()["label"] == "Partner" and rollen.sub()["label_gen"] == "des Partners"
        assert rollen.dom_poss_aus_sub_sicht() == "seiner Partnerin"
        assert rollen.aktuelle_kombi_label() == "Partnerin (führt) & Partner"
        assert rollen.platzhalter()["{dom_rolle}"] == "die Partnerin"
        assert "(seine echte Partnerin)" in rollen.platzhalter()["{dom_real_klammer}"]
        g = rollen.grundierung()
        assert "– die Partnerin." in g and "Herrin" not in g and "Sklav" not in g
        assert "kein Sperma und keinen Penis" in g  # Anatomie bleibt geschlechts-, nicht modusabhängig
    with _modus("kink", dom_geschlecht="mann", sub_geschlecht="frau"):
        assert rollen.dom()["anrede"] == "dein Partner" and rollen.sub()["label"] == "Partnerin"
        assert rollen.dom_poss_aus_sub_sicht() == "ihres Partners"
    with _modus("kink", dom_geschlecht="frau", sub_geschlecht="frau"):
        assert "du die Partnerin, sie die Partnerin" in rollen.grundierung()
    with _modus("vanilla"):
        assert rollen.aktuelle_kombi_label() == "Partnerin & Partner"


# ---------------------------------------------------------------- Presets

def test_presets_je_modus():
    assert list(persona_presets.presets_fuer_modus("bdsm")) == ["standard", "streng", "verspielt"]
    assert list(persona_presets.presets_fuer_modus("kink")) == ["kink_standard", "kink_frech"]
    for key in ("kink_standard", "kink_frech"):
        p = persona_presets.PRESETS[key]
        assert p["modus"] == "kink" and p["label"]
        assert "Demütigung" in p["stil_fuss"]  # ausdrücklich verboten
    # kink_frech erbt den Coach vom Kink-Standard, nicht vom BDSM-Standard
    assert persona_presets.PRESETS["kink_frech"]["coach_stil"] == persona_presets.PRESETS["kink_standard"]["coach_stil"]
    assert "Spielart" in persona_presets.PRESETS["kink_standard"]["coach_stil"]
    with _modus(""):
        assert persona_presets.default_key() == "standard"
        assert persona_presets.stil_key_fuer_eingabe("1") == "standard"
        assert persona_presets.stil_key_fuer_eingabe("kink_frech") is None
        assert "Spielerisch-sadistisch" in persona_presets.stil_hinweis()
    with _modus("kink"):
        assert persona_presets.default_key() == "kink_standard"
        assert persona_presets.stil_key_fuer_eingabe("1") == "kink_standard"
        assert persona_presets.stil_key_fuer_eingabe("2") == "kink_frech"
        assert persona_presets.stil_key_fuer_eingabe("streng") is None
        assert "Spielerisch-sadistisch" not in persona_presets.stil_hinweis()
    # Stil-Key eines anderen Modus → Standard des aktuellen Modus
    with _modus("kink", persona_stil="streng"):
        assert persona_presets.aktuelles_preset() is persona_presets.PRESETS["kink_standard"]
    with _modus("kink", persona_stil="kink_frech"):
        assert persona_presets.aktuelles_label() == "Frech & fordernd"


def test_sklaven_prompt_im_kink_modus():
    with _modus("kink", bot_name="", sklave_anrede=""):
        p = persona.fuer_sklaven_prompt()
        assert "Verführerisch-führend" in p
        assert '"deine Partnerin"' in p
        assert "Sklav" not in p and "Herrin" not in p and "Domina" not in p
        assert "Stock-Domina" not in p
    with _modus("", bot_name="", sklave_anrede=""):
        p = persona.fuer_sklaven_prompt()
        assert "Spielerisch-sadistisch" in p and '"deine Herrin"' in p


if __name__ == "__main__":
    import inspect
    fehler = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"  ✅ {name}")
            except Exception as e:  # noqa: BLE001
                fehler += 1
                print(f"  ❌ {name}: {type(e).__name__}: {e}")
    print("FEHLER" if fehler else "ALLE GRÜN")
    sys.exit(1 if fehler else 0)
