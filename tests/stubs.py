"""
Gemeinsame Stub-Präambel für die lokalen Tests.

Die Tests laufen mit echten Deps (im Container) ODER lokal ohne
python-telegram-bot/qdrant-client/apscheduler/httpx. Fehlen die echten
Module, werden sie durch MagicMocks ersetzt – exakt das Muster, das die
Test-Dateien bisher einzeln kopiert hatten. Neue Tests importieren einfach:

    from tests import stubs  # noqa: F401  (muss VOR jedem bot-Import stehen)
    stubs.aktivieren()
"""
import os
import sys

_WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULE = [
    "telegram", "telegram.ext", "telegram.constants", "telegram.error",
    "qdrant_client", "qdrant_client.models", "qdrant_client.http",
    "qdrant_client.http.models", "qdrant_client.http.exceptions",
    "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.asyncio",
    "apscheduler.triggers", "apscheduler.triggers.cron",
    "apscheduler.triggers.interval", "httpx", "dotenv",
]


def aktivieren(domina_chat_id: str = "111", sklave_chat_id: str = "222") -> bool:
    """Setzt sys.path, Pflicht-Env und – falls nötig – die Modul-Stubs.
    Rückgabe: True, wenn gestubbt wurde (lokal), False bei echten Deps."""
    if _WURZEL not in sys.path:
        sys.path.insert(0, _WURZEL)
    os.environ.setdefault("DOMINA_CHAT_ID", domina_chat_id)
    os.environ.setdefault("SKLAVE_CHAT_ID", sklave_chat_id)
    try:  # pragma: no cover
        import telegram  # noqa: F401
        import qdrant_client  # noqa: F401
        return False
    except ImportError:
        pass
    from unittest.mock import MagicMock
    for name in _MODULE:
        if name in sys.modules and not isinstance(sys.modules[name], MagicMock):
            continue
        m = MagicMock(name=name)
        m.__name__ = name
        m.__path__ = []
        sys.modules[name] = m
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    return True
