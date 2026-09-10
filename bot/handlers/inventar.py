"""
Inventar 🧰 – /inventar für BEIDE Rollen: Spielsachen und Hilfsmittel pflegen,
die im Haushalt wirklich da sind, plus Wunschliste (Anschaffungen).

  * /inventar                  – beide Listen anzeigen, danach Eingabe-Modus
  * /inventar + Gerte (hart)   – direkt eintragen (alle Formen auch als Argument)
  * im Eingabe-Modus:
      + Text            → vorhanden eintragen
      + wunsch: Text    → Wunschliste
      - 3  /  - w1      → entfernen (Nummer aus der Anzeige)
      da w1             → Wunsch erfüllt → nach „vorhanden“
      fertig / /abbrechen → Modus beenden

Die Listen wirken als Wissen + Verbot in allen Aufgaben-Generatoren und beiden
Chats (coach_persona.inventar_block) und als gelegentlicher Ausstattungs-Impuls
(services/inventar.impuls_wahl). Bewusst KEINE Nachricht an die andere Seite bei
Änderungen – der Coach kennt die Listen und erwähnt Neues nur im Gespräch.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import inventar, paare, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)

MODE = "inventar_eingabe"
_FERTIG_WORTE = {"fertig", "ok", "okay", "passt", "done", "ende", "abbrechen", "/abbrechen"}


async def _liste_senden(message) -> None:
    v, w = inventar.anzeige(inventar.vorhanden(), inventar.wuensche())
    esc = telegram_helper.escape_md
    await message.reply_text(
        t("INVENTAR_ANZEIGE", vorhanden=esc(v), wuensche=esc(w)),
        parse_mode="MarkdownV2",
    )


async def _ausfuehren(message, op: tuple[str, str]) -> bool:
    """Führt eine geparste Eingabe aus und bestätigt. True, wenn sich die
    Nummerierung geändert hat (dann die Liste frisch zeigen)."""
    art, nutz = op
    if art in ("add", "add_wunsch"):
        wunsch = art == "add_wunsch"
        ok, grund = await inventar.hinzufuegen(nutz, wunsch=wunsch)
        if ok:
            key = "INVENTAR_WUNSCH_HINZUGEFUEGT" if wunsch else "INVENTAR_HINZUGEFUEGT"
            await message.reply_text(t(key, eintrag=nutz[:inventar.MAX_LAENGE]))
            # Ein neu vorhandener Gegenstand räumt einen gleichnamigen Wunsch ab –
            # dann verschieben sich die w-Nummern.
            return not wunsch
        if grund == "doppelt":
            await message.reply_text(t("INVENTAR_DOPPELT"))
        elif grund == "voll":
            await message.reply_text(
                t("INVENTAR_VOLL", max=inventar.MAX_WUNSCH if wunsch else inventar.MAX_VORHANDEN))
        else:
            await message.reply_text(t("INVENTAR_UNVERSTANDEN"), parse_mode="Markdown")
        return False
    if art == "del":
        eintrag = await inventar.entfernen(nutz)
        if eintrag is None:
            await message.reply_text(t("INVENTAR_UNBEKANNTE_NUMMER"))
            return False
        await message.reply_text(t("INVENTAR_ENTFERNT", eintrag=eintrag))
        return True
    if art == "da":
        eintrag, grund = await inventar.angeschafft(nutz)
        if eintrag:
            await message.reply_text(t("INVENTAR_ANGESCHAFFT", eintrag=eintrag))
            return True
        if grund == "kein_wunsch":
            await message.reply_text(t("INVENTAR_KEIN_WUNSCH"))
        elif grund == "voll":
            await message.reply_text(t("INVENTAR_VOLL", max=inventar.MAX_VORHANDEN))
        else:
            await message.reply_text(t("INVENTAR_UNBEKANNTE_NUMMER"))
        return False
    await message.reply_text(t("INVENTAR_UNVERSTANDEN"), parse_mode="Markdown")
    return False


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/inventar [Eingabe] – beide Rollen, gleiche Paar-Listen."""
    chat_id = str(update.effective_chat.id)
    if paare.resolve(chat_id) is None:
        return
    arg = " ".join(context.args).strip() if context.args else ""
    if arg:
        op = inventar.parse_eingabe(arg)
        if op is None:
            await update.message.reply_text(t("INVENTAR_UNVERSTANDEN"), parse_mode="Markdown")
            return
        nummern_neu = await _ausfuehren(update.message, op)
        if nummern_neu:
            await _liste_senden(update.message)
        return
    await _liste_senden(update.message)
    state.set_mode(chat_id, MODE)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Eingabe-Modus nach /inventar (Router: mode == inventar_eingabe, beide Rollen)."""
    chat_id = str(update.effective_chat.id)
    text = (update.message.text or "").strip()
    if text.lower() in _FERTIG_WORTE:
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("INVENTAR_FERTIG"))
        return
    op = inventar.parse_eingabe(text)
    if op is None:
        await update.message.reply_text(t("INVENTAR_UNVERSTANDEN"), parse_mode="Markdown")
        state.touch_mode(chat_id)
        return
    nummern_neu = await _ausfuehren(update.message, op)
    if nummern_neu:
        await _liste_senden(update.message)
    state.touch_mode(chat_id)
