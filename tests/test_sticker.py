"""
Regressions-Tests für Reaktions-Sticker (2026-07-30):
Mapping-Laden (fehlende/kaputte Datei, mtime-Reload), chance-Gate,
best-effort-Versand an die Sub-Seite – plus Struktur-Guard für den
privileg.py-Fix (Entscheidungs-Block gehört in _entscheidung_anwenden,
nicht in handle_frei_aufgabe; Bug vom Review-D8-Commit).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_sticker.py
"""
import ast
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DOMINA_CHAT_ID"] = "111"
os.environ["SKLAVE_CHAT_ID"] = "222"
_STICKER_FILE = os.path.join(tempfile.mkdtemp(), "reaktions_sticker.json")
os.environ["REAKTIONS_STICKER_FILE"] = _STICKER_FILE

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

from bot.services import sticker_reaktionen as sr  # noqa: E402


class FakeBot:
    def __init__(self):
        self.gesendet = []

    async def send_sticker(self, chat_id, sticker):
        self.gesendet.append((chat_id, sticker))


# Monoton steigende Kunst-mtime: echte Zeitstempel haben (tmpfs) nur ~4 ms
# Auflösung – zwei Writes im selben Tick würden den mtime-Reload maskieren.
_MTIME = [1_000_000_000.0]


def _bump_mtime() -> None:
    _MTIME[0] += 10
    os.utime(_STICKER_FILE, (_MTIME[0], _MTIME[0]))


def _schreibe(mapping: dict) -> None:
    with open(_STICKER_FILE, "w", encoding="utf-8") as f:
        json.dump({"set_name": "test_by_bot", "sticker": mapping}, f)
    _bump_mtime()


def test_fehlende_datei_ist_stiller_noop():
    bot = FakeBot()
    assert asyncio.run(sr.sende_sklave(bot, sr.LOB)) is False
    assert bot.gesendet == []
    assert sr.verfuegbar(sr.LOB) is False
    print("✅ Fehlende Mapping-Datei: stiller No-op")


def test_versand_und_mtime_reload():
    _schreibe({"lob": "FILE_ID_1", "kaputt": 123})  # Nicht-String-Wert fliegt raus
    bot = FakeBot()
    assert asyncio.run(sr.sende_sklave(bot, sr.LOB)) is True
    assert bot.gesendet == [("222", "FILE_ID_1")]  # Sub-Seite des Env-Paars
    assert not sr.verfuegbar("kaputt")

    _schreibe({"lob": "FILE_ID_2"})
    assert asyncio.run(sr.sende_sklave(bot, sr.LOB)) is True
    assert bot.gesendet[-1] == ("222", "FILE_ID_2")
    print("✅ Versand an Sub-Seite + mtime-Reload ohne Neustart")


def test_unbekannter_tag_und_kaputte_datei():
    bot = FakeBot()
    assert asyncio.run(sr.sende_sklave(bot, "gibtsnicht")) is False
    with open(_STICKER_FILE, "w") as f:
        f.write("{kein json")
    _bump_mtime()
    assert asyncio.run(sr.sende_sklave(bot, sr.LOB)) is False
    assert bot.gesendet == []
    print("✅ Unbekannter Tag + kaputtes JSON: kein Crash, kein Versand")


def test_chance_gate():
    _schreibe({"lob": "FILE_ID_3"})
    bot = FakeBot()
    orig = sr.random.random
    try:
        sr.random.random = lambda: 0.9  # über chance → unterdrückt
        assert asyncio.run(sr.sende_sklave(bot, sr.LOB, chance=0.5)) is False
        sr.random.random = lambda: 0.1  # unter chance → sendet
        assert asyncio.run(sr.sende_sklave(bot, sr.LOB, chance=0.5)) is True
    finally:
        sr.random.random = orig
    assert len(bot.gesendet) == 1
    print("✅ chance-Gate drosselt deterministisch")


def test_sende_fehler_bleibt_still():
    _schreibe({"lob": "FILE_ID_4"})

    class KaputterBot:
        async def send_sticker(self, chat_id, sticker):
            raise RuntimeError("Netz weg")

    assert asyncio.run(sr.sende_sklave(KaputterBot(), sr.LOB)) is False
    print("✅ Sende-Fehler: best-effort, keine Exception nach außen")


def test_privileg_entscheidungs_block_richtig_verortet():
    """Guard gegen den verrutschten Sende-Block (Bug aus Review-D8-Commit):
    _entscheidung_anwenden muss die Sklaven-Meldung senden, handle_frei_aufgabe
    darf keine Variablen des Entscheidungs-Flows referenzieren."""
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bot", "handlers", "privileg.py")
    with open(pfad, encoding="utf-8") as f:
        baum = ast.parse(f.read())
    funktionen = {n.name: n for n in ast.walk(baum)
                  if isinstance(n, ast.AsyncFunctionDef)}
    namen_frei = {x.id for x in ast.walk(funktionen["handle_frei_aufgabe"])
                  if isinstance(x, ast.Name)}
    namen_entsch = {x.id for x in ast.walk(funktionen["_entscheidung_anwenden"])
                    if isinstance(x, ast.Name)}
    assert "meldung_sklave" not in namen_frei, "Entscheidungs-Block steckt wieder in handle_frei_aufgabe!"
    assert "meldung_sklave" in namen_entsch, "_entscheidung_anwenden sendet keine Sklaven-Meldung mehr!"
    print("✅ privileg.py: Entscheidungs-Meldung sitzt in _entscheidung_anwenden")


if __name__ == "__main__":
    test_fehlende_datei_ist_stiller_noop()
    test_versand_und_mtime_reload()
    test_unbekannter_tag_und_kaputte_datei()
    test_chance_gate()
    test_sende_fehler_bleibt_still()
    test_privileg_entscheidungs_block_richtig_verortet()
    print("\n🎉 Alle Sticker-Tests grün.")
