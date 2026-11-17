"""
Mini-HTTP-Server zum Live-Lesen der Log-Datei (stdlib, keine Extra-Deps).

ACHTUNG: Das Log enthält intime Chat-Inhalte. Der Server startet fail-closed –
ohne LOG_USERS (Basic-Auth, Format "user:pass,...") oder LOG_TOKEN wird er gar
nicht gestartet. docker-compose mappt den Port ins LAN, daher ist Auth Pflicht.

Endpunkte:
  GET /                  -> letzte 500 Zeilen (Basic-Auth erforderlich)
  GET /?n=2000           -> letzte 2000 Zeilen
  GET /?token=XYZ        -> alternativ via LOG_TOKEN (curl-Skripte)
  GET /?eingabe=a1b2c3   -> EIN Modell-Aufruf zum Nachlesen: die volle Eingabe
                            (System-Prompt + alle Nachrichten, mit Zeilenumbrüchen)
                            und die Antwort dazu. Die Kennung steht in den Zeilen
                            LLM-EINGABE / LLM-Call / LLM-AUSGABE als #a1b2c3.
  GET /?eingabe=letzte   -> dasselbe für den jüngsten Aufruf

Die Eingaben ans Modell (services/grok._log_eingabe) enthalten die vollständigen
Profile beider Seiten. Sie sind deshalb nur für Nutzer aus LOG_EINGABE_USERS
(und für LOG_TOKEN) sichtbar; allen anderen fehlen die LLM-EINGABE-Zeilen in der
Übersicht, und der Abruf wird abgelehnt. Die VOLL-Zeile (ein ganzer Prompt in
einer Zeile) steht nie in der Übersicht – dafür ist der Abruf da.
"""
import base64
import collections
import hmac
import logging
import os
import re
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


# Marker der Eingabe-Zeilen im Logformat "<zeit> - <modul> - <stufe> - <text>".
_KURZ = " - LLM-EINGABE #"
_VOLL = " - LLM-EINGABE-VOLL #"
# Interner "Nutzername" für die Anmeldung per LOG_TOKEN (kein gültiger Basic-Name).
_TOKEN_NUTZER = "\x00token"
_KENNUNG_RE = re.compile(r"[0-9a-f]{4,16}")


def _eingabe_nutzer() -> set:
    return {u.strip() for u in (config.LOG_EINGABE_USERS or "").split(",") if u.strip()}


def _darf_eingabe(nutzer) -> bool:
    """Darf dieser Zugang sehen, was an das Modell ging? Nur ausdrücklich
    benannte Nutzer (LOG_EINGABE_USERS) und der Betreiber-Token."""
    if nutzer == _TOKEN_NUTZER:
        return True
    return bool(nutzer) and nutzer in _eingabe_nutzer()


def _tail(path: str, n: int, mit_eingabe: bool = False) -> str:
    """Die letzten n SICHTBAREN Zeilen: VOLL-Zeilen nie, die Kurz-Zeile zur
    Eingabe nur mit `mit_eingabe`."""
    if not os.path.exists(path):
        return "(noch kein Log vorhanden)"

    def sichtbar(zeile: str) -> bool:
        if _VOLL in zeile:
            return False
        return mit_eingabe or _KURZ not in zeile

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # deque statt readlines()[-n:]: hält nur n Zeilen im RAM, nicht die ganze Datei.
            return "".join(collections.deque((z for z in f if sichtbar(z)), maxlen=n))
    except Exception as e:
        return f"(Fehler beim Lesen: {e})"


def _log_dateien() -> list:
    """Aktuelle Logdatei zuerst, dann die rotierten (bot.log.1 …)."""
    return [config.LOG_FILE] + [f"{config.LOG_FILE}.{i}" for i in range(1, config.LOG_BACKUP_COUNT + 1)]


def _mit_umbruechen(text: str) -> str:
    return text.replace(" ⏎ ", "\n").replace("⏎", "\n")


