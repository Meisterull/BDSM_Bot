"""
Primärer LLM via OpenAI-kompatiblem Endpoint (Grok, OpenRouter oder Ollama –
siehe config.LLM_PROVIDER). Mit exponential backoff Retry bei Fehlern.
Der Modulname bleibt historisch `grok` – alle Callsites importieren ihn so.
"""
import re
import asyncio
import logging
import httpx
from bot import config

logger = logging.getLogger(__name__)

_client = httpx.AsyncClient(timeout=config.LLM_TIMEOUT)
_HEADERS = {"Content-Type": "application/json"}
if config.LLM_API_KEY:  # Ollama braucht keinen Key
    _HEADERS["Authorization"] = f"Bearer {config.LLM_API_KEY}"

# Retry Konfiguration
_MAX_RETRIES = 3
_BASE_DELAY = 1.0   # Sekunden
_MAX_DELAY = 30.0   # Maximale Wartezeit


async def chat(system_prompt: str, messages: list[dict], reasoning: bool = False,
               temperature: float | None = None, max_tokens: int | None = None,
               frequency_penalty: float | None = None,
               presence_penalty: float | None = None) -> str:
    """
    Sendet Chat-Request an Grok, gibt Antwort-Text zurück.
    Wiederholt bei Fehlern mit exponential backoff.
    reasoning=True verwendet das Reasoning-Modell für komplexe Aufgaben.
    temperature=0 z.B. für deterministische Klassifikation; max_tokens begrenzt die Länge.
    frequency_penalty/presence_penalty (OpenAI-kompatibel) bremsen wortgleiche
    Wiederholungen; default None = unverändert (nur Pfade, die es brauchen, setzen sie).
    """
    model = config.LLM_MODEL_REASONING if reasoning else config.LLM_MODEL
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": 0.7 if temperature is None else temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    # Penalties nur senden, wenn das Modell sie unterstützt – grok-4.3 quittiert
    # sie sonst mit HTTP 400 (siehe config.GROK_SUPPORTS_PENALTIES).
    if config.GROK_SUPPORTS_PENALTIES:
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty

    logger.debug("LLM %s, Modell: %s", config.LLM_PROVIDER, model)

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await _post_chat(config.LLM_API_URL, _HEADERS, payload)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            last_exc = e
            if status == 429 or status >= 500:
                if attempt < _MAX_RETRIES - 1:  # nach dem letzten Versuch nicht mehr schlafen
                    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                    logger.warning(
                        "LLM (%s) HTTP %s – Versuch %d/%d, warte %.1fs",
                        config.LLM_PROVIDER, status, attempt + 1, _MAX_RETRIES, delay
                    )
                    await asyncio.sleep(delay)
            else:
                # Nicht-retrybarer Fehler (z.B. 4xx) – Retry-Schleife abbrechen, Fallback versuchen
                logger.error("LLM (%s) HTTP Fehler %s (kein Retry): %s",
                             config.LLM_PROVIDER, status, e)
                break
        except (httpx.TransportError, KeyError, IndexError, ValueError) as e:
            # TransportError deckt Connect/Read/Write/Timeout UND RemoteProtocolError ab;
            # KeyError/IndexError/ValueError = kaputter Response-Body (resp.json()[...]):
            # solche Antworten (Proxy, Fallback-LLM) sollen genauso Retry+Fallback durchlaufen.
            last_exc = e
            if attempt < _MAX_RETRIES - 1:  # nach dem letzten Versuch nicht mehr schlafen
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "LLM (%s) Verbindungs-/Antwortfehler – Versuch %d/%d, warte %.1fs: %s",
                    config.LLM_PROVIDER, attempt + 1, _MAX_RETRIES, delay, e
                )
                await asyncio.sleep(delay)

    logger.error("LLM (%s) nach %d Versuchen nicht erreichbar.",
                 config.LLM_PROVIDER, _MAX_RETRIES)

    # Optionaler Fallback auf einen OpenAI-kompatiblen Endpoint (z.B. lokales Ollama)
    fallback = await _try_fallback(payload, model)
    if fallback is not None:
        return fallback

    raise last_exc


