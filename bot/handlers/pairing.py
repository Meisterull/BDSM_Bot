"""
Pairing-Flow (Multiuser-Abschluss): Registrierung neuer Paare via /start.

Nur aktiv, wenn config.PAIRING_ENABLED gesetzt ist (Default aus) – sonst
verhält sich der Bot gegenüber fremden Chats wie bisher (stumm bzw.
"Nicht autorisiert").

Ablauf:
  1. Unbekannter Chat sendet /start → Rollenwahl (1 = dominant, 2 = devot).
  2. Nach der Wahl entsteht ein Invite-Code (paare.erstelle_invite).
  3. Der Partner sendet /start und den Code (oder /start <code> als
     Deep-Link) → paare.loese_invite_ein registriert das Paar; wer einlöst,
     bekommt automatisch die Gegenrolle.
  4. Beide werden benachrichtigt, die Command-Menüs werden gesetzt. Das
     Onboarding startet wie gehabt automatisch mit der ersten Nachricht
     (onboarding.start_if_needed, pro Paar dank Mandanten-Grenze).
"""
import logging
import re

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import ContextTypes

from bot import commands_katalog, config, state
from bot.services import paare
from bot.messages import t

logger = logging.getLogger(__name__)

PAIRING_MODES = ("pairing_rolle", "pairing_warten")
_CODE_RE = re.compile(r"[A-Z2-9]{%d}" % paare.INVITE_CODE_LAENGE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start – Mitglieder bekommen einen Hinweis, Fremde den Pairing-Einstieg."""
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    if paare.resolve(chat_id) is not None:
        await update.message.reply_text(t("PAIRING_START_BEKANNT"))
        return
    if not config.PAIRING_ENABLED:
        return  # wie bisher: fremde Chats bleiben still (kein Setup-Leak)

    # Deep-Link: /start <code>
    args = context.args or []
    if args and await _versuche_code(update, context, args[0]):
        return

    state.set_mode(chat_id, "pairing_rolle")
    await update.message.reply_text(t("PAIRING_START_MENUE"))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text-Eingaben unbekannter Chats in den Pairing-Modes (Routing: main.py)."""
    chat_id = str(update.effective_chat.id)
    text = (update.message.text or "").strip()

    if await _versuche_code(update, context, text):
        return

    if state.get_mode(chat_id) == "pairing_rolle" and text in ("1", "2"):
        rolle = paare.ROLLE_DOM if text == "1" else paare.ROLLE_SUB
        try:
            code = paare.erstelle_invite(rolle, chat_id)
        except ValueError:
            logger.warning("Invite-Erstellung abgelehnt für Chat %s", chat_id)
            return
        state.set_mode(chat_id, "pairing_warten")
        await update.message.reply_text(
            t("PAIRING_CODE_ERSTELLT", code=code, stunden=config.INVITE_TTL_STUNDEN),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(t("PAIRING_ROLLE_UNGUELTIG"))


async def _versuche_code(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Versucht `text` als Invite-Code einzulösen. True = Eingabe wurde als
    Code behandelt (erfolgreich ODER mit Fehlermeldung beantwortet)."""
    kandidat = (text or "").strip().upper()
    if not _CODE_RE.fullmatch(kandidat):
        return False

    chat_id = str(update.effective_chat.id)
    paar = paare.loese_invite_ein(kandidat, chat_id)
    if paar is None:
        await update.message.reply_text(t("PAIRING_CODE_UNGUELTIG"))
        return True

    # Erfolg: Modes aufräumen, Menüs setzen, beide benachrichtigen.
    state.set_mode(paar.dom_chat_id, "chat")
    state.set_mode(paar.sub_chat_id, "chat")
    await _setze_menues(context, paar)
    # Tages-Jobs des neuen Paares sofort planen (sonst erst beim Neustart)
    try:
        from bot import main as main_mod  # lazy: main importiert diesen Handler
        main_mod.plane_zeit_jobs(context.bot, paar)
    except Exception:
        logger.exception("Pairing: Zeit-Jobs für Paar %s nicht planbar", paar.paar_id)
    await update.message.reply_text(t("PAIRING_ERFOLG"))
    partner_id = paar.partner_chat_id(chat_id)
    try:
        await context.bot.send_message(chat_id=partner_id, text=t("PAIRING_ERFOLG"))
    except Exception:
        logger.exception("Pairing: Partner %s nicht benachrichtigbar", partner_id)
    logger.info("Pairing erfolgreich: Paar %s", paar.paar_id)
    return True


async def _setze_menues(context: ContextTypes.DEFAULT_TYPE, paar: "paare.Paar") -> None:
    """Rollenspezifische Command-Menüs fürs Paar in DESSEN UI-Locale
    (best-effort; beim nächsten Neustart setzt post_init sie für alle Paare).
    Auch von /einstellungen nach einem Sprachwechsel aufgerufen."""
    try:
        with paare.kontext(paar.paar_id):
            dom_menue = [BotCommand(c, b) for c, b in commands_katalog.domina_menue()]
            sub_menue = [BotCommand(c, b) for c, b in commands_katalog.sklave_menue()]
        await context.bot.set_my_commands(
            dom_menue, scope=BotCommandScopeChat(chat_id=int(paar.dom_chat_id)))
        await context.bot.set_my_commands(
            sub_menue, scope=BotCommandScopeChat(chat_id=int(paar.sub_chat_id)))
    except Exception:
        logger.exception("Pairing: Command-Menüs für Paar %s nicht setzbar", paar.paar_id)
