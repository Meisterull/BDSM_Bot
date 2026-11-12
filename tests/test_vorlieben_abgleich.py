"""
Regressions-Tests für den Vorlieben-Abgleich der Vorschläge (2026-11-12):
Dislikes aus mag_nicht_-Tags, Dislike-Detektor + Retry, Domina-Grenzen und
bereinigte Cluster-Beispiele im Prompt, Haltungs-Umschreibungen im Limits-Check,
belastbares Niedrig-Signal im Bewertungs-Kontext.

    python3 tests/test_vorlieben_abgleich.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from bot.services import kategorie_logik, limits_check  # noqa: E402
from bot.services.db import auswertung as db_auswertung, kern as db_kern  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.scheduler import tiny_task  # noqa: E402


def test_dislikes_aus_tags():
    d = kategorie_logik.dislike_kategorien
    # Tag allein reicht – auch wenn die Zähler längst weggealtert sind
    assert d({"persoenlichkeit_tags": ["mag_nicht_Dienst", "mag_Pegging", "willig"]}) == ["Dienst"]
    # Zähler-Dislike bleibt, kein Doppel-Eintrag
    prof = {"kategorie_reaktionen": {"Dienst": {"positiv": 0, "negativ": 2}},
            "persoenlichkeit_tags": ["mag_nicht_Dienst"]}
    assert d(prof) == ["Dienst"]
    # Aktuell netto positive Zähler schlagen den alten Tag
    assert d({"kategorie_reaktionen": {"Dienst": {"positiv": 2, "negativ": 1}},
              "persoenlichkeit_tags": ["mag_nicht_Dienst"]}) == []
    # Ausdrücklicher Wunsch schlägt den Tag
    assert d({"wunsch_kategorien": ["Dienst"], "persoenlichkeit_tags": ["mag_nicht_Dienst"]}) == []
    # Robust gegen kaputte Einträge
    assert d({"persoenlichkeit_tags": [None, 7, "mag_nicht_"]}) == []
    assert d(None) == []


def test_dislike_treffer():
    t = kategorie_logik.dislike_treffer
    assert t("Er spielt zehn Minuten dein Dienstmädchen.", ["Dienst"]) == ["Dienst"]
    assert t("Er bringt dir was zu trinken und schweigt.", ["Dienst"]) == ["Dienst"]
    assert t("Er putzt das Bad, bis du zufrieden bist.", ["Dienst"]) == ["Dienst"]
    # Die breite Einsortier-Tabelle (gehorsam/erlaubnis/warten) darf NICHT auslösen
    assert t("Am Dienstag wartet er gehorsam auf deine Erlaubnis.", ["Dienst"]) == []
    # Spielzeug-Namen und bildhafte Komposita sind kein Dienst
    assert t("Dann setzt du den Massager ein, daneben steht der Putzeimer.", ["Dienst"]) == []
    # Kategorien ohne eigene Marker: Keyword-Tabelle, aber nur am Wortanfang
    assert t("Zwanzig Hiebe mit der Peitsche.", ["Peitsche", "Dienst"]) == ["Peitsche"]
    assert t("", ["Dienst"]) == [] and t("egal", []) == []


def test_prompt_grenzen_und_cluster():
    basis = dict(erfahrungsstand="x", level=1, interessen=["Kontrolle"],
                 sklave_vorlieben=[], sklave_hard_limits=[], letzte_aufgaben=["vor 3 Tagen: A"])
    _s, ohne = fp.tiny_task_vorschlag(**basis)
    assert "Persönliche Grenzen" not in ohne
    assert "(Oral, Impact, Anbetung, Demütigung, Dienst, Orgasmus-Kontrolle …)" in ohne
    _s, mit = fp.tiny_task_vorschlag(
        **basis, sklave_dislike_kategorien=["Dienst"],
        domina_grenzen=["Fußanbetung (langweilig)", "Haltungstraining (langweilig)"])
    assert "Persönliche Grenzen (NIEMALS vorschlagen" in mit
    assert "Fußanbetung (langweilig), Haltungstraining (langweilig)" in mit
    assert "(Oral, Impact, Demütigung, Orgasmus-Kontrolle …)" in mit, mit
    assert "❌ Dienst" in mit
    # Wochenend-Variante nutzt denselben Kontext
    _s, ausf = fp.ausfuehrlicher_task_vorschlag(**basis, domina_grenzen=["Fußanbetung"])
    assert "Persönliche Grenzen" in ausf and "Anbetung," not in ausf


def test_lieblings_zeile_wertet_wunsch_nicht_ab():
    _s, prompt = fp.tiny_task_vorschlag(
        erfahrungsstand="x", level=1, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
        sklave_wunsch_kategorien=["Pegging", "Spanking"],
        bewertungs_kontext="Aufgaben die der Domina zuletzt mehrfach weniger gefielen (1-2★): Pegging\n")
    assert "💚 Pegging (bleibt sein Wunsch" in prompt, prompt
    assert "schwach bewertet" not in prompt
    assert "💚 Spanking\n" in prompt


async def test_haltung_umschreibungen():
    gr = ["Haltungstraining (langweilig)"]
    v = limits_check.verletzungen
    assert await v("Knie zusammen, Hände auf den Oberschenkeln, zehn Minuten.", [], gr)
    assert await v("Er soll aufrecht sitzen und den Rücken gerade halten.", [], gr)
    assert await v("Er hält die Position, bis du nickst.", [], gr) == []  # bewusst nicht breiter
    assert await v("Er kniet vor dir, Hände auf dem Rücken.", [], gr) == []


async def test_bewertungs_kontext_schwelle_und_fenster():
    jetzt = datetime.now(timezone.utc)
    def p(kat, sterne, tage):
        return MagicMock(payload={"kategorie": kat, "domina_bewertung": sterne,
                                  "erteilt_am": (jetzt - timedelta(days=tage)).isoformat()})
    orig_aio, orig_mk = db_kern._aio, db_kern.mandanten_key
    db_kern.mandanten_key = lambda u: u
    try:
        # eine einzelne alte 1★ → kein Niedrig-Signal; 4★ bleibt
        db_kern._aio = AsyncMock(return_value=([p("Pegging", 1, 120), p("Spanking", 5, 200)], None))
        k = await db_auswertung.get_bewertungs_kontext("sklave")
        assert "weniger gefielen" not in k and "gut gefielen (4-5★): Spanking" in k, k
        # eine frische reicht nicht, zwei frische schon; alte zählen nicht mit
        db_kern._aio = AsyncMock(return_value=([p("Pegging", 1, 5), p("Pegging", 2, 90)], None))
        assert "weniger gefielen" not in await db_auswertung.get_bewertungs_kontext("sklave")
        db_kern._aio = AsyncMock(return_value=([p("Pegging", 1, 5), p("Pegging", 2, 40),
                                                p("allgemein", 1, 1), p("allgemein", 1, 2)], None))
        k = await db_auswertung.get_bewertungs_kontext("sklave")
        assert "der Domina zuletzt mehrfach weniger gefielen (1-2★): Pegging\n" in k, k
        # kaputtes Datum → kein Signal, kein Crash
        kaputt = [MagicMock(payload={"kategorie": "Pegging", "domina_bewertung": 1, "erteilt_am": x})
                  for x in ("", None)]
        db_kern._aio = AsyncMock(return_value=(kaputt, None))
        assert await db_auswertung.get_bewertungs_kontext("sklave") == ""
    finally:
        db_kern._aio, db_kern.mandanten_key = orig_aio, orig_mk


async def test_dislike_sichern():
    orig = tiny_task.limits_check.generate_mit_limit_retry
    schlecht = "Er spielt zehn Minuten dein Dienstmädchen."
    try:
        # kein Treffer → kein LLM-Call
        gen = AsyncMock(return_value="NEU")
        tiny_task.limits_check.generate_mit_limit_retry = gen
        assert await tiny_task._dislike_sichern("Zwanzig Hiebe.", "P", "S", [], [], ["Dienst"]) == "Zwanzig Hiebe."
        gen.assert_not_called()
        # Treffer → genau ein Retry, sauberer Neuversuch gewinnt
        gut = "Du lässt ihn zwanzig Hiebe mitzählen."
        gen = AsyncMock(return_value=gut)
        tiny_task.limits_check.generate_mit_limit_retry = gen
        assert await tiny_task._dislike_sichern(schlecht, "P", "S", [], [], ["Dienst"]) == gut
        assert gen.call_count == 1 and "ACHTUNG" in gen.call_args.args[0]
        # Retry bedient die Kategorie weiter / ist leer / wirft → Original bleibt
        for rueck in ("Er serviert dir den Kaffee.", None):
            tiny_task.limits_check.generate_mit_limit_retry = AsyncMock(return_value=rueck)
            assert await tiny_task._dislike_sichern(schlecht, "P", "S", [], [], ["Dienst"]) == schlecht
        tiny_task.limits_check.generate_mit_limit_retry = AsyncMock(side_effect=RuntimeError("x"))
        assert await tiny_task._dislike_sichern(schlecht, "P", "S", [], [], ["Dienst"]) == schlecht
        # Spannungsfeld (Domina 4-5★) ist ausdrücklich erlaubt → keine Prüfung
        gen = AsyncMock(return_value="NEU")
        tiny_task.limits_check.generate_mit_limit_retry = gen
        hoch = "Aufgaben die der Domina gut gefielen (4-5★): Dienst\n"
        assert await tiny_task._dislike_sichern(schlecht, "P", "S", [], [], ["Dienst"], hoch) == schlecht
        gen.assert_not_called()
    finally:
        tiny_task.limits_check.generate_mit_limit_retry = orig


def test_domina_signal_alter_und_gewicht():
    jetzt = datetime.now(timezone.utc)
    def sig(pos, neg, tage):
        return {"positiv": pos, "negativ": neg,
                "letztes_signal": (jetzt - timedelta(days=tage)).isoformat()}
    n = kategorie_logik.domina_netto_effektiv
    assert n(sig(1, 0, 10)) == 1 and n(sig(1, 0, 59)) == 1
    assert n(sig(1, 0, 61)) == 0                     # altes Einzelsignal verblasst
    assert n(sig(5, 0, 100)) == 4 and n(sig(5, 0, 400)) == 0
    assert n(sig(0, 2, 70)) == -1 and n(sig(0, 1, 70)) == 0
    assert n({"positiv": 2, "negativ": 0}) == 2      # ohne Zeitstempel unverändert
    assert n({"positiv": 2, "letztes_signal": "kaputt"}) == 2 and n(None) == 0
    praef = {"A": sig(1, 0, 5), "B": sig(5, 0, 5), "C": sig(1, 0, 200),
             "D": sig(0, 3, 5), "E": sig(0, 1, 5)}
    assert kategorie_logik.domina_praeferenz_rangliste(praef) == (["B", "A"], ["D", "E"])
    _s, prompt = fp.tiny_task_vorschlag(
        erfahrungsstand="x", level=1, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
        domina_kategorie_praeferenzen=praef)
    assert "stärkstes Signal zuerst): gern übernommen: B, A – eher abgelehnt: D, E" in prompt, prompt
    assert "zuletzt gern" not in prompt
    # nur verblasste Signale → Zeile entfällt ganz
    _s, leer = fp.tiny_task_vorschlag(
        erfahrungsstand="x", level=1, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
        domina_kategorie_praeferenzen={"C": sig(1, 0, 200)})
    assert "Vorschlags-Feedback" not in leer


def test_steuer_token_raus():
    from bot.services import grok
    o = grok.ohne_steuer_token
    assert o("Kerzen-Spiel am Abend<|eos|>") == "Kerzen-Spiel am Abend"
    assert o("<|im_start|>Text<|im_end|>") == "Text"
    assert o("Pfeil <| bleibt |> und a<b|c>d auch") == "Pfeil <| bleibt |> und a<b|c>d auch"
    assert o("") == "" and o(None) == ""


def _run():
    test_dislikes_aus_tags()
    test_dislike_treffer()
    test_prompt_grenzen_und_cluster()
    test_lieblings_zeile_wertet_wunsch_nicht_ab()
    asyncio.run(test_haltung_umschreibungen())
    asyncio.run(test_bewertungs_kontext_schwelle_und_fenster())
    asyncio.run(test_dislike_sichern())
    test_domina_signal_alter_und_gewicht()
    test_steuer_token_raus()
    print("✅ Alle Vorlieben-Abgleich-Tests bestanden")


if __name__ == "__main__":
    _run()
