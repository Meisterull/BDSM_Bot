"""
Helper für Telegram Nachrichten.
"""
import asyncio
import contextlib
import logging
import re
from telegram import Bot
from telegram.constants import ChatAction
from telegram.error import BadRequest
from bot import config

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def typing_action(bot: Bot, chat_id):
    """Zeigt durchgehend 'tippt…' im Chat, solange der with-Block läuft.

    Telegram blendet die Aktion nach ~5s aus, daher erneuern wir sie alle 4s –
    so fühlen sich lange Grok-Antworten (10–20s) nicht nach 'hängt' an.
    """
    async def _loop():
        try:
            while True:
                try:
                    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception:
                    pass  # Tipp-Indikator ist rein kosmetisch – nie den Hauptpfad stören
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()


TELEGRAM_MAX_ZEICHEN = 4096


def nachricht_teilen(text: str, limit: int = TELEGRAM_MAX_ZEICHEN) -> list[str]:
    """Teilt eine Nachricht in sendbare Stücke ≤ limit (Telegram: 4096 Zeichen).

    Schnitt bevorzugt an Absatz-, dann Zeilen-, dann Satzgrenzen; harter Schnitt
    nur als letzte Rettung. Kurze Texte kommen unverändert als Ein-Element-Liste
    zurück (04.09.2026: 5893-Zeichen-Antwort → BadRequest 'Message is too long').
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    teile = []
    while len(text) > limit:
        fenster = text[:limit]
        schnitt = -1
        for muster in ("\n\n", "\n", ". "):
            schnitt = fenster.rfind(muster)
            if schnitt > limit // 2:
                schnitt += len(muster.rstrip())  # Satzpunkt bleibt beim ersten Teil
                break
            schnitt = -1
        if schnitt <= 0:
            schnitt = limit
        teile.append(text[:schnitt].strip())
        text = text[schnitt:].strip()
    if text:
        teile.append(text)
    return teile


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))


def strip_md(text: str) -> str:
    """Entfernt häufige Markdown-Marker, falls Text ohne parse_mode gesendet wird."""
    if not text:
        return text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", text)
    text = re.sub(r"_([^_\n]+?)_", r"\1", text)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)
    return text


def md_einbett_sicher(text: str) -> str:
    """Macht LLM-/Nutzer-Freitext sicher für die Einbettung IN ein Markdown-
    Template (z.B. _{inhalt}_ in TINYFB_FRAGE): gepaarte Marker werden Klartext
    (strip_md), übrig bleibende einzelne Marker entfernt. Ein einzelnes
    '*'/'_'/'['/'`' im eingebetteten Freitext bricht sonst das UMGEBENDE
    Template-Markup – die 21:30-Rückfrage fiel dadurch fast täglich in den
    strip_md-Fallback und verlor ihre komplette Formatierung (Log 02.–07.08.)."""
    return re.sub(r"[*_`\[\]]", "", strip_md(text or ""))


async def _send_with_md_fallback(bot: Bot, chat_id: str, text: str, parse_mode: str | None,
                                 reply_markup=None) -> None:
    """Sendet die Nachricht, mit Fallback auf strip_md bei Markdown-Parse-Fehlern.

    reply_markup (z.B. Inline-Buttons) bleibt im Fallback erhalten.
    """
    if parse_mode is None:
        await bot.send_message(chat_id=chat_id, text=strip_md(text), parse_mode=None,
                               reply_markup=reply_markup)
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode,
                               reply_markup=reply_markup)
    except BadRequest as e:
        if "parse" in str(e).lower():
            logger.warning("Markdown-Parse fehlgeschlagen (chat %s), sende strip_md: %s", chat_id, e)
            await bot.send_message(chat_id=chat_id, text=strip_md(text), parse_mode=None,
                                   reply_markup=reply_markup)
        else:
            raise


async def send_an(bot: Bot, paar, rolle: str, text: str, parse_mode: str | None = None,
                  reply_markup=None, voice_text: str | None = None) -> None:
    """Paar-parametrisierter Versand an eine Rolle (Multiuser-Fundament).

    `voice_text`: wenn gesetzt (und TTS konfiguriert, s. services/tts.py),
    kommt NACH dem Text zusätzlich eine Voice-Message mit diesem Wortlaut –
    best-effort, ein TTS-Fehler beeinträchtigt den Text nie.
    """
    chat_id = paar.chat_id(rolle)
    await _send_with_md_fallback(bot, chat_id, text, parse_mode, reply_markup)
    if voice_text:
        await voice_an(bot, chat_id, voice_text, empfaenger_rolle=rolle)


async def send_domina(bot: Bot, text: str, parse_mode: str | None = None, reply_markup=None,
                      voice_text: str | None = None) -> None:
    """Kontext-Wrapper: sendet an die Dom-Seite des Paares im aktiven
    Paar-Kontext (Updates: TypeHandler in main.py; Scheduler: _pro_paar).
    Damit sind die ~33 nutzenden Module automatisch paar-korrekt."""
    from bot.services import paare  # lazy: zirkelfreier Modul-Import
    await send_an(bot, paare.paar_im_kontext(), paare.ROLLE_DOM, text, parse_mode,
                  reply_markup, voice_text=voice_text)


async def send_sklave(bot: Bot, text: str, parse_mode: str | None = None, reply_markup=None,
                      voice_text: str | None = None) -> None:
    """Kontext-Wrapper: sendet an die Sub-Seite des Paares im aktiven Paar-Kontext."""
    from bot.services import paare
    await send_an(bot, paare.paar_im_kontext(), paare.ROLLE_SUB, text, parse_mode,
                  reply_markup, voice_text=voice_text)


async def voice_an(bot: Bot, chat_id, text: str, empfaenger_rolle: str | None = None) -> bool:
    """Spricht `text` als Voice-Message an chat_id (Grok-TTS bzw. Piper).
    Still no-op wenn TTS aus ist; True nur bei erfolgreichem Versand.
    `empfaenger_rolle` (paare.ROLLE_*) wählt die Grok-Stimme: die Dom-Seite
    hört den Coach, die Sub-Seite die Herrin (Default)."""
    from bot.services import paare, tts  # lazy: kein Import-Gewicht wenn TTS aus
    stimme = tts.ROLLE_COACH if empfaenger_rolle == paare.ROLLE_DOM else tts.ROLLE_HERRIN
    try:
        ogg = await tts.synthesize(text, rolle=stimme)
        if not ogg:
            return False
        await bot.send_voice(chat_id=chat_id, voice=ogg)
        return True
    except Exception:
        logger.exception("Voice-Versand fehlgeschlagen (Text war schon zugestellt)")
        return False


async def reply_markdown_safe(message, text: str, reply_markup=None) -> None:
    """Sendet via message.reply_text mit parse_mode='Markdown'. Bei Parse-Fehler
    fallback auf Plain-Text mit strip_md (reply_markup bleibt erhalten).

    Verwende dies wenn der Inhalt vom LLM/User kommt und Markdown enthalten kann.
    """
    try:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except BadRequest as e:
        if "parse" in str(e).lower():
            logger.warning("Markdown-Parse fehlgeschlagen, sende strip_md: %s", e)
            await message.reply_text(strip_md(text), reply_markup=reply_markup)
        else:
            raise
