"""
Tests zum Mitschreiben der Modell-Eingabe (17.11.2026):

  - grok.chat schreibt je Aufruf LLM-EINGABE (Überblick) und LLM-EINGABE-VOLL
    (System-Prompt + alle Nachrichten) auf DEBUG; eine Kennung verbindet sie
    mit LLM-Call und LLM-AUSGABE – auch bei gleichzeitigen Aufrufen
  - kein Zugangsschlüssel im Log; abschaltbar über LOG_LLM_EINGABE
  - Log-Server: VOLL-Zeilen nie in der Übersicht, die Eingabe-Zeilen und der
    Abruf `?eingabe=<Kennung>` nur für Nutzer aus LOG_EINGABE_USERS (und den Token)

    python3 tests/test_llm_eingabe_log.py
"""
import asyncio
import base64
import logging
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402
from bot.services import grok, logserver  # noqa: E402


class _Sammler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.texte = []

    def emit(self, record):
        self.texte.append(record.getMessage())


def _mit_sammler():
    sammler = _Sammler()
    log = logging.getLogger("bot.services.grok")
    alt_stufe = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(sammler)

    def weg():
        log.removeHandler(sammler)
        log.setLevel(alt_stufe)
    return sammler, weg


def _zeilen(sammler, anfang):
    return [z for z in sammler.texte if z.startswith(anfang)]


# --------------------------------------------------------------------------
# grok: Eingabe + Kennung
# --------------------------------------------------------------------------

async def test_eingabe_steht_mit_kennung_im_log():
    sammler, weg = _mit_sammler()
    orig = (grok._post_chat, config.LOG_LLM_EINGABE, dict(grok._HEADERS))

    async def fake_post(url, headers, payload, timeout=None):
        grok._log_ausgabe("Antwort\nzweite Zeile")
        return "Antwort"

    grok._post_chat = fake_post
    config.LOG_LLM_EINGABE = True
    grok._HEADERS["Authorization"] = "Bearer STRENG-GEHEIM"
    try:
        verlauf = [{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "Na?"},
                   {"role": "user", "content": "Zeile A\nZeile B"}]
        assert await grok.chat("SYSTEM eins\nzwei", verlauf) == "Antwort"
        kurz, voll, aus = (_zeilen(sammler, a) for a in ("LLM-EINGABE #", "LLM-EINGABE-VOLL #", "LLM-AUSGABE #"))
        assert len(kurz) == len(voll) == len(aus) == 1, sammler.texte
        kennung = kurz[0].split()[1]
        assert re.fullmatch(r"#[0-9a-f]{6}", kennung), kennung
        assert voll[0].split()[1] == kennung == aus[0].split()[1]
        assert "System 16 Zeichen, 3 Nachricht(en)" in kurz[0] and kurz[0].endswith("zuletzt (user): Zeile A ⏎ Zeile B")
        assert voll[0].endswith("[system] ⏎ SYSTEM eins ⏎ zwei ⏎ [user] ⏎ Hallo ⏎ [assistant] ⏎ Na? ⏎ "
                                "[user] ⏎ Zeile A ⏎ Zeile B"), voll[0]
        assert "\n" not in voll[0] and aus[0].endswith("Antwort ⏎ zweite Zeile")
        assert not any("STRENG-GEHEIM" in z for z in sammler.texte)
        assert grok._nr() == ""                       # außerhalb des Aufrufs keine Kennung
        # nächster Aufruf: eigene Kennung
        await grok.simple("Noch eine Frage", system="S")
        assert _zeilen(sammler, "LLM-EINGABE #")[-1].split()[1] != kennung
        # abgeschaltet: keine Eingabe-Zeilen, die Antwort trägt weiter ihre Kennung
        sammler.texte.clear()
        config.LOG_LLM_EINGABE = False
        await grok.simple("Ohne Mitschrift", system="S")
        assert not _zeilen(sammler, "LLM-EINGABE") and len(_zeilen(sammler, "LLM-AUSGABE #")) == 1
    finally:
        grok._post_chat, config.LOG_LLM_EINGABE = orig[0], orig[1]
        grok._HEADERS.clear()
        grok._HEADERS.update(orig[2])
        weg()


