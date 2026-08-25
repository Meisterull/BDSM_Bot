"""
Telegram-Mini-App-Server: Cockpit + Sprachnachrichten-Studio (LAN-only, HTTPS).

Stdlib-only (Muster logserver.py): ThreadingHTTPServer in einem Daemon-Thread,
TLS über ssl.SSLContext (Zertifikat der lokalen Heimnetz-CA, s. .env.example).
Async-Services (Qdrant, TTS, Telegram-Versand) laufen über
run_coroutine_threadsafe auf dem Bot-Event-Loop.

Auth: Telegrams signierte initData (HMAC-SHA256 mit dem Bot-Token nach
Mini-App-Spez). Die User-ID daraus wird über paare.resolve() zu (Paar, Rolle)
aufgelöst – Fremde bekommen 403, egal ob sie die URL kennen. Fail-closed:
ohne MINIAPP_PORT oder ohne lesbares Zertifikat startet der Server nicht
(Telegram öffnet eh nur https-URLs).

Endpunkte (alle außer / verlangen den Header X-Init-Data):
  GET  /                    -> bot/webapp/index.html
  GET  /api/uebersicht      -> Cockpit-Daten (rollenbewusst)
  POST /api/vorschau        -> {text} -> OGG-Audio (nur Dom-Seite)
  POST /api/senden          -> {text} -> Text+Voice an den Sklaven (nur Dom-Seite)
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from bot import config

logger = logging.getLogger(__name__)

_LOOP: asyncio.AbstractEventLoop | None = None
_BOT = None
_INDEX_PFAD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "webapp", "index.html")
# Kleine Anfragekörper reichen völlig (Text einer Sprachnachricht).
_MAX_BODY = 64 * 1024


def pruefe_init_data(init_data: str) -> dict | None:
    """Validiert Telegrams initData (Signatur + Frische) und gibt das
    user-Objekt zurück, sonst None. Spez: Datencheck-String = sortierte
    key=value-Zeilen ohne hash, Secret = HMAC('WebAppData', Bot-Token)."""
    if not init_data or len(init_data) > 8192:
        return None
    felder = parse_qsl(init_data, keep_blank_values=True)
    hash_wert = dict(felder).get("hash", "")
    if not hash_wert:
        return None
    daten = "\n".join(f"{k}={v}" for k, v in sorted(felder) if k != "hash")
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    erwartet = hmac.new(secret, daten.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(erwartet, hash_wert):
        return None
    d = dict(felder)
    try:
        # 24h-Frische: initData wird beim App-Öffnen frisch ausgestellt; ein
        # abgegriffener alter String soll nicht ewig als Ausweis taugen.
        if time.time() - int(d.get("auth_date", "0")) > 86400:
            return None
        return json.loads(d.get("user", "{}")) or None
    except (ValueError, TypeError):
        return None


def _await(coro, timeout: float = 60.0):
    """Coroutine auf dem Bot-Loop ausführen (Aufruf aus dem Server-Thread)."""
    fut = asyncio.run_coroutine_threadsafe(coro, _LOOP)
    return fut.result(timeout)


async def _uebersicht_daten(paar_id: str, rolle: str) -> dict:
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        profil = await qdrant.get_user_profile("sklave") or {}
        score = await qdrant.get_vertrauens_score("sklave")
        tasks = await qdrant.get_tasks_by_status(
            ["offen", "erledigt", "nicht_erledigt"], sort_by_datum=True)
        erledigt_gesamt = await qdrant.get_completed_task_count("sklave")
        stimmung = await qdrant.get_latest_stimmung("sklave", max_stunden=48)
    offene = [
        {"aufgabe": t.get("aufgabe", ""), "erteilt_am": (t.get("erteilt_am") or "")[:10],
         "kategorie": t.get("kategorie", "")}
        for t in tasks if t.get("status") == "offen"
    ][:10]
    letzte = [
        {"aufgabe": t.get("aufgabe", ""), "status": t.get("status", ""),
         "erteilt_am": (t.get("erteilt_am") or "")[:10]}
        for t in tasks if t.get("status") in ("erledigt", "nicht_erledigt")
    ][:8]
    # Abzeichen bewusst nur als Anzahl – die geheimen Ziele bleiben geheim.
    return {
        "rolle": rolle,
        "punkte": profil.get("punkte", 0),
        "streak": profil.get("streak", 0),
        "streak_max": profil.get("streak_max", 0),
        "vertrauen": {"score": score.get("score", 0), "stufe": score.get("stufe", ""),
                      "quote": score.get("quote", 0)},
        "erledigt_gesamt": erledigt_gesamt,
        "abzeichen_anzahl": len(profil.get("abzeichen", [])),
        "offene_tasks": offene,
        "letzte_tasks": letzte,
        "stimmung": (stimmung or {}).get("zusammenfassung", ""),
        "grok_tts": bool(config.GROK_TTS),
    }


async def _vorschau_ogg(text: str) -> bytes | None:
    from bot.services import tts
    return await tts.synthesize(text, rolle=tts.ROLLE_HERRIN)


async def _sende_an_sklaven(paar_id: str, text: str) -> None:
    from bot.services import paare, telegram_helper, tts
    with paare.kontext(paar_id):
        await telegram_helper.send_sklave(_BOT, tts.entferne_sprech_tags(text),
                                          voice_text=text)


class _Handler(BaseHTTPRequestHandler):
    # --- Antwort-Helfer -------------------------------------------------------
    def _antwort(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, daten: dict) -> None:
        self._antwort(code, json.dumps(daten, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8")

    def _auth(self) -> tuple | None:
        """X-Init-Data prüfen -> (paar, rolle) oder None (Antwort schon gesendet)."""
        from bot.services import paare
        user = pruefe_init_data(self.headers.get("X-Init-Data", ""))
        if not user or not user.get("id"):
            self._json(401, {"fehler": "ungueltige initData"})
            return None
        aufgeloest = paare.resolve(str(user["id"]))
        if aufgeloest is None:
            self._json(403, {"fehler": "nicht autorisiert"})
            return None
        return aufgeloest

    def _body_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if not 0 < n <= _MAX_BODY:
            self._json(400, {"fehler": "Body fehlt/zu groß"})
            return None
        try:
            return json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"fehler": "kein JSON"})
            return None

    # --- Routen ---------------------------------------------------------------
    def do_GET(self):
        pfad = urlparse(self.path).path
        if pfad == "/":
            try:
                with open(_INDEX_PFAD, "rb") as f:
                    self._antwort(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._antwort(500, b"index.html fehlt", "text/plain")
            return
        if pfad == "/telegram-web-app.js":
            # Selbst gehostet: keine Abhängigkeit von telegram.org im LAN-Betrieb.
            try:
                with open(os.path.join(os.path.dirname(_INDEX_PFAD), "telegram-web-app.js"), "rb") as f:
                    self._antwort(200, f.read(), "application/javascript; charset=utf-8")
            except OSError:
                self._antwort(404, b"script fehlt", "text/plain")
            return
        if pfad == "/favicon.ico":
            self._antwort(204, b"", "image/x-icon")
            return
        if pfad == "/api/uebersicht":
            auth = self._auth()
            if not auth:
                return
            paar, rolle = auth
            try:
                daten = _await(_uebersicht_daten(paar.paar_id, rolle))
            except Exception:
                logger.exception("Mini-App: Übersicht fehlgeschlagen")
                self._json(500, {"fehler": "Daten nicht ladbar"})
                return
            self._json(200, daten)
            return
        self._antwort(404, b"nicht gefunden", "text/plain")

    def do_POST(self):
        from bot.services import paare
        pfad = urlparse(self.path).path
        if pfad == "/api/log":
            # Diagnose-Beacon der Seite (Inbetriebnahme): bewusst OHNE Auth –
            # loggt nur, führt nichts aus; Body hart gedeckelt.
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 2048)
                logger.info("Mini-App-Diagnose %s: %s", self.client_address[0],
                            self.rfile.read(n).decode("utf-8", "replace"))
            except Exception:
                pass
            self._json(200, {"ok": True})
            return
        if pfad not in ("/api/vorschau", "/api/senden"):
            self._antwort(404, b"nicht gefunden", "text/plain")
            return
        auth = self._auth()
        if not auth:
            return
        paar, rolle = auth
        if rolle != paare.ROLLE_DOM:
            self._json(403, {"fehler": "nur für die Dom-Seite"})
            return
        body = self._body_json()
        if body is None:
            return
        text = (body.get("text") or "").strip()
        if not text or len(text) > config.TTS_MAX_ZEICHEN * 2:
            self._json(400, {"fehler": "Text fehlt oder zu lang"})
            return
        try:
            if pfad == "/api/vorschau":
                ogg = _await(_vorschau_ogg(text), timeout=45)
                if not ogg:
                    self._json(502, {"fehler": "TTS nicht verfügbar"})
                    return
                self._antwort(200, ogg, "audio/ogg")
            else:
                _await(_sende_an_sklaven(paar.paar_id, text), timeout=45)
                self._json(200, {"ok": True})
        except Exception:
            logger.exception("Mini-App: %s fehlgeschlagen", pfad)
            self._json(500, {"fehler": "Aktion fehlgeschlagen"})

    def log_message(self, fmt, *args):
        # Bewusst LAUT (anders als logserver): das Ding wird gerade in Betrieb
        # genommen, und "kommt das Handy überhaupt an?" ist die Kernfrage.
        # UA + initData-Länge unterscheiden Telegram-WebView von Browser-Test.
        ua = (self.headers.get("User-Agent") or "?")[:60]
        init_len = len(self.headers.get("X-Init-Data") or "")
        plattform = self.headers.get("X-Tg-Platform") or "-"
        logger.info("Mini-App-Zugriff %s: %s [tg=%s, initData=%d Z., UA=%s]",
                    self.client_address[0], fmt % args, plattform, init_len, ua)


class _TLSServer(ThreadingHTTPServer):
    def get_request(self):
        # TLS-Handshake läuft im accept(); Fehler (z.B. Client vertraut der CA
        # nicht → "unknown ca"-Alert) wären sonst als OSError STILL verschluckt.
        try:
            return super().get_request()
        except ssl.SSLError as e:
            logger.warning("Mini-App: TLS-Handshake abgelehnt: %s", e)
            raise OSError(str(e))


def start(loop: asyncio.AbstractEventLoop, bot) -> None:
    """Startet den Mini-App-Server im Daemon-Thread (no-op wenn MINIAPP_PORT=0)."""
    global _LOOP, _BOT
    if not config.MINIAPP_PORT:
        return
    if not (os.path.isfile(config.MINIAPP_SSL_CERT) and os.path.isfile(config.MINIAPP_SSL_KEY)):
        logger.error("Mini-App NICHT gestartet: Zertifikat/Key fehlen (%s / %s).",
                     config.MINIAPP_SSL_CERT, config.MINIAPP_SSL_KEY)
        return
    _LOOP, _BOT = loop, bot
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(config.MINIAPP_SSL_CERT, config.MINIAPP_SSL_KEY)
        srv = _TLSServer(("0.0.0.0", config.MINIAPP_PORT), _Handler)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True, name="miniapp").start()
        logger.info("Mini-App-Server läuft auf Port %d (HTTPS, initData-Auth).",
                    config.MINIAPP_PORT)
    except Exception:
        logger.exception("Mini-App-Server konnte nicht gestartet werden")
