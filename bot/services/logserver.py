"""
Mini-HTTP-Server zum Live-Lesen der Log-Datei (stdlib, keine Extra-Deps).

ACHTUNG: Das Log enthält intime Chat-Inhalte. Der Server startet fail-closed –
ohne LOG_USERS (Basic-Auth, Format "user:pass,...") oder LOG_TOKEN wird er gar
nicht gestartet. docker-compose mappt den Port ins LAN, daher ist Auth Pflicht.

Endpunkte:
  GET /            -> letzte 500 Zeilen (Basic-Auth erforderlich)
  GET /?n=2000     -> letzte 2000 Zeilen
  GET /?token=XYZ  -> alternativ via LOG_TOKEN (curl-Skripte)
"""
import base64
import collections
import hmac
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from bot import config

logger = logging.getLogger(__name__)


def _parse_users() -> dict:
    """LOG_USERS='user1:pass1,user2:pass2' -> {user: pass}. Leer = keine Basic-Auth."""
    users = {}
    for pair in (config.LOG_USERS or "").split(","):
        pair = pair.strip()
        if ":" in pair:
            u, p = pair.split(":", 1)
            if u.strip():
                users[u.strip()] = p
    return users


def _tail(path: str, n: int) -> str:
    if not os.path.exists(path):
        return "(noch kein Log vorhanden)"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # deque statt readlines()[-n:]: hält nur n Zeilen im RAM, nicht die ganze Datei.
            return "".join(collections.deque(f, maxlen=n))
    except Exception as e:
        return f"(Fehler beim Lesen: {e})"


class _Handler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        users = _parse_users()
        # Query-Token weiterhin akzeptiert (Abwärtskompatibilität / curl-Skripte)
        q = parse_qs(urlparse(self.path).query)
        if config.LOG_TOKEN and hmac.compare_digest(q.get("token", [""])[0], config.LOG_TOKEN):
            return True
        if not users and not config.LOG_TOKEN:
            return False  # fail-closed: ohne konfigurierte Auth nie autorisieren
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(hdr[6:]).decode("utf-8", "replace").partition(":")
            except Exception:
                return False
            stored = users.get(u)
            if stored is not None and hmac.compare_digest(stored, p):
                return True
        return False

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="bot-log"')
            self.end_headers()
            self.wfile.write(b"auth required")
            return
        q = parse_qs(urlparse(self.path).query)
        try:
            n = max(1, min(20000, int(q.get("n", ["500"])[0])))
        except ValueError:
            n = 500
        body = _tail(config.LOG_FILE, n).encode("utf-8", "replace")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Zugriffe nicht ins Bot-Log spammen


def start() -> None:
    """Startet den Log-Server in einem Daemon-Thread (no-op wenn LOG_PORT=0)."""
    if not config.LOG_PORT:
        return
    # Fail-closed: Das Log enthält intime Chat-Inhalte und der Port wird in
    # docker-compose ins LAN gemappt. Ohne Auth wird der Server NICHT gestartet.
    if not _parse_users() and not config.LOG_TOKEN:
        logger.error(
            "Log-Server NICHT gestartet: weder LOG_USERS noch LOG_TOKEN gesetzt. "
            "Auth konfigurieren oder LOG_PORT=0 setzen."
        )
        return
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", config.LOG_PORT), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True, name="logserver").start()
        n_users = len(_parse_users())
        logger.info("Log-Server läuft auf Port %d (Basic-Auth: %d Nutzer, Token: %s)",
                    config.LOG_PORT, n_users, "ja" if config.LOG_TOKEN else "nein")
    except Exception:
        logger.exception("Log-Server konnte nicht gestartet werden")
