"""
Primärer LLM via OpenAI-kompatiblem Endpoint (Grok, OpenRouter oder Ollama –
siehe config.LLM_PROVIDER). Mit exponential backoff Retry bei Fehlern.
Der Modulname bleibt historisch `grok` – alle Callsites importieren ihn so.
"""
import re
import asyncio
import logging
import time
import httpx
from bot import config

logger = logging.getLogger(__name__)

_client = httpx.AsyncClient(timeout=config.LLM_TIMEOUT)
_HEADERS = {"Content-Type": "application/json"}
if config.LLM_API_KEY:  # Ollama braucht keinen Key
    _HEADERS["Authorization"] = f"Bearer {config.LLM_API_KEY}"

# Steuer-Token, die Modelle gelegentlich als Text mitliefern (<|eos|>,
# <|im_end|>, <|eot_id|> …). Live-Befund 12.11.2026: so ein Token hing an
# gespeicherten Kurzlabels und wanderte von dort in spätere Prompts.
_STEUER_TOKEN = re.compile(r"<\|[A-Za-z0-9_\-]{1,40}\|>")


def ohne_steuer_token(text: str) -> str:
    """Entfernt mitgelieferte Steuer-Token aus LLM-Output (zentral für alle
    Antwortwege: Primär-/Fallback-Endpoint und beide lokalen Modelle)."""
    if not text or "<|" not in text:
        return text or ""
    return _STEUER_TOKEN.sub("", text).strip()


# Retry Konfiguration
_MAX_RETRIES = 3
_BASE_DELAY = 1.0   # Sekunden
_MAX_DELAY = 30.0   # Maximale Wartezeit


def _log_ausgabe(text: str) -> None:
    """Jede Modell-Antwort im Wortlaut auf DEBUG – also nur in Logdatei und
    Logserver, nicht in den Docker-json-Logs (wie GESENDET-INHALT). So lässt
    sich nachsehen, was ein verworfener Entwurf enthielt (Limit-Treffer,
    Detektor-Retry), der nie als Nachricht rausging."""
    logger.debug("LLM-AUSGABE | %s", (text or "").replace("\n", " ⏎ "))


async def chat(system_prompt: str, messages: list[dict], reasoning: bool = False,
               temperature: float | None = None, max_tokens: int | None = None,
               frequency_penalty: float | None = None,
               presence_penalty: float | None = None, lokal: bool = False,
               modell: str | None = None, reasoning_effort: str | None = None) -> str:
    """
    Sendet Chat-Request an Grok, gibt Antwort-Text zurück.
    Wiederholt bei Fehlern mit exponential backoff.
    reasoning=True verwendet das Reasoning-Modell für komplexe Aufgaben.
    temperature=0 z.B. für deterministische Klassifikation; max_tokens begrenzt die Länge.
    frequency_penalty/presence_penalty (OpenAI-kompatibel) bremsen wortgleiche
    Wiederholungen; default None = unverändert (nur Pfade, die es brauchen, setzen sie).
    modell überschreibt die Modellwahl für diesen einen Aufruf (z.B. das Prüf-Modell
    der Machbarkeits-Prüfung); reasoning_effort (low|medium|high) geht nur mit, wenn
    gesetzt – nicht jedes Modell kennt den Parameter (HTTP 400 → einmal ohne ihn).
    """
    model = modell or (config.LLM_MODEL_REASONING if reasoning else config.LLM_MODEL)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": 0.7 if temperature is None else temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    # Ohne ausdrückliche Vorgabe gilt für Reasoning-Aufrufe die konfigurierte Denkstufe.
    if reasoning_effort is None and reasoning and not modell:
        reasoning_effort = config.LLM_REASONING_EFFORT or None
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    # Penalties nur senden, wenn das Modell sie unterstützt – grok-4.3 quittiert
    # sie sonst mit HTTP 400 (siehe config.GROK_SUPPORTS_PENALTIES).
    if config.GROK_SUPPORTS_PENALTIES:
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty

    logger.debug("LLM %s, Modell: %s", config.LLM_PROVIDER, model)

    # Generator-Test (11.11.2026): ausgewählte Generatoren (Tiny-Task, Wett-Idee)
    # gehen zuerst ans lokale Generator-Modell – mit vollem Live-Prompt. Fehler,
    # Zeitüberschreitung oder leere Antwort → ganz normal weiter zu Grok.
    if lokal and config.GENERATOR_LLM_MODEL:
        antwort = await _generator_lokal(payload, max_tokens)
        if antwort:
            return antwort

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await _post_chat(config.LLM_API_URL, _HEADERS, payload)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            last_exc = e
            # Netz für reasoning_effort: Modelle ohne den Parameter quittieren ihn
            # mit HTTP 400 (xAI: grok-4.20-reasoning). Einmal ohne ihn wiederholen,
            # statt wegen einer Konfig-Kleinigkeit in den langsamen Fallback zu laufen.
            if status == 400 and "reasoning_effort" in payload:
                logger.warning("LLM (%s) lehnt reasoning_effort für %s ab – wiederhole ohne.",
                               config.LLM_PROVIDER, model)
                payload.pop("reasoning_effort")
                continue
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