async def test_gleichzeitige_aufrufe_behalten_ihre_kennung():
    sammler, weg = _mit_sammler()
    orig = (grok._post_chat, config.LOG_LLM_EINGABE)

    async def fake_post(url, headers, payload, timeout=None):
        inhalt = payload["messages"][-1]["content"]
        await asyncio.sleep(0.02 if inhalt == "erste" else 0)      # die erste Antwort kommt zuletzt
        grok._log_ausgabe(f"Antwort auf {inhalt}")
        return "ok"

    grok._post_chat = fake_post
    config.LOG_LLM_EINGABE = True
    try:
        await asyncio.gather(grok.simple("erste", system="S"), grok.simple("zweite", system="S"))
        je_kennung = {}
        for z in sammler.texte:
            if z.startswith(("LLM-EINGABE #", "LLM-AUSGABE #")):
                je_kennung.setdefault(z.split()[1], []).append(z)
        assert len(je_kennung) == 2, je_kennung
        for zeilen in je_kennung.values():
            frage = "erste" if any(z.endswith("zuletzt (user): erste") for z in zeilen) else "zweite"
            assert any(z.endswith(f"Antwort auf {frage}") for z in zeilen), zeilen
    finally:
        grok._post_chat, config.LOG_LLM_EINGABE = orig
        weg()


# --------------------------------------------------------------------------
# Log-Server: Sichtbarkeit und Abruf
# --------------------------------------------------------------------------

LOG = """2026-01-01 10:00:00,000 - bot.app.gates - DEBUG - EMPFANGEN-INHALT ← DOMINA | Hallo
2026-01-01 10:00:00,100 - bot.services.grok - DEBUG - LLM-EINGABE #aaa111 | modell-x | System 22 Zeichen, 1 Nachricht(en) | zuletzt (user): Hallo
2026-01-01 10:00:00,101 - bot.services.grok - DEBUG - LLM-EINGABE-VOLL #aaa111 | [system] ⏎ Regel eins ⏎ Regel zwei ⏎ [user] ⏎ Hallo
2026-01-01 10:00:01,000 - bot.services.grok - INFO - LLM-Call #aaa111 modell-x: 0.9s | Tokens in=10 out=2
2026-01-01 10:00:01,001 - bot.services.grok - DEBUG - LLM-AUSGABE #aaa111 | Na du ⏎ zweite Zeile
2026-01-01 10:00:01,050 - bot.app.routing - INFO - GESENDET → DOMINA | (5 Zeichen)
2026-01-01 10:05:00,100 - bot.services.grok - DEBUG - LLM-EINGABE #bbb222 | modell-x | System 5 Zeichen, 1 Nachricht(en) | zuletzt (user): Später
2026-01-01 10:05:00,101 - bot.services.grok - DEBUG - LLM-EINGABE-VOLL #bbb222 | [system] ⏎ Kurz ⏎ [user] ⏎ Später
2026-01-01 10:05:01,001 - bot.services.grok - DEBUG - LLM-AUSGABE #bbb222 | Gern
2026-01-01 10:05:01,050 - bot.app.routing - INFO - GESENDET → SKLAVE | (4 Zeichen)
"""
ALT = ("2025-12-31 09:00:00,101 - bot.services.grok - DEBUG - LLM-EINGABE-VOLL #ccc333 | [system] ⏎ Alt ⏎ [user] ⏎ Früher\n")


def _log_datei():
    pfad = os.path.join(tempfile.mkdtemp(), "bot.log")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(LOG)
    with open(pfad + ".1", "w", encoding="utf-8") as f:
        f.write(ALT)
    return pfad


