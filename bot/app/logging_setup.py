"""
Logging-Setup (0600-Logdatei, Formate).

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from bot import config



class _Private0600FileHandler(RotatingFileHandler):
    """Erzeugt die Logdatei IMMER mit 0600 – auch nach doRollover() (D9/S3).
    Der einmalige chmod beim Start deckte nur die erste Datei ab; jede Rotation
    legte die neue bot.log mit umask-Default (0644, world-readable) an."""

    def _open(self):
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if "w" in self.mode else os.O_APPEND)
        fd = os.open(self.baseFilename, flags, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


def _setup_logging() -> None:
    """Console (INFO) + rotierende Logdatei (DEBUG) – komplettes Logging zur Analyse."""
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    handlers = [console]
    try:
        os.makedirs(os.path.dirname(config.LOG_FILE) or ".", exist_ok=True)
        fileh = _Private0600FileHandler(
            config.LOG_FILE, maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        fileh.setLevel(logging.DEBUG)
        fileh.setFormatter(fmt)
        handlers.append(fileh)
        # Logdatei enthält intime Inhalte (DEBUG) → nur für den Bot-User lesbar.
        try:
            os.chmod(config.LOG_FILE, 0o600)
        except OSError:
            pass
    except Exception as e:
        console.handle(logging.makeLogRecord(
            {"msg": f"Logdatei nicht initialisierbar: {e}", "levelno": logging.ERROR, "levelname": "ERROR", "name": "logging"}))
    root.handlers = handlers
    # Laute Bibliotheken dämpfen, damit GESENDET/EMPFANGEN/Fehler/LLM-Calls
    # nicht im httpcore-DEBUG-Spam untergehen (sonst rotiert die Logdatei in Minuten).
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Eigene Module bleiben gesprächig (Aufrufe, LLM-Calls zur Analyse).
    logging.getLogger("bot").setLevel(logging.DEBUG)
    logging.getLogger("bot.services.grok").setLevel(logging.DEBUG)


_setup_logging()