async def _post_chat(url: str, headers: dict, payload: dict,
                     timeout: float | None = None) -> str:
    """Ein OpenAI-kompatibler Chat-Completion-Aufruf, gibt den Antwort-Text zurück.

    `timeout` überschreibt das Client-Timeout für diesen einen Aufruf (der
    Fallback-LLM braucht deutlich länger als der Primär-Provider). Nicht
    einfach `timeout=None` durchreichen: httpx liest das als "gar kein
    Timeout", nicht als "Client-Vorgabe" – deshalb nur setzen, wenn übergeben.
    """
    extra = {"timeout": timeout} if timeout is not None else {}
    start = time.monotonic()
    resp = await _client.post(url, headers=headers, json=payload, **extra)
    resp.raise_for_status()
    daten = resp.json()
    # Beobachtbarkeit (24.08.2026): Dauer + Token-Verbrauch je Call — vorher
    # waren Kosten und Latenz komplett unsichtbar (kein usage-Logging, keine
    # Zeitmessung im ganzen Bot). Eine INFO-Zeile pro Call reicht: der
    # Logserver/`docker logs` machen daraus Tages-Summen per grep.
    usage = daten.get("usage") or {}
    # Denk-Tokens (11.11.2026): bei Reasoning-Modellen stecken Wartezeit und
    # Kosten fast ganz in den reasoning_tokens, die in `out` NICHT enthalten
    # sind (Live: out=5, denk≈1900, 20 s). cache = Anteil des Prompts aus dem
    # Präfix-Cache des Anbieters. Beide nur anhängen, wenn vorhanden.
    zusatz = ""
    denk = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if denk:
        zusatz += f" denk={denk}"
    cache = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cache:
        zusatz += f" cache={cache}"
    logger.info(
        "LLM-Call %s: %.1fs | Tokens in=%s out=%s%s",
        payload.get("model"), time.monotonic() - start,
        usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"), zusatz,
    )
    # `content` kann bei Refusal/leerem Completion `null`/leer sein. Das ist wie
    # ein Fehler zu behandeln (ValueError → Retry+Fallback-Kaskade in chat()):
    # ein stilles "" würde sonst an reply_text("") crashen bzw. Antworten
    # verschlucken – im schlimmsten Fall NACH einem Status-Update (gefuehl.py).
    content = ohne_steuer_token(daten["choices"][0]["message"]["content"])
    _log_ausgabe(content)
    if not content or not content.strip():
        raise ValueError("LLM lieferte leere Antwort (Refusal/leeres Completion)")
    return content


async def _try_fallback(payload: dict, model: str) -> str | None:
    """Versucht den optionalen Fallback-LLM (falls konfiguriert). None wenn aus/fehlgeschlagen."""
    if not config.FALLBACK_LLM_URL:
        return None
    headers = {"Content-Type": "application/json"}
    if config.FALLBACK_LLM_KEY:
        headers["Authorization"] = f"Bearer {config.FALLBACK_LLM_KEY}"
    fb_payload = {**payload, "model": config.FALLBACK_LLM_MODEL or model}
    fb_payload.pop("reasoning_effort", None)  # anbieter-spezifisch, der Fallback kennt ihn nicht
    try:
        logger.warning("LLM (%s) nicht erreichbar – nutze Fallback-LLM %s (Modell %s, Timeout %.0fs)",
                       config.LLM_PROVIDER, config.FALLBACK_LLM_URL,
                       fb_payload.get("model"), config.FALLBACK_LLM_TIMEOUT)
        return await _post_chat(config.FALLBACK_LLM_URL, headers, fb_payload,
                                timeout=config.FALLBACK_LLM_TIMEOUT)
    except Exception as e:
        logger.error("Fallback-LLM ebenfalls fehlgeschlagen: %s", e)
        return None


async def _generator_lokal(payload: dict, max_tokens: int | None) -> str | None:
    """Ein Aufruf ans lokale Generator-Modell (Ollama /api/chat, damit num_ctx
    gesetzt werden kann – der OpenAI-Endpunkt schneidet lange Live-Prompts ab).
    None bei Fehler/leer; der Aufrufer fällt dann auf Grok zurück."""
    body = {
        "model": config.GENERATOR_LLM_MODEL,
        "messages": payload["messages"],
        "stream": False,
        "options": {
            "temperature": payload.get("temperature", 0.7),
            "num_predict": max_tokens or 900,
            "num_ctx": config.GENERATOR_LLM_CTX,
            "num_thread": config.GENERATOR_LLM_THREADS,
        },
    }
    start = time.monotonic()
    try:
        resp = await _client.post(f"{config.OLLAMA_URL.rstrip('/')}/api/chat", json=body,
                                  timeout=config.GENERATOR_LLM_TIMEOUT)
        resp.raise_for_status()
        daten = resp.json()
        text = ohne_steuer_token(((daten.get("message") or {}).get("content") or "").strip())
        _log_ausgabe(text)
        logger.info("LLM-Call %s (lokal, Generator): %.1fs | Tokens in=%s out=%s",
                    config.GENERATOR_LLM_MODEL, time.monotonic() - start,
                    daten.get("prompt_eval_count", "?"), daten.get("eval_count", "?"))
        if not text:
            logger.warning("Lokales Generator-Modell lieferte leere Antwort – nutze Grok.")
            return None
        return text
    except Exception as e:
        logger.warning("Lokales Generator-Modell fehlgeschlagen nach %.0fs (%s: %s) – nutze Grok.",
                       time.monotonic() - start, type(e).__name__, e)
        return None


async def simple(prompt: str | tuple, reasoning: bool = False,
                 temperature: float | None = None, max_tokens: int | None = None,
                 system: str = "", lokal: bool = False,
                 modell: str | None = None, reasoning_effort: str | None = None) -> str:
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
                      temperature=temperature, max_tokens=max_tokens, lokal=lokal,
                      modell=modell, reasoning_effort=reasoning_effort)


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


def extract_sprachnachricht(text: str) -> tuple[bool, str]:
    """Extrahiert eine vom Coach markierte Nachricht an den Sklaven.
    Pattern: [SPRACHNACHRICHT: <inhalt>] – analog extract_task."""
    match = re.search(r"\[SPRACHNACHRICHT:\s*(.+?)\]", text, re.DOTALL)
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