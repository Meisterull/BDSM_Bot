"""
Regressions-Tests für bot/prompts/persona_presets.py – Markdown-Preset-Loader,
Custom-Preset-Verzeichnis und die überschreibbaren Verhaltensregel-Templates
(Veröffentlichungs-Schritt 3, 2026-07-03).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_presets.py
"""
import contextlib
import os
import shutil
import sys
import tempfile

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

from bot import config  # noqa: E402
from bot.prompts import persona_presets, rollen, sklave  # noqa: E402
from bot.services import persona_config  # noqa: E402


@contextlib.contextmanager
def _rollen(dom: str, sub: str):
    alt = dict(persona_config._cache)
    persona_config._cache["dom_geschlecht"] = dom
    persona_config._cache["sub_geschlecht"] = sub
    try:
        yield
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)


@contextlib.contextmanager
def _custom_dir(dateien: dict):
    """Temporäres PERSONA_PRESETS_DIR mit den gegebenen Dateien (Pfad→Inhalt)."""
    alt = config.PERSONA_PRESETS_DIR
    tmp = tempfile.mkdtemp(prefix="presets_test_")
    try:
        for rel, inhalt in dateien.items():
            pfad = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with open(pfad, "w", encoding="utf-8") as f:
                f.write(inhalt)
        config.PERSONA_PRESETS_DIR = tmp
        persona_presets.reload()
        yield tmp
    finally:
        config.PERSONA_PRESETS_DIR = alt
        persona_presets.reload()
        shutil.rmtree(tmp, ignore_errors=True)


def test_builtin_presets_geladen():
    """Die drei mitgelieferten Presets kommen vollständig aus den .md-Dateien."""
    for key in ("standard", "streng", "verspielt"):
        p = persona_presets.PRESETS[key]
        assert p["label"], key
        for sektion in ("stil_kopf", "stil_fuss", "coach_stil"):
            assert p[sektion].strip(), f"{key}.{sektion} leer"
    assert persona_presets.PRESETS["standard"]["label"] == "Spielerisch-sadistisch (Standard)"
    assert "Spielerisch-sadistisch" in persona_presets.PRESETS["standard"]["stil_kopf"]
    assert "kürzere, kältere Variante" in persona_presets.PRESETS["streng"]["stil_fuss"]
    # streng/verspielt definieren keinen eigenen Coach – erben vom Standard.
    std_coach = persona_presets.PRESETS["standard"]["coach_stil"]
    assert persona_presets.PRESETS["streng"]["coach_stil"] == std_coach
    assert persona_presets.PRESETS["verspielt"]["coach_stil"] == std_coach


def test_stil_key_fuer_eingabe():
    assert persona_presets.stil_key_fuer_eingabe("1") == "standard"
    assert persona_presets.stil_key_fuer_eingabe("2") == "streng"
    assert persona_presets.stil_key_fuer_eingabe("verspielt") == "verspielt"
    assert persona_presets.stil_key_fuer_eingabe("99") is None
    assert persona_presets.stil_key_fuer_eingabe("quatsch") is None
    assert "1️⃣" in persona_presets.stil_hinweis()


def test_regeln_template_fm_wortgleich():
    """Default-Konstellation (F/M): das Template ergibt wortgleich den früher
    hardcodierten Regel-Block aus prompts/sklave.py."""
    with _rollen("", ""):
        regeln = rollen.ersetze_platzhalter(persona_presets.template("regeln_gespraech"))
        assert "wiederhole seinen Wunsch nicht in eigenen Worten zurück. Ihm zu bestätigen" in regeln
        assert "Du bist seine Herrin, keine Datenbank, die seine Wünsche protokolliert" in regeln
        assert "du bist nicht der Coach, du bist die Herrin" in regeln
        assert "deine reale Person (seine echte Domina)" in regeln
        assert 'das richte ich ihr aus' in regeln
        assert "eine Gegenfrage, die ihn weitertreibt. Du bringst die Bewegung rein, nicht er." in regeln
        assert "{" not in regeln  # kein Platzhalter überlebt


