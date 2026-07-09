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
        await voice_an(bot, chat_id, voice_text)


async def send_domina(bot: Bot, text: str, parse_mode: str | None = None, reply_markup=None) -> None:
    """Kontext-Wrapper: sendet an die Dom-Seite des Paares im aktiven
    Paar-Kontext (Updates: TypeHandler in main.py; Scheduler: _pro_paar).
    Damit sind die ~33 nutzenden Module automatisch paar-korrekt."""
    from bot.services import paare  # lazy: zirkelfreier Modul-Import
    await send_an(bot, paare.paar_im_kontext(), paare.ROLLE_DOM, text, parse_mode, reply_markup)


async def send_sklave(bot: Bot, text: str, parse_mode: str | None = None, reply_markup=None,
                      voice_text: str | None = None) -> None:
    """Kontext-Wrapper: sendet an die Sub-Seite des Paares im aktiven Paar-Kontext."""
    from bot.services import paare
    await send_an(bot, paare.paar_im_kontext(), paare.ROLLE_SUB, text, parse_mode,
                  reply_markup, voice_text=voice_text)


async def voice_an(bot: Bot, chat_id, text: str) -> bool:
    """Spricht `text` als Voice-Message an chat_id (lokales Piper-TTS).
    Still no-op wenn TTS aus ist; True nur bei erfolgreichem Versand."""
    from bot.services import tts  # lazy: kein Import-Gewicht wenn TTS aus
    try:
        ogg = await tts.synthesize(text)
        if not ogg:
            return False
        await bot.send_voice(chat_id=chat_id, voice=ogg)
        return True
    except Exception:
        logger.exception("Voice-Versand fehlgeschlagen (Text war schon zugestellt)")
        return False


async def voice_an_sklaven(bot: Bot, text: str) -> bool:
    """Kontext-Wrapper: Voice an die Sub-Seite des Paares im aktiven Paar-Kontext."""
    from bot.services import paare
    return await voice_an(bot, paare.paar_im_kontext().sub_chat_id, text)


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
