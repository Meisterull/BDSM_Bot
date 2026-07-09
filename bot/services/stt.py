"""
STT-Service – Sprachnachrichten verstehen via Whisper (Wyoming-Protokoll).

Gegenstück zu tts.py: Telegram-Voice (OGG/Opus) wird mit opusdec (opus-tools,
siehe Dockerfile) zu PCM dekodiert und an einen wyoming-whisper-Server im
eigenen Netz geschickt (STT_WYOMING_URL, z.B. tcp://192.0.2.10:10300).
Vollständig lokal – kein Cloud-Leak intimer Sprachnachrichten.

Default AUS (STT_WYOMING_URL leer) → aktiv() False, Sprachnachrichten laufen
dann wie bisher als Medien-Weiterleitung an den anderen Chat.
"""
import asyncio
import io
import json
import logging
import wave

from bot import config

logger = logging.getLogger(__name__)

_TIMEOUT = 60  # Whisper small-int8 braucht auf CPU einige Sekunden pro Minute Audio
_CHUNK = 4096  # PCM-Bytes pro audio-chunk


def _host_port() -> tuple[str, int] | None:
    url = (config.STT_WYOMING_URL or "").strip()
    if not url:
        return None
    url = url.removeprefix("tcp://")
    host, _, port = url.rpartition(":")
    if not host or not port.isdigit():
        logger.warning("STT_WYOMING_URL unverständlich: %r – STT aus", config.STT_WYOMING_URL)
        return None
    return host, int(port)


def aktiv() -> bool:
    return _host_port() is not None


async def _ogg_zu_wav(ogg: bytes) -> bytes | None:
    """OGG/Opus → WAV (16 kHz mono) via opusdec. None wenn Decoder fehlt/failt."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "opusdec", "--quiet", "--rate", "16000", "--force-wav", "-", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("opusdec nicht installiert (opus-tools im Image?) – STT nicht möglich")
        return None
    stdout, _ = await proc.communicate(ogg)
    if proc.returncode != 0 or not stdout:
        logger.warning("opusdec fehlgeschlagen (rc=%s)", proc.returncode)
        return None
    return stdout


def _wav_parameter(wav: bytes) -> tuple[bytes, int, int, int]:
    """WAV → (frames, rate, width, channels)."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth(), w.getnchannels()


def _event(typ: str, data: dict | None = None) -> bytes:
    data_bytes = json.dumps(data or {}).encode("utf-8")
    header = {"type": typ, "data_length": len(data_bytes)}
    return json.dumps(header).encode("utf-8") + b"\n" + data_bytes


async def _wyoming_transcribe(pcm: bytes, rate: int, width: int, channels: int) -> str | None:
    ziel = _host_port()
    if not ziel:
        return None
    reader, writer = await asyncio.open_connection(*ziel)
    try:
        # Sprach-Hint DES PAARES (persona_config.sprach_code, Kontext ist beim
        # Voice-Handling gesetzt) – Whisper ist multilingual, der Hint erspart
        # die Auto-Detection und verhindert Zwangs-Deutsch bei EN-Paaren.
        # Fallback: globales STT_SPRACHE, dann Server-Default.
        from bot.services import persona_config
        try:
            sprache = persona_config.sprach_code() or (config.STT_SPRACHE or "").strip()
        except Exception:
            sprache = (config.STT_SPRACHE or "").strip()
        writer.write(_event("transcribe", {"language": sprache} if sprache else {}))
        writer.write(_event("audio-start", {"rate": rate, "width": width, "channels": channels}))
        for i in range(0, len(pcm), _CHUNK):
            chunk = pcm[i:i + _CHUNK]
            data_bytes = json.dumps({"rate": rate, "width": width, "channels": channels}).encode()
            header = {"type": "audio-chunk", "data_length": len(data_bytes), "payload_length": len(chunk)}
            writer.write(json.dumps(header).encode() + b"\n" + data_bytes + chunk)
        writer.write(_event("audio-stop"))
        await writer.drain()

        while True:
            zeile = await reader.readline()
            if not zeile:
                return None
            evt = json.loads(zeile)
            edata = evt.get("data") or {}
            data_len = int(evt.get("data_length") or 0)
            payload_len = int(evt.get("payload_length") or 0)
            if data_len:
                edata = json.loads(await reader.readexactly(data_len))
            if payload_len:
                await reader.readexactly(payload_len)
            if evt.get("type") == "transcript":
                return (edata.get("text") or "").strip() or None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 – Cleanup darf nie werfen
            pass


async def transcribe(ogg: bytes) -> str | None:
    """Telegram-Voice-Bytes → Transkript. None bei aus/Fehler/leerem Ergebnis."""
    if not aktiv() or not ogg:
        return None
    try:
        async with asyncio.timeout(_TIMEOUT):
            wav = await _ogg_zu_wav(ogg)
            if not wav:
                return None
            pcm, rate, width, channels = _wav_parameter(wav)
            if not pcm:
                return None
            return await _wyoming_transcribe(pcm, rate, width, channels)
    except Exception:
        logger.exception("STT-Transkription fehlgeschlagen")
        return None