def test_regeln_template_andere_kombis():
    with _rollen("mann", "frau"):
        regeln = rollen.ersetze_platzhalter(persona_presets.template("regeln_gespraech"))
        assert "Ihr zu bestätigen, was sie ohnehin gesagt hat" in regeln
        assert "Du bist ihr Herr, keine Datenbank, die ihre Wünsche protokolliert" in regeln
        assert "deine reale Person (ihr echter Dom)" in regeln
        assert "das richte ich ihm aus" in regeln
        assert "du bist nicht der Coach, du bist der Herr" in regeln
    with _rollen("frau", "frau"):
        regeln = rollen.ersetze_platzhalter(persona_presets.template("regeln_gespraech"))
        assert "deine reale Person (ihre echte Domina)" in regeln
        assert "Du bist ihre Herrin" in regeln


def test_sklave_prompt_enthaelt_regeln():
    """sklave.get bettet den ersetzten Regel-Block ein – keine Platzhalter-Leichen."""
    with _rollen("", ""):
        prompt = sklave.get(hard_limits=["Blut"], vorlieben=["Wachs"])
        assert "FÜHREN STATT SPIEGELN" in prompt
        assert "WORTVIELFALT" in prompt
        assert "ERFINDE KEINE ZAHLEN" in prompt
        assert "{sub_" not in prompt and "{dom_" not in prompt


def test_custom_preset_und_template_override():
    eigenes = (
        "label: Eiskalt & flüsternd\n\n"
        "## stil_kopf\nSTIL: flüstert nur.\n"
    )
    override = "- NUR-TEST-REGEL für {sub_akk}.\n"
    with _custom_dir({"eiskalt.md": eigenes,
                      "templates/regeln_gespraech.md": override}):
        p = persona_presets.PRESETS["eiskalt"]
        assert p["label"] == "Eiskalt & flüsternd"
        assert p["stil_kopf"] == "STIL: flüstert nur."
        # Fehlende Sektionen erben vom Standard (Coach bleibt die Freundin).
        assert p["stil_fuss"] == persona_presets.PRESETS["standard"]["stil_fuss"]
        assert p["coach_stil"] == persona_presets.PRESETS["standard"]["coach_stil"]
        # Custom-Presets sind wählbar (Nummer hinter den Built-ins).
        assert persona_presets.stil_key_fuer_eingabe("4") == "eiskalt"
        # Template-Override ersetzt den Default, Platzhalter funktionieren.
        with _rollen("", ""):
            regeln = rollen.ersetze_platzhalter(persona_presets.template("regeln_gespraech"))
        assert regeln == "- NUR-TEST-REGEL für ihn."
    # Nach dem Aufräumen: Built-ins wiederhergestellt.
    assert "eiskalt" not in persona_presets.PRESETS
    assert "NUR-TEST-REGEL" not in persona_presets.template("regeln_gespraech")


def test_kaputtes_custom_preset_wird_uebersprungen():
    with _custom_dir({"kaputt.md": "label: Ohne Stilkopf\n\nnur Fließtext ohne Sektion\n"}):
        assert "kaputt" not in persona_presets.PRESETS
        assert "standard" in persona_presets.PRESETS  # Built-ins unbeeinträchtigt


def test_kombi_helpers():
    assert rollen.kombi_label(*rollen.KOMBIS[0]) == "Herrin & Sklave"
    assert rollen.kombi_fuer_eingabe("2") == ("mann", "frau")
    assert rollen.kombi_fuer_eingabe("5") is None
    assert rollen.kombi_fuer_eingabe("bla") is None
    assert "1️⃣ Herrin & Sklave" in rollen.kombi_hinweis()
    with _rollen("mann", "mann"):
        assert rollen.aktuelle_kombi_label() == "Herr & Sklave"


def _run():
    test_builtin_presets_geladen()
    test_stil_key_fuer_eingabe()
    test_regeln_template_fm_wortgleich()
    test_regeln_template_andere_kombis()
    test_sklave_prompt_enthaelt_regeln()
    test_custom_preset_und_template_override()
    test_kaputtes_custom_preset_wird_uebersprungen()
    test_kombi_helpers()
    print("✅ Alle Preset-Tests bestanden")


if __name__ == "__main__":
    _run()