async def _post_chat(url: str, headers: dict, payload: dict) -> str:
    """Ein OpenAI-kompatibler Chat-Completion-Aufruf, gibt den Antwort-Text zurück."""
    resp = await _client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    # `content` kann bei Refusal/leerem Completion `null` sein → nie None
    # zurückgeben, sonst crashen .strip()-Callsites und der Limit-Check
    # (_normalisiere(None)). Leerer String ist überall sauber behandelt.
    return resp.json()["choices"][0]["message"]["content"] or ""


async def _try_fallback(payload: dict, model: str) -> str | None:
    """Versucht den optionalen Fallback-LLM (falls konfiguriert). None wenn aus/fehlgeschlagen."""
    if not config.FALLBACK_LLM_URL:
        return None
    headers = {"Content-Type": "application/json"}
    if config.FALLBACK_LLM_KEY:
        headers["Authorization"] = f"Bearer {config.FALLBACK_LLM_KEY}"
    fb_payload = {**payload, "model": config.FALLBACK_LLM_MODEL or model}
    try:
        logger.warning("LLM (%s) nicht erreichbar – nutze Fallback-LLM %s",
                       config.LLM_PROVIDER, config.FALLBACK_LLM_URL)
        return await _post_chat(config.FALLBACK_LLM_URL, headers, fb_payload)
    except Exception as e:
        logger.error("Fallback-LLM ebenfalls fehlgeschlagen: %s", e)
        return None


async def simple(prompt: str | tuple, reasoning: bool = False,
                 temperature: float | None = None, max_tokens: int | None = None,
                 system: str = "") -> str:
    """Einfacher Single-Turn Aufruf. `system` erlaubt es, statische Anteile
    (Persona/Stil/Regeln) als System-Message zu trennen, statt alles in die
    User-Message zu mischen (Prompt-Injection-Hygiene).

    `prompt` darf auch ein `(system, user)`-Tupel sein – das ist das Rückgabe-
    format der migrierten Prompt-Builder (bot/prompts/*), damit deren Aufrufer
    nicht alle einzeln entpacken müssen. Ein explizites `system=` gewinnt."""
    if isinstance(prompt, tuple):
        builder_system, prompt = prompt
        system = system or builder_system
    return await chat(system, [{"role": "user", "content": prompt}], reasoning=reasoning,
                      temperature=temperature, max_tokens=max_tokens)


def clean_text(text: str) -> str:
    """Entfernt Whitespace und umschließende Anführungszeichen aus LLM-Output
    (zentral statt 8× `.strip().strip('"').strip("'")` an den Callsites)."""
    return (text or "").strip().strip('"').strip("'").strip()


def parse_json(text: str):
    """Parst JSON aus LLM-Output robust: entfernt Markdown-Code-Fences
    (```json … ```) und schneidet notfalls das äußerste {...}/[...] aus
    umgebendem Text heraus. Wirft json.JSONDecodeError wenn nichts parsebar ist."""
    import json
    t = (text or "").strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
        if m:
            t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        for open_c, close_c in (("{", "}"), ("[", "]")):
            start, end = t.find(open_c), t.rfind(close_c)
            if start != -1 and end > start:
                try:
                    return json.loads(t[start:end + 1])
                except json.JSONDecodeError:
                    continue
        raise


def extract_task(text: str) -> tuple[bool, str]:
    """
    Versucht Aufgabe aus Grok-Antwort zu extrahieren.
    Gibt (gefunden, aufgabe_text) zurück.
    Pattern: [AUFGABE: <beschreibung>]
    """
    match = re.search(r"\[AUFGABE:\s*(.+?)\]", text, re.DOTALL)
    if match:
        return True, match.group(1).strip()
    return False, ""


def extract_keyword_task(text: str) -> tuple[bool, str]:
    """
    Direkte Erkennung via "Aufgabe:" (bzw. "Task:" bei nicht-deutscher
    Nutzung) am Anfang der Nachricht.
    """
    stripped = text.strip()
    for prefix in ("aufgabe:", "task:"):
        if stripped.lower().startswith(prefix):
            return True, stripped[len(prefix):].strip()
    return False, ""