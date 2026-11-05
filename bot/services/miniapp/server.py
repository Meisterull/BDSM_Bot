"""
HTTP-Handler, TLS-Server, Start.

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""
import logging
import asyncio
import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from bot import config
from bot.services.miniapp import aktionen, kern, training, uebersicht, werkstatt
from bot.services.miniapp import vanilla as miniapp_vanilla

logger = logging.getLogger(__name__)



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
        user = kern.pruefe_init_data(self.headers.get("X-Init-Data", ""))
        if not user or not user.get("id"):
            self._json(401, {"fehler": "ungueltige initData"})
            return None
        aufgeloest = paare.resolve(str(user["id"]))
        if aufgeloest is None:
            self._json(403, {"fehler": "nicht autorisiert"})
            return None
        return aufgeloest

    @staticmethod
    def _ist_vanilla(paar) -> bool:
        from bot.services import modi, paare
        with paare.kontext(paar.paar_id):
            return modi.ist_vanilla()

    def _body_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if not 0 < n <= kern._MAX_BODY:
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
                with open(kern._INDEX_PFAD, "rb") as f:
                    self._antwort(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._antwort(500, b"index.html fehlt", "text/plain")
            return
        if pfad == "/telegram-web-app.js":
            # Selbst gehostet: keine Abhängigkeit von telegram.org im LAN-Betrieb.
            try:
                with open(os.path.join(os.path.dirname(kern._INDEX_PFAD), "telegram-web-app.js"), "rb") as f:
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
                if self._ist_vanilla(paar):
                    daten = kern._await(miniapp_vanilla._uebersicht_vanilla(paar.paar_id, rolle))
                else:
                    daten = kern._await(uebersicht._uebersicht_daten(paar.paar_id, rolle))
            except Exception:
                logger.exception("Mini-App: Übersicht fehlgeschlagen")
                self._json(500, {"fehler": "Daten nicht ladbar"})
                return
            self._json(200, daten)
            return
        if pfad == "/api/trainings":
            from bot.services import paare
            auth = self._auth()
            if not auth:
                return
            paar, rolle = auth
            if rolle != paare.ROLLE_DOM:
                self._json(403, {"fehler": "nur für die Dom-Seite"})
                return
            try:
                self._json(200, kern._await(training._trainings_liste(paar.paar_id)))
            except Exception:
                logger.exception("Mini-App: Trainings-Liste fehlgeschlagen")
                self._json(500, {"fehler": "Liste nicht ladbar"})
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
        # Pfad -> (erlaubte Rolle, braucht 'text'-Feld). Vorschau/Senden/Werkstatt
        # sind Dom-Sache, Wünsche kommen vom Sub.
        routen = {
            "/api/vorschau": (paare.ROLLE_DOM, True),
            "/api/senden": (paare.ROLLE_DOM, True),
            "/api/aufgabe": (paare.ROLLE_DOM, True),
            "/api/inspiration": (paare.ROLLE_DOM, False),
            "/api/wette": (paare.ROLLE_DOM, False),
            "/api/aufgabe_loeschen": (paare.ROLLE_DOM, False),
            "/api/serie": (paare.ROLLE_DOM, True),
            "/api/kette": (paare.ROLLE_DOM, False),
            "/api/training": (paare.ROLLE_DOM, False),
            "/api/psycho_uebung": (paare.ROLLE_DOM, False),
            "/api/psycho_antwort": (paare.ROLLE_DOM, False),
            "/api/anruf": (paare.ROLLE_DOM, False),
            "/api/wunsch": (paare.ROLLE_SUB, True),
            # None = beide Rollen (bearbeiten je nur EIGENE Daten, s. Coroutinen)
            "/api/profil_liste": (None, False),
            "/api/abwesenheit": (None, False),
        }
        # Vanilla (Etappe 4): /api/v/<aktion> für beide Seiten, nur im Vanilla-Modus
        if pfad.startswith("/api/v/"):
            name = pfad[len("/api/v/"):]
            if name not in miniapp_vanilla.AKTIONEN:
                self._antwort(404, b"nicht gefunden", "text/plain")
                return
            auth = self._auth()
            if not auth:
                return
            paar, rolle = auth
            if not self._ist_vanilla(paar):
                self._json(409, {"fehler": "nur im Vanilla-Modus"})
                return
            body = self._body_json()
            if body is None:
                return
            try:
                ergebnis = kern._await(miniapp_vanilla._aktion(paar.paar_id, rolle, name, body), timeout=180)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            except Exception:
                logger.exception("Mini-App: /api/v/%s fehlgeschlagen", name)
                self._json(500, {"fehler": "Aktion fehlgeschlagen"})
            return
        if pfad not in routen:
            self._antwort(404, b"nicht gefunden", "text/plain")
            return
        auth = self._auth()
        if not auth:
            return
        paar, rolle = auth
        noetige_rolle, braucht_text = routen[pfad]
        # Vanilla: die rollengebundenen BDSM-Routen (Werkstatt, Studio, Training,
        # Sub-Wunsch an die Domina) gibt es nicht – nur die Paar-Routen laufen.
        if pfad not in ("/api/profil_liste", "/api/abwesenheit") and self._ist_vanilla(paar):
            self._json(409, {"fehler": "nicht im Vanilla-Modus"})
            return
        if noetige_rolle and rolle != noetige_rolle:
            self._json(403, {"fehler": f"nur für die Rolle '{noetige_rolle}'"})
            return
        if pfad == "/api/anruf":
            # Roh-Audio (WAV) statt JSON – eigener, größerer Body-Deckel.
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if not 44 < n <= 8 * 1024 * 1024:
                self._json(400, {"fehler": "Audio fehlt oder ist zu groß (max 8 MB)"})
                return
            try:
                ergebnis = kern._await(aktionen._anruf(paar.paar_id, self.rfile.read(n)), timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            except Exception:
                logger.exception("Mini-App: /api/anruf fehlgeschlagen")
                self._json(500, {"fehler": "Anruf fehlgeschlagen"})
            return
        body = self._body_json()
        if body is None:
            return
        text = (body.get("text") or "").strip()
        if braucht_text and not (0 < len(text) <= config.TTS_MAX_ZEICHEN * 2):
            self._json(400, {"fehler": "Text fehlt oder zu lang"})
            return
        try:
            if pfad == "/api/vorschau":
                ogg = kern._await(werkstatt._vorschau_ogg(text), timeout=45)
                if not ogg:
                    self._json(502, {"fehler": "TTS nicht verfügbar"})
                    return
                self._antwort(200, ogg, "audio/ogg")
            elif pfad == "/api/senden":
                kern._await(werkstatt._sende_an_sklaven(paar.paar_id, text), timeout=45)
                self._json(200, {"ok": True})
            elif pfad == "/api/aufgabe":
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = kern._await(werkstatt._werkstatt_aufgabe(paar.paar_id, text, kategorie),
                                  timeout=90)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/wette":
                thema = (body.get("thema") or "").strip()[:60]
                ergebnis = kern._await(werkstatt._werkstatt_wette(paar.paar_id, thema), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/inspiration":
                # reasoning-Generierung + Limits-Filter: darf dauern
                self._json(200, kern._await(werkstatt._werkstatt_inspiration(paar.paar_id), timeout=180))
            elif pfad == "/api/aufgabe_loeschen":
                task_id = (body.get("task_id") or "").strip()
                if not task_id:
                    self._json(400, {"fehler": "task_id fehlt"})
                    return
                ergebnis = kern._await(werkstatt._werkstatt_loeschen(paar.paar_id, task_id), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/serie":
                try:
                    tage = int(body.get("tage", 0))
                except (TypeError, ValueError):
                    tage = 0
                if tage not in (2, 3, 7, 14):
                    self._json(400, {"fehler": "tage muss 2, 3, 7 oder 14 sein"})
                    return
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = kern._await(werkstatt._werkstatt_serie(paar.paar_id, text, kategorie, tage),
                                  timeout=180)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/kette":
                glieder = body.get("glieder")
                if (not isinstance(glieder, list) or not 2 <= len(glieder) <= 8
                        or not all(isinstance(g, str) and 0 < len(g.strip()) <= 500
                                   for g in glieder)):
                    self._json(400, {"fehler": "glieder: 2–8 Texte (je ≤500 Zeichen)"})
                    return
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = kern._await(werkstatt._werkstatt_kette(
                    paar.paar_id, [g.strip() for g in glieder], kategorie), timeout=120)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/training":
                kategorie = (body.get("kategorie") or "").strip()[:60]
                if not kategorie:
                    self._json(400, {"fehler": "kategorie fehlt"})
                    return
                ergebnis = kern._await(training._training_erstellen(paar.paar_id, kategorie), timeout=180)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/psycho_uebung":
                ergebnis = kern._await(training._psycho_uebung(
                    paar.paar_id, str(body.get("typ", "")).strip()), timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/psycho_antwort":
                uebung = str(body.get("uebung", "")).strip()[:4000]
                antwort = str(body.get("antwort", "")).strip()[:4000]
                if not (uebung and antwort):
                    self._json(400, {"fehler": "uebung/antwort fehlt"})
                    return
                ergebnis = kern._await(training._psycho_antwort(
                    paar.paar_id, str(body.get("typ", "")).strip(), uebung, antwort),
                    timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/profil_liste":
                werte = body.get("werte")
                if not isinstance(werte, list):
                    self._json(400, {"fehler": "werte-Liste fehlt"})
                    return
                ergebnis = kern._await(aktionen._profil_liste_setzen(
                    paar.paar_id, rolle, str(body.get("feld", "")), werte), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/abwesenheit":
                ergebnis = kern._await(aktionen._abwesenheit_setzen(paar.paar_id, rolle, body), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            else:  # /api/wunsch
                kern._await(aktionen._wunsch_einreichen(paar.paar_id, text), timeout=45)
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
    if not config.MINIAPP_PORT:
        return
    if not (os.path.isfile(config.MINIAPP_SSL_CERT) and os.path.isfile(config.MINIAPP_SSL_KEY)):
        logger.error("Mini-App NICHT gestartet: Zertifikat/Key fehlen (%s / %s).",
                     config.MINIAPP_SSL_CERT, config.MINIAPP_SSL_KEY)
        return
    kern._LOOP, kern._BOT = loop, bot
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
