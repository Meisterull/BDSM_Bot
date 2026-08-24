"""
TTS-Service – Sprachnachrichten via Grok-TTS (Cloud) und/oder Piper (lokal).

Zwei Backends, beide enden in der gleichen opusenc-Kette (opus-tools, siehe
Dockerfile) → OGG/Opus, das Format für echte Telegram-Voice-Bubbles:
- Grok-TTS (GROK_TTS=1, api.x.ai/v1/tts, XAI_API_KEY): expressive multilinguale
  Stimmen, EINE pro Empfänger-Rolle (der Sklave hört die Herrin, die Domina den
  Coach). Der vorzulesende Text stammt ohnehin von Grok – TTS schickt also
  nichts Neues in die Cloud. WAV angefordert ({"codec": "wav"}), damit die
  lokale Opus-Kette unverändert weiterläuft.
- Piper (TTS_WYOMING_URL, Wyoming-Protokoll): vollständig lokal, Fallback wenn
  Grok aus/fehlschlägt.

Beides aus → synthesize() liefert None, alle Aufrufer sind best-effort und
senden dann nur Text. Fehler dürfen NIE eine Text-Zustellung verhindern.
"""
import asyncio
import io
import json
import logging
import re
import wave

import httpx

from bot import config

logger = logging.getLogger(__name__)

_TIMEOUT = 20  # Sekunden für Synthese + Encoding zusammen (best-effort Pfad)

# Empfänger-Rolle → Grok-Stimme. Bewusst über die EMPFÄNGER-Seite: Voice an den
# Sub spricht die Herrin-Persona, Voice an die Dom-Seite den Coach.
ROLLE_HERRIN = "herrin"
ROLLE_COACH = "coach"

# Sprech-Tags, die Grok-TTS versteht: eckige Einschübe ([laugh], [pause] …)
# und wickelnde Stil-Tags (<whisper>…</whisper> …). Die Anleitung wandert in
# Generator-Prompts (nur bei GROK_TTS – Piper würde die Tags vorlesen), der
# Entferner macht Texte für die Text-Bubble bzw. den Piper-Fallback sauber.
SPRECH_TAG_ANLEITUNG = (
    "Vertonungs-Tags (die Nachricht wird als Sprachnachricht gesprochen): setze SPARSAM "
    "2-4 Sprech-Tags, wo sie Wirkung tragen – [laugh] [sigh] [pause] [long-pause] als "
    "eigene Einschübe, <whisper>…</whisper> <soft>…</soft> <slow>…</slow> um kurze "
    "Passagen (z.B. eine Drohung geflüstert, eine Pause vor der Pointe, ein spöttisches "
    "Lachen). Keine anderen Tags, nichts erklären."
)
_INLINE_TAG_RE = re.compile(r"\[(?:pause|long-pause|laugh|cry|sigh|breath|giggle|whisper)\]", re.I)
_WRAP_TAG_RE = re.compile(r"</?(?:whisper|soft|loud|slow|fast|singing)>", re.I)


def entferne_sprech_tags(text: str) -> str:
    """Nimmt Grok-Sprech-Tags aus einem Text – für die Text-Darstellung in der
    Chat-Bubble und für den Piper-Fallback (der läse '[laugh]' sonst wörtlich vor)."""
    text = _INLINE_TAG_RE.sub("", text or "")
    text = _WRAP_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


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


def bereinige(text: str, tags_erhalten: bool = False) -> str:
    """Macht einen Chat-Text vorlesbar: Markdown-Zeichen und Emojis raus,
    Mehrfach-Whitespace glätten. Kein NLP – Piper liest den Rest gut.
    tags_erhalten=True (Grok-Pfad) lässt `>` stehen, damit Sprech-Tags wie
    <whisper>…</whisper> überleben – Piper bekommt solche Tags nie zu sehen."""
    text = re.sub(r"[*_`#|]" if tags_erhalten else r"[*_`#>|]", "", text or "")
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


def _grok_voice(rolle: str) -> str:
    return (config.GROK_TTS_VOICE_COACH if rolle == ROLLE_COACH
            else config.GROK_TTS_VOICE_HERRIN)


async def _grok_wav(text: str, rolle: str) -> bytes | None:
    """Grok-TTS: Text → WAV-Bytes (16-bit-PCM laut codec-Anforderung).
    Wirft bei HTTP-Fehlern – der Aufrufer fängt und fällt auf Piper zurück."""
    from bot.services import persona_config  # lazy wie _voice_fuer_kontext
    try:
        sprache = persona_config.sprach_code() or "de"
    except Exception:
        sprache = "de"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.x.ai/v1/tts",
            headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
            json={
                "text": text,
                "voice_id": _grok_voice(rolle),
                "language": sprache,
                # WAV statt MP3-Default: das Image hat nur opusenc, und der
                # frisst WAV – die lokale Opus-Kette bleibt für beide Backends.
                "output_format": {"codec": "wav"},
            },
        )
        resp.raise_for_status()
        return resp.content or None


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


def _gekuerzt(text: str) -> str:
    if len(text) > config.TTS_MAX_ZEICHEN:
        return text[: config.TTS_MAX_ZEICHEN].rsplit(" ", 1)[0] + " …"
    return text


async def synthesize(text: str, rolle: str = ROLLE_HERRIN) -> bytes | None:
    """Text → OGG/Opus-Bytes für Telegram send_voice. None = TTS aus/Fehler
    (Aufrufer sendet dann einfach keinen Voice-Zusatz).
    `rolle` wählt die Grok-Stimme (Empfänger-Seite: herrin|coach)."""
    # 1) Grok-TTS (Gate + Key nötig); Fehler → still weiter zu Piper.
    if config.GROK_TTS and config.XAI_API_KEY:
        grok_text = _gekuerzt(bereinige(text, tags_erhalten=True))
        if grok_text:
            try:
                async with asyncio.timeout(_TIMEOUT):
                    wav = await _grok_wav(grok_text, rolle)
                    if wav:
                        ogg = await _als_opus(wav)
                        if ogg:
                            return ogg
            except Exception:
                logger.exception("Grok-TTS fehlgeschlagen – versuche Piper-Fallback")

    # 2) Piper (lokal) – Original-Verhalten. Sprech-Tags raus: Piper kennt sie
    # nicht und läse eckige Tags wörtlich vor.
    if not _host_port():
        return None
    text = _gekuerzt(bereinige(entferne_sprech_tags(text)))
    if not text:
        return None
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