def test_uebersicht_und_abruf():
    orig = (config.LOG_FILE, config.LOG_EINGABE_USERS, config.LOG_TOKEN)
    config.LOG_FILE = _log_datei()
    try:
        ohne = logserver._tail(config.LOG_FILE, 500)
        assert "LLM-EINGABE" not in ohne and "EMPFANGEN-INHALT ← DOMINA | Hallo" in ohne
        assert "LLM-AUSGABE #aaa111" in ohne and ohne.count("\n") == 6, ohne
        mit = logserver._tail(config.LOG_FILE, 500, mit_eingabe=True)
        assert "LLM-EINGABE #aaa111" in mit and "LLM-EINGABE #bbb222" in mit and "LLM-EINGABE-VOLL" not in mit
        # n zählt sichtbare Zeilen
        letzte = logserver._tail(config.LOG_FILE, 2).splitlines()
        assert [z.split(" - ")[-1][:11] for z in letzte] == ["LLM-AUSGABE", "GESENDET → "], letzte

        ansicht = logserver._eingabe_ansicht("aaa111")
        assert ansicht.startswith("=== LLM-EINGABE #aaa111 · 2026-01-01 10:00:00 ===\n[system]\nRegel eins\nRegel zwei\n[user]\nHallo\n")
        assert "=== ANTWORT ===" in ansicht and "LLM-Call #aaa111 modell-x" in ansicht
        assert "LLM-AUSGABE #aaa111 | Na du\nzweite Zeile" in ansicht and "bbb222" not in ansicht
        assert "#bbb222" in logserver._eingabe_ansicht("letzte").splitlines()[0]
        # rotierte Datei wird mit durchsucht; unbekannte Kennung → None
        alt = logserver._eingabe_ansicht("ccc333")
        assert "[user]\nFrüher" in alt and "(keine Antwort im Log)" in alt
        assert logserver._eingabe_ansicht("fff999") is None

        # Freigabe: nur benannte Nutzer und der Token
        config.LOG_EINGABE_USERS = "anna, carla"
        assert logserver._darf_eingabe("anna") and logserver._darf_eingabe("carla")
        assert not logserver._darf_eingabe("bert") and not logserver._darf_eingabe(None) and not logserver._darf_eingabe("")
        assert logserver._darf_eingabe(logserver._TOKEN_NUTZER)
        config.LOG_EINGABE_USERS = ""
        assert not logserver._darf_eingabe("anna") and logserver._darf_eingabe(logserver._TOKEN_NUTZER)
    finally:
        config.LOG_FILE, config.LOG_EINGABE_USERS, config.LOG_TOKEN = orig


def test_http_zugriff_je_nutzer():
    orig = (config.LOG_FILE, config.LOG_EINGABE_USERS, config.LOG_TOKEN, config.LOG_USERS)
    config.LOG_FILE = _log_datei()
    config.LOG_USERS = "anna:pw1,bert:pw2"
    config.LOG_EINGABE_USERS = "anna"
    config.LOG_TOKEN = "tok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), logserver._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    basis = f"http://127.0.0.1:{srv.server_address[1]}"

    def hol(pfad, nutzer=None):
        req = urllib.request.Request(basis + pfad)
        if nutzer:
            req.add_header("Authorization", "Basic " + base64.b64encode(nutzer.encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    try:
        assert hol("/")[0] == 401 and hol("/?eingabe=aaa111")[0] == 401
        assert hol("/", "anna:falsch")[0] == 401
        # bert darf das Log lesen, aber keine Eingaben
        status, text = hol("/", "bert:pw2")
        assert status == 200 and "LLM-EINGABE" not in text and "GESENDET → SKLAVE" in text
        status, text = hol("/?eingabe=aaa111", "bert:pw2")
        assert status == 403 and "Regel eins" not in text
        assert hol("/?eingabe=letzte", "bert:pw2")[0] == 403
        # anna sieht den Überblick und den Abruf
        status, text = hol("/", "anna:pw1")
        assert status == 200 and "LLM-EINGABE #aaa111" in text and "LLM-EINGABE-VOLL" not in text
        status, text = hol("/?eingabe=AAA111", "anna:pw1")
        assert status == 200 and "[system]\nRegel eins\nRegel zwei" in text and "Na du\nzweite Zeile" in text
        assert hol("/?eingabe=../../etc", "anna:pw1")[0] == 400
        status, text = hol("/?eingabe=fff999", "anna:pw1")
        assert status == 200 and "kein Aufruf" in text
        # der Betreiber-Token sieht alles
        status, text = hol("/?token=tok&eingabe=letzte")
        assert status == 200 and "#bbb222" in text
        assert "LLM-EINGABE #aaa111" in hol("/?token=tok")[1]
    finally:
        srv.shutdown()
        srv.server_close()
        config.LOG_FILE, config.LOG_EINGABE_USERS, config.LOG_TOKEN, config.LOG_USERS = orig


if __name__ == "__main__":
    asyncio.run(test_eingabe_steht_mit_kennung_im_log())
    asyncio.run(test_gleichzeitige_aufrufe_behalten_ihre_kennung())
    test_uebersicht_und_abruf()
    test_http_zugriff_je_nutzer()
    print("✅ Alle Tests zur Modell-Eingabe im Log bestanden")
