"""
Regressions-Tests für die tägliche Stimmungsfrage an den Sub (Befund 27.08.2026:
26. und 27.08. ging zeichengleich dieselbe Frage raus, davor zwei Tage lang
dasselbe Wetter-Bild – und am 25.08. der Richtungs-Seed im Wortlaut).

  S1 – Alle Versuche zu ähnlich → statischer Standardtext, NICHT der als
       Wiederholung erkannte Kandidat.
  S2 – Ein akzeptierter Kandidat wird samt benutzter Richtung zurückgegeben.
  S3 – Retries ziehen Richtungen ohne Zurücklegen und meiden die zuletzt
       benutzten.
  S4 – Die Richtungen sind Stichworte, keine fertigen Fragesätze, und der
       Prompt verbietet das wörtliche Abschreiben.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_stimmung.py
"""
import asyncio
import os
import sys
import tempfile

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

from bot.handlers import stimmung as _st  # noqa: E402
from bot.prompts import followup as _fp  # noqa: E402
from bot.messages import t  # noqa: E402

WETTER = "Ich frage mich gerade, wenn dein Tag ein Wetter wäre – welches würde es sein?"


def _mit_stubs(eintraege, antworten):
    """Patcht Sperr-Listen-Abfrage und LLM; gibt eine Restore-Funktion zurück."""
    orig_q, orig_g = _st.qdrant.get_recent_stimmung_eintraege, _st.grok.simple
    _st.qdrant.get_recent_stimmung_eintraege = AsyncMock(return_value=eintraege)
    rest = list(antworten)
    async def _simple(*a, **k):
        return rest.pop(0) if rest else antworten[-1]
    _st.grok.simple = _simple

    def restore():
        _st.qdrant.get_recent_stimmung_eintraege = orig_q
        _st.grok.simple = orig_g
    return restore


# --------------------------------------------------------------------------
# S1 – erkannte Wiederholung geht NICHT raus
# --------------------------------------------------------------------------

async def test_alle_versuche_zu_aehnlich_faellt_auf_standardtext():
    restore = _mit_stubs([{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}],
                         [WETTER] * 5)
    try:
        frage, richtung = await _st._frage_text()
        assert frage == t("STIMMUNG_FRAGE"), f"Wiederholung gesendet: {frage!r}"
        assert richtung == ""
    finally:
        restore()


# --------------------------------------------------------------------------
# S2 – akzeptierter Kandidat kommt mit seiner Richtung zurück
# --------------------------------------------------------------------------

async def test_frische_frage_wird_mit_richtung_zurueckgegeben():
    restore = _mit_stubs([{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}],
                         ["Sag mir, wie viel Kraft heute noch in dir steckt."])
    try:
        frage, richtung = await _st._frage_text()
        assert frage == "Sag mir, wie viel Kraft heute noch in dir steckt."
        assert richtung in _fp.stimmung_richtungen()
        assert richtung != "ein Wetter-Bild für den heutigen Tag"
    finally:
        restore()


# --------------------------------------------------------------------------
# S2b – verworfener Kandidat landet in der Sperr-Liste des nächsten Versuchs
# --------------------------------------------------------------------------

async def test_verworfener_kandidat_geht_in_die_naechste_sperrliste():
    prompts = []
    orig_q, orig_g = _st.qdrant.get_recent_stimmung_eintraege, _st.grok.simple
    _st.qdrant.get_recent_stimmung_eintraege = AsyncMock(
        return_value=[{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}])
    antworten = [WETTER, "Sag mir, wie schwer der Tag heute auf dir liegt."]

    async def _simple(prompt, **k):
        prompts.append(prompt[0])
        return antworten[len(prompts) - 1]
    _st.grok.simple = _simple
    try:
        frage, _ = await _st._frage_text()
        assert frage == "Sag mir, wie schwer der Tag heute auf dir liegt."
        assert len(prompts) == 2
        assert WETTER not in prompts[0].split("So hast du zuletzt gefragt")[0]
        # Versuch 2 kennt den verworfenen Kandidaten aus Versuch 1
        assert prompts[1].count(WETTER) >= 2
    finally:
        _st.qdrant.get_recent_stimmung_eintraege, _st.grok.simple = orig_q, orig_g