def _eingabe_ansicht(kennung: str) -> str | None:
    """Ein Modell-Aufruf zum Nachlesen – `kennung` ist die #-Nummer aus dem Log
    oder "letzte". Gibt Eingabe (Zeilenumbrüche wiederhergestellt) und die
    Zeilen der Antwort zurück; None, wenn der Aufruf in keiner Logdatei steht."""
    for pfad in _log_dateien():
        if not os.path.exists(pfad):
            continue
        treffer, danach = None, []
        try:
            with open(pfad, "r", encoding="utf-8", errors="replace") as f:
                for zeile in f:
                    if _VOLL in zeile:
                        k = zeile.split(_VOLL, 1)[1].split(" ", 1)[0]
                        if kennung in ("letzte", k):
                            treffer, danach = (k, zeile), []
                    elif treffer and (f" - LLM-Call #{treffer[0]} " in zeile
                                      or f" - LLM-AUSGABE #{treffer[0]} " in zeile):
                        danach.append(zeile)
        except Exception as e:
            return f"(Fehler beim Lesen: {e})"
        if treffer:
            k, zeile = treffer
            _kopf, _, inhalt = zeile.partition(f"{_VOLL}{k} | ")
            teile = [f"=== LLM-EINGABE #{k} · {zeile[:19]} ===", _mit_umbruechen(inhalt.rstrip("\n")), "",
                     "=== ANTWORT ==="]
            teile += [_mit_umbruechen(z.rstrip("\n")) for z in danach] or ["(keine Antwort im Log)"]
            return "\n".join(teile) + "\n"
    return None


class _Handler(BaseHTTPRequestHandler):
    def _angemeldet(self):
        """Name des angemeldeten Nutzers, _TOKEN_NUTZER beim Query-Token, None
        ohne gültige Anmeldung."""
        users = _parse_users()
        # Query-Token weiterhin akzeptiert (Abwärtskompatibilität / curl-Skripte)
        q = parse_qs(urlparse(self.path).query)
        if config.LOG_TOKEN and hmac.compare_digest(q.get("token", [""])[0], config.LOG_TOKEN):
            return _TOKEN_NUTZER
        if not users and not config.LOG_TOKEN:
            return None  # fail-closed: ohne konfigurierte Auth nie autorisieren
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(hdr[6:]).decode("utf-8", "replace").partition(":")
            except Exception:
                return None
            stored = users.get(u)
            if stored is not None and hmac.compare_digest(stored, p):
                return u
        return None

    def _authorized(self) -> bool:
        return self._angemeldet() is not None

    def _antwort(self, status: int, text: str) -> None:
        body = text.encode("utf-8", "replace")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        nutzer = self._angemeldet()
        if nutzer is None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="bot-log"')
            self.end_headers()
            self.wfile.write(b"auth required")
            return
        q = parse_qs(urlparse(self.path).query)
        kennung = q.get("eingabe", [""])[0].strip().lower()
        if kennung:
            if not _darf_eingabe(nutzer):
                self._antwort(403, "Die Eingaben ans Modell sind für diesen Zugang nicht freigegeben "
                                   "(LOG_EINGABE_USERS).\n")
                return
            if kennung != "letzte" and not _KENNUNG_RE.fullmatch(kennung):
                self._antwort(400, "eingabe=<Kennung aus dem Log, z. B. a1b2c3> oder eingabe=letzte\n")
                return
            self._antwort(200, _eingabe_ansicht(kennung) or "(kein Aufruf mit dieser Kennung im Log)\n")
            return
        try:
            n = max(1, min(20000, int(q.get("n", ["500"])[0])))
        except ValueError:
            n = 500
        self._antwort(200, _tail(config.LOG_FILE, n, mit_eingabe=_darf_eingabe(nutzer)))

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
        logger.info("Log-Server läuft auf Port %d (Basic-Auth: %d Nutzer, Token: %s, Modell-Eingaben für: %s)",
                    config.LOG_PORT, n_users, "ja" if config.LOG_TOKEN else "nein",
                    ", ".join(sorted(_eingabe_nutzer() & set(_parse_users()))) or "niemand")
    except Exception:
        logger.exception("Log-Server konnte nicht gestartet werden")
