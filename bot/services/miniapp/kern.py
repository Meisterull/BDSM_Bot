"""
initData-Prüfung, Event-Loop-Brücke, Konstanten.

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl
from bot import config

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)

_LOOP: asyncio.AbstractEventLoop | None = None

_BOT = None

# Seit dem Paket-Umbau liegt diese Datei eine Ebene tiefer (bot/services/miniapp/) –
# darum drei dirname() bis bot/.
_INDEX_PFAD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
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