# --------------------------------------------------------------------------
# S3 – Richtungen ohne Zurücklegen, verbrauchte hinten anstellen
# --------------------------------------------------------------------------

def test_richtungen_ohne_zuruecklegen():
    alle = _fp.stimmung_richtungen()
    verbrauchte = set(alle[:5])
    for _ in range(50):
        k = _st._richtungs_kandidaten(verbrauchte)
        assert len(k) == _st._VERSUCHE
        assert len(set(k)) == len(k), f"Richtung doppelt gezogen: {k}"
        assert not (set(k) & verbrauchte), f"Zuletzt benutzte Richtung wiederverwendet: {k}"
    # Notausgang: sind fast alle verbraucht, wird trotzdem aufgefüllt
    k = _st._richtungs_kandidaten(set(alle[1:]))
    assert len(k) == _st._VERSUCHE and len(set(k)) == _st._VERSUCHE


# --------------------------------------------------------------------------
# S4 – Stichworte statt fertiger Fragesätze + Abschreib-Verbot im Prompt
# --------------------------------------------------------------------------

def test_richtungen_sind_stichworte():
    import re
    for r in _fp.stimmung_richtungen():
        assert not r.endswith("?"), f"Fertiger Fragesatz als Seed: {r!r}"
        assert not r[0].isupper(), f"Seed liest sich wie ein eigener Satz: {r!r}"
        # Keine 2. Person: "dir/du" meint im Prompt die Dom-Seite. Schreibt das
        # Modell das Stichwort ab, kippt die Anrede auf den Sub und die Richtung
        # dreht sich um (Befund 27.08.2026).
        assert not re.search(r"\b(du|dir|dich|dein\w*)\b", r, re.I), \
            f"Zweite Person im Seed kippt die Richtung: {r!r}"
        # Ebenso keine harten Sub-Pronomen – die Rolle ist konfigurierbar.
        assert not re.search(r"\b(er|ihn|ihm|sein\w*|sie|ihr\w*)\b", r, re.I), \
            f"Festes Rollen-Pronomen im Seed: {r!r}"


def test_prompt_verbietet_woertliche_uebernahme():
    system, _ = _fp.stimmung_abfragen(vermeiden=[WETTER], richtung="wo der Kopf heute steht")
    assert "wo der Kopf heute steht" in system
    assert "NICHT wörtlich" in system
    assert WETTER in system  # Sperr-Liste steht drin


# --------------------------------------------------------------------------
# S5 – die gesperrten Satzanfänge stehen wörtlich im Prompt
# --------------------------------------------------------------------------

def test_prompt_nennt_gesperrte_satzanfaenge():
    vermeiden = [
        "Ich frage mich gerade, wie dein Tag war?",
        "Ich frage mich, was dich heute trägt?",
        "Wenn dein Tag ein Wetter wäre – welches?",
        "Kurz",  # zu kurz für einen Anfang – darf nicht crashen
    ]
    system, _ = _fp.stimmung_abfragen(vermeiden=vermeiden, richtung="wo der Kopf heute steht")
    assert '"Ich frage mich"' in system
    assert '"Wenn dein Tag"' in system
    # Dubletten nur einmal, "Kurz" gar nicht
    assert system.count('"Ich frage mich"') == 1
    assert '"Kurz"' not in system


def test_satzanfaenge_dedupe_und_kurzfilter():
    assert _fp._satzanfaenge(["Ich frage mich, was?", "ich frage mich noch mehr", "Zu kurz"]) == \
        ["Ich frage mich"]
    assert _fp._satzanfaenge([]) == []
    assert _fp._satzanfaenge(["", None]) == []


def main():
    for coro in (
        test_alle_versuche_zu_aehnlich_faellt_auf_standardtext,
        test_frische_frage_wird_mit_richtung_zurueckgegeben,
        test_verworfener_kandidat_geht_in_die_naechste_sperrliste,
    ):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    for fn in (
        test_richtungen_ohne_zuruecklegen,
        test_richtungen_sind_stichworte,
        test_prompt_verbietet_woertliche_uebernahme,
        test_prompt_nennt_gesperrte_satzanfaenge,
        test_satzanfaenge_dedupe_und_kurzfilter,
    ):
        fn()
        print(f"✅ {fn.__name__}")
    print("✅ Alle Stimmungs-Tests bestanden")


if __name__ == "__main__":
    main()
