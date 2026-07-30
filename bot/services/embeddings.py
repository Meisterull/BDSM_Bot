"""
Ollama Embeddings via jina-embeddings-v2-base-de (768 dim, Default in config.OLLAMA_MODEL).
Mit exponential backoff Retry bei Fehlern.
"""
import asyncio
import logging
import httpx

from bot import config

logger = logging.getLogger(__name__)

_client = httpx.AsyncClient(timeout=30.0)

# Retry Konfiguration
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


async def get_embedding(text: str) -> list[float]:
    """
    Gibt Dense-Embedding-Vektor (768 dim) zurück.
    Wiederholt bei Fehlern mit exponential backoff.
    """
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            resp = await _client.post(
                f"{config.OLLAMA_URL}/api/embeddings",
                json={"model": config.OLLAMA_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            vector = resp.json()["embedding"]
            # Klarer Fehler bei Modellwechsel (OLLAMA_MODEL) statt kryptischer
            # Qdrant-Upsert-Fehler weit weg vom eigentlichen Problem.
            if len(vector) != config.EMBEDDING_DIM:
                raise RuntimeError(
                    f"Embedding-Dimension {len(vector)} != EMBEDDING_DIM {config.EMBEDDING_DIM} – "
                    f"OLLAMA_MODEL geändert? Dann bot/tools/migrate_embeddings.py ausführen."
                )
            return vector

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            # 429 mit-retryen (Hermes-Review C3): lokales Ollama sendet es zwar
            # praktisch nie, aber ein vorgeschalteter Proxy könnte.
            if status == 429 or status >= 500:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:  # nach dem letzten Versuch nicht mehr schlafen
                    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                    logger.warning(
                        "Ollama HTTP %s – Versuch %d/%d, warte %.1fs",
                        status, attempt + 1, _MAX_RETRIES, delay
                    )
                    await asyncio.sleep(delay)
            else:
                logger.error("Ollama HTTP Fehler %s (kein Retry): %s", status, e)
                raise

        except httpx.TransportError as e:
            # Supertyp statt Einzel-Klassen (Hermes-Review H5): deckt auch
            # RemoteProtocolError/WriteError ab – grok.py macht es genauso.
            last_exc = e
            if attempt < _MAX_RETRIES - 1:  # nach dem letzten Versuch nicht mehr schlafen
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "Ollama Verbindungsfehler – Versuch %d/%d, warte %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, delay, e
                )
                await asyncio.sleep(delay)

    logger.error("Ollama nach %d Versuchen nicht erreichbar.", _MAX_RETRIES)
    raise last_exc
