"""
Lokales Fallback-LLM (Ollama) für den Sklaven-Chat bei Grok-Ausfall.

Bewusst getrennt vom generischen FALLBACK_LLM_* in grok.py: der reicht den
vollen Payload weiter, aber ein lokales CPU-Modell schafft nur ~12 tok/s
Prompt-Verarbeitung – der volle Sklaven-Prompt (mehrere tausend Tokens) hieße
Minuten bis zum ersten Token. Dieser Pfad bekommt deshalb einen Kurz-Prompt
(bot/prompts/sklave.get_kurz) und eine gestutzte History.

Kein Retry: bei Grok-Ausfall hat der Nutzer schon die grok.chat-Retries
abgewartet; schlägt auch das lokale Modell fehl, soll sofort die statische
Fallback-Nachricht kommen.
"""
import logging
import httpx
from bot import config

logger = logging.getLogger(__name__)

# Eigener Client mit langem Read-Timeout: ~60-90s Antwortzeit sind auf CPU normal.
_client = httpx.AsyncClient(timeout=httpx.Timeout(config.LOKAL_LLM_TIMEOUT, connect=10.0))


def aktiv() -> bool:
    return bool(config.LOKAL_LLM_MODEL)


async def chat_kurz(system: str, messages: list[dict], max_tokens: int = 160) -> str:
    """Ein Chat-Aufruf gegen das lokale Ollama-Modell. Gibt "" bei leerer Antwort
    zurück (Konvention wie grok._post_chat), wirft bei HTTP-/Transportfehlern."""
    payload = {
        "model": config.LOKAL_LLM_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": 0.9,
        # Begrenzte Länge: bei ~3-4 tok/s auf CPU ist jedes Token Wartezeit,
        # und die Herrin-Antworten sollen ohnehin kurz sein.
        "max_tokens": max_tokens,
    }
    url = f"{config.OLLAMA_URL.rstrip('/')}/v1/chat/completions"
    logger.warning("Grok-Ausfall: nutze lokales Fallback-Modell %s", config.LOKAL_LLM_MODEL)
    resp = await _client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def kuerze_history(history: list[dict], anzahl: int = 4, max_zeichen: int = 300) -> list[dict]:
    """Letzte `anzahl` Nachrichten, jede auf `max_zeichen` gestutzt – hält die
    Prompt-Verarbeitung des CPU-Modells im erträglichen Bereich."""
    return [
        {"role": m["role"], "content": m["content"][:max_zeichen]}
        for m in history[-anzahl:]
    ]
