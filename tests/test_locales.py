"""
Regressions-Tests für die Locale-Schicht (Veröffentlichungs-Schritt 2, 2026-07-02).

Sichert: en.py trägt exakt die Keys der Referenz-Locale de.py, jede Übersetzung
hat identische {platzhalter}, und der Fallback-Mechanismus in lade() füllt
fehlende Keys mit Deutsch auf.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_locales.py
"""
import os
import re
import sys

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

from bot.locales import de, en, lade  # noqa: E402


def _platzhalter(text: str) -> set:
    """Einfache {name}-Platzhalter; {{escaped}} zählt nicht."""
    return set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", text))


def test_en_keys_vollstaendig():
    fehlt = set(de.MESSAGES) - set(en.MESSAGES)
    fremd = set(en.MESSAGES) - set(de.MESSAGES)
    assert not fehlt, f"en.py fehlen {len(fehlt)} Keys: {sorted(fehlt)[:10]}"
    assert not fremd, f"en.py hat unbekannte Keys: {sorted(fremd)[:10]}"


def test_platzhalter_identisch():
    diff = {
        k: (_platzhalter(de.MESSAGES[k]), _platzhalter(en.MESSAGES[k]))
        for k in de.MESSAGES
        if k in en.MESSAGES and _platzhalter(de.MESSAGES[k]) != _platzhalter(en.MESSAGES[k])
    }
    assert not diff, f"Platzhalter-Abweichungen: {dict(list(diff.items())[:5])}"


def test_lade_fallback_und_overlay():
    de_dict = lade("de")
    en_dict = lade("en")
    assert de_dict["COMMON_ABGEBROCHEN"] != en_dict["COMMON_ABGEBROCHEN"]
    assert set(de_dict) == set(en_dict)  # Overlay ändert den Key-Satz nie
    # Unbekannte Locale → still Deutsch
    assert lade("xx") == de_dict
    assert lade("") == de_dict


def test_safeword_texte_uebersetzt():
    """Safety-Texte dürfen nicht auf den deutschen Fallback angewiesen sein."""
    for key in [k for k in de.MESSAGES if k.startswith("SAFEWORD_")]:
        assert key in en.MESSAGES, f"Safety-Text {key} nicht übersetzt"
        assert en.MESSAGES[key] != de.MESSAGES[key], f"Safety-Text {key} unübersetzt"


def _run():
    test_en_keys_vollstaendig()
    test_platzhalter_identisch()
    test_lade_fallback_und_overlay()
    test_safeword_texte_uebersetzt()
    print("✅ Alle Locale-Tests bestanden")


if __name__ == "__main__":
    _run()
