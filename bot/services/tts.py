"""
TTS-Service – Sprachnachrichten der Herrin via Piper (Wyoming-Protokoll).

Vollständig lokal: der Text geht an einen wyoming-piper-Server im eigenen Netz
(TTS_WYOMING_URL, z.B. tcp://192.0.2.10:10200), das PCM wird im Container mit
opusenc (opus-tools, siehe Dockerfile) in OGG/Opus gewandelt – das Format, das
Telegram für echte Voice-Bubbles verlangt. Kein Cloud-Leak intimer Inhalte.

Default AUS (TTS_WYOMING_URL leer) → synthesize() liefert None, alle Aufrufer
sind best-effort und senden dann nur Text. Fehler dürfen NIE eine Text-
Zustellung verhindern.
"""
import asyncio
import io
import json
import logging
import re
import wave

from bot import config

logger = logging.getLogger(__name__)

_TIMEOUT = 20  # Sekunden für Synthese + Encoding zusammen (best-effort Pfad)


def _host_port() -> tuple[str, int] | None:
    """Parst TTS_WYOMING_URL ('tcp://host:port' oder 'host:port'). None = aus."""
    url = (config.TTS_WYOMING_URL or "").strip()
    if not url:
        return None
    url = url.removeprefix("tcp://")
    host, _, port = url.rpartition(":")
    if not host or not port.isdigit():
        logger.warning("TTS_WYOMING_URL unverständlich: %r – TTS aus", config.TTS_WYOMING_URL)
        return None
    return host, int(port)


def bereinige(text: str) -> str:
    """Macht einen Chat-Text vorlesbar: Markdown-Zeichen und Emojis raus,
    Mehrfach-Whitespace glätten. Kein NLP – Piper liest den Rest gut."""
    text = re.sub(r"[*_`#>|]", "", text or "")
    # Emojis / Symbole (außerhalb Basis-Multilingual-Plane + Misc-Symbole)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _voice_fuer_kontext() -> str:
    """Piper-Stimme für die Sprache des Kontext-Paares: TTS_STIMMEN-Map über
    persona_config.sprach_code(), Fallback TTS_VOICE ("" = Server-Default).
    Piper-Stimmen sind einsprachig – ohne passende Stimme liest die
    Default-Stimme fremdsprachigen Text mit falscher Phonetik."""
    from bot.services import persona_config  # lazy: kein Import-Gewicht wenn TTS aus
    try:
        code = persona_config.sprach_code()
    except Exception:
        code = ""
    return (config.TTS_STIMMEN.get(code) if code else None) or config.TTS_VOICE


async def _wyoming_pcm(text: str) -> tuple[bytes, int, int, int] | None:
    """Holt rohes PCM vom Wyoming-Server. Returns (pcm, rate, width, channels)."""
    ziel = _host_port()
    if not ziel:
        return None
    reader, writer = await asyncio.open_connection(*ziel)
    try:
        data: dict = {"text": text}
        voice = _voice_fuer_kontext()
        if voice:
            data["voice"] = {"name": voice}
        data_bytes = json.dumps(data).encode("utf-8")
        header = {"type": "synthesize", "data_length": len(data_bytes)}
        writer.write(json.dumps(header).encode("utf-8") + b"\n" + data_bytes)
        await writer.drain()

        pcm = bytearray()
        rate, width, channels = 22050, 2, 1
        while True:
            zeile = await reader.readline()
            if not zeile:
                break
            evt = json.loads(zeile)
            edata = evt.get("data") or {}
            data_len = int(evt.get("data_length") or 0)
            payload_len = int(evt.get("payload_length") or 0)
            if data_len:
                edata = json.loads(await reader.readexactly(data_len))
            payload = await reader.readexactly(payload_len) if payload_len else b""
            typ = evt.get("type")
            if typ == "audio-start":
                rate = int(edata.get("rate", rate))
                width = int(edata.get("width", width))
                channels = int(edata.get("channels", channels))
            elif typ == "audio-chunk":
                pcm += payload
            elif typ == "audio-stop":
                break
        return (bytes(pcm), rate, width, channels) if pcm else None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 – Cleanup darf nie werfen
            pass


def _als_wav(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def _als_opus(wav: bytes) -> bytes | None:
    """WAV → OGG/Opus via opusenc (opus-tools). None wenn Encoder fehlt/failt."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "opusenc", "--quiet", "--bitrate", "32", "-", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("opusenc nicht installiert (opus-tools im Image?) – kein Voice-Versand")
        return None
    stdout, _ = await proc.communicate(wav)
    if proc.returncode != 0 or not stdout:
        logger.warning("opusenc fehlgeschlagen (rc=%s)", proc.returncode)
        return None
    return stdout


async def synthesize(text: str) -> bytes | None:
    """Text → OGG/Opus-Bytes für Telegram send_voice. None = TTS aus/Fehler
    (Aufrufer sendet dann einfach keinen Voice-Zusatz)."""
    if not _host_port():
        return None
    text = bereinige(text)
    if not text:
        return None
    if len(text) > config.TTS_MAX_ZEICHEN:
        text = text[: config.TTS_MAX_ZEICHEN].rsplit(" ", 1)[0] + " …"
    try:
        async with asyncio.timeout(_TIMEOUT):
            ergebnis = await _wyoming_pcm(text)
            if not ergebnis:
                return None
            wav = _als_wav(*ergebnis)
            return await _als_opus(wav)
    except Exception:
        logger.exception("TTS-Synthese fehlgeschlagen (best-effort, Text ging normal raus)")
        return None
