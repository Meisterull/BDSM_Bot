"""
Wunsch Handler – Sklave kann Wünsche einreichen.
"""
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.handlers import entscheidung_flow
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    state.set_mode(chat_id, "wunsch_eingabe")

    await update.message.reply_text(t("WUNSCH_EINREICHEN"), parse_mode="Markdown")


def _wunsch_buttons(wuensche: list) -> InlineKeyboardMarkup | None:
    """Pro Wunsch ein Lösch-Knopf (umgeht Command-mit-Argument-Probleme)."""
    if not wuensche:
        return None
    rows = [[InlineKeyboardButton(f"🗑 {i + 1}. {w[:28]}", callback_data=f"wunschdel:{i}")]
            for i, w in enumerate(wuensche)]
    rows.append([InlineKeyboardButton(t("BUTTON_ALLE_LOESCHEN"), callback_data="wunschdel:alle")])
    return InlineKeyboardMarkup(rows)


def _wunsch_liste_text(wuensche: list) -> str:
    liste = "\n".join(f"{i + 1}. {w}" for i, w in enumerate(wuensche))
    return t("WUNSCH_LISTE", liste=liste)


async def meine_wuensche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/meinewuensche – Sklave sieht & verwaltet seine im Chat erfassten Wünsche
    (nur er, nicht die Domina). Löschen per Button."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return
    prof = await qdrant.get_user_profile("sklave") or {}
    wuensche = prof.get("entdeckte_wuensche", []) or []
    if not wuensche:
        await update.message.reply_text(t("WUNSCH_KEINE_GESAMMELT"))
        return
    await update.message.reply_text(
        _wunsch_liste_text(wuensche), parse_mode="Markdown",
        reply_markup=_wunsch_buttons(wuensche),
    )


async def callback_loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🗑-Button: einen Wunsch oder alle löschen, dann Liste aktualisieren."""
    query = update.callback_query
    await query.answer()
    if str(query.message.chat_id) != paare.sub_chat_id():
        return
    _, was = query.data.split(":", 1)
    prof = await qdrant.get_user_profile("sklave") or {}
    wuensche = prof.get("entdeckte_wuensche", []) or []

    if was == "alle":
        await qdrant.patch_profile_fields("sklave", {"entdeckte_wuensche": []})
        await query.edit_message_text(t("WUNSCH_ALLE_GELOESCHT"))
        return
    try:
        i = int(was)
    except ValueError:
        return
    if not (0 <= i < len(wuensche)):
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(t("WUNSCH_EINTRAG_WEG"))
        return
    wuensche.pop(i)
    await qdrant.patch_profile_fields("sklave", {"entdeckte_wuensche": wuensche})
    if wuensche:
        await query.edit_message_text(
            _wunsch_liste_text(wuensche), parse_mode="Markdown",
            reply_markup=_wunsch_buttons(wuensche),
        )
    else:
        await query.edit_message_text(t("WUNSCH_LISTE_LEER"))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet den eingegebenen Wunsch des Sklaven."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    # Getipptes "abbrechen" nicht als Wunsch speichern und an die Domina schicken.
    if text.lower() in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    # Wunsch in Qdrant speichern
    wunsch_id = await qdrant.save_wunsch(
        user_id="sklave",
        data={
            "text": text,
            "datum": datetime.now(timezone.utc).isoformat(),
            "status": "eingereicht",
        },
    )

    # State aufräumen
    state.set_mode(chat_id, "chat")

    # Bestätigung an Sklave – in der Stimme der Herrin, nicht bürokratisch
    await update.message.reply_text(t("WUNSCH_ANGEKOMMEN"))

    # Domina-State für Entscheidung setzen. Die Inline-Buttons werden IMMER mitgesendet –
    # ihr Callback ist modus-unabhängig (liest die wunsch_id aus den Callback-Daten).
    # Den Text-Mode 'wunsch_entscheidung' setzen wir nur, wenn die Domina frei ist, um
    # einen laufenden Flow nicht zu überschreiben.
    domina_s = state.get(paare.dom_chat_id())
    domina_s["wunsch_id"] = wunsch_id
    domina_mode = state.get_mode(paare.dom_chat_id())

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ANNEHMEN"), callback_data=f"wunsch:annehmen:{wunsch_id}"),
        InlineKeyboardButton(t("BUTTON_ABLEHNEN"), callback_data=f"wunsch:ablehnen:{wunsch_id}"),
    ]])

    if domina_mode == "chat":
        state.set_mode(paare.dom_chat_id(), "wunsch_entscheidung")
        nachricht = t("WUNSCH_AN_DOMINA", text=text)
    else:
        nachricht = t("WUNSCH_AN_DOMINA_WARTEND", text=text)

    # send_domina: Markdown-Fallback erhält die Buttons – kein Verlust bei BadRequest.
    await telegram_helper.send_domina(
        context.bot, nachricht, parse_mode="Markdown", reply_markup=keyboard,
    )


async def callback_entscheidung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button: schnelle Entscheidung ohne Kommentar."""
    query = update.callback_query
    await query.answer()
    _, action, wunsch_id = query.data.split(":", 2)

    entscheidung = "angenommen" if action == "annehmen" else "abgelehnt"
    await _entscheidung_speichern(context, wunsch_id, entscheidung, kommentar="")

    # Mode nur zurücksetzen, wenn er noch UNSERER ist – ein später Tap auf einen
    # alten Button darf keinen gerade aktiven anderen Flow killen.
    if state.get_mode(paare.dom_chat_id()) == "wunsch_entscheidung":
        state.set_mode(paare.dom_chat_id(), "chat")
    state.get(paare.dom_chat_id()).pop("wunsch_id", None)

    emoji = "✅" if entscheidung == "angenommen" else "❌"
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(t("WUNSCH_ENTSCHIEDEN", emoji=emoji, entscheidung=entscheidung))


def _parse_entscheidung(text: str) -> tuple[str, str] | None:
    """Erkennt annehmen/ablehnen (+ optionalen Kommentar) in der Domina-Antwort."""
    text_lower = text.lower()
    if text_lower.startswith("annehmen"):
        return "angenommen", text[len("annehmen"):].strip()
    if text_lower.startswith("ablehnen"):
        return "abgelehnt", text[len("ablehnen"):].strip()
    return None


async def handle_entscheidung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Entscheidung der Domina zu einem eingereichten Wunsch."""
    await entscheidung_flow.handle_entscheidung(
        update, context,
        state_key="wunsch_id",
        parse_entscheidung=_parse_entscheidung,
        hinweis_text=t("WUNSCH_ENTSCHEIDUNG_HINWEIS"),
        bestaetigung_text=lambda entscheidung: t("WUNSCH_ENTSCHEIDUNG_GESPEICHERT", entscheidung=entscheidung),
        persistiere=_entscheidung_speichern,
    )


async def _entscheidung_speichern(context, wunsch_id: str, entscheidung: str, kommentar: str) -> None:
    """Persistiert Entscheidung und sendet KI-Antwort an den Sklaven."""
    await qdrant.update_wunsch(wunsch_id, {
        "status": entscheidung,
        "domina_kommentar": kommentar,
        "entschieden_am": datetime.now(timezone.utc).isoformat(),
    })

    wunsch_data = await qdrant.get_wunsch(wunsch_id) or {}
    wunsch_text = wunsch_data.get("text", "")

    from bot.prompts import persona, followup as fp
    if entscheidung == "angenommen":
        system = (
            f"Du bist die Herrin. Dein Sklave hat einen Wunsch eingereicht und du hast ihn angenommen. "
            f"Reagiere direkt an ihn – ein bis drei Sätze, Ich-Form.\n\n"
            f"{persona.fuer_sklaven_prompt()}\n\n"
            f"Mach in deinen Worten klar, unter welchen Bedingungen das gilt oder wofür er das bekommt. Keine generische Floskel.\n"
            f"Kein [AUFGABE: ...] Tag."
        )
    else:
        system = (
            f"Du bist die Herrin. Dein Sklave hat einen Wunsch eingereicht und du hast ihn abgelehnt. "
            f"Reagiere direkt an ihn – ein bis drei Sätze, Ich-Form.\n\n"
            f"{persona.fuer_sklaven_prompt()}\n\n"
            f"Eine ehrliche, knappe Absage. Nicht mitleidig, aber auch nicht hart-floskelhaft. Du entscheidest, das ist klar – aber ohne Bot-Spruch.\n"
            f"Kein [AUFGABE: ...] Tag."
        )
    user = fp.nutzer_text("Sein Wunsch", wunsch_text)
    if kommentar:
        user += f"\nDein Kommentar zur Entscheidung: {kommentar}"

    try:
        nachricht = await grok.simple(user, system=system)
        await telegram_helper.send_sklave(context.bot, nachricht)
    except Exception as e:
        logger.error("Fehler bei Wunsch-Benachrichtigung: %s", e)
        fallback = (t("FALLBACK_WUNSCH_ANGENOMMEN") if entscheidung == "angenommen"
                    else t("FALLBACK_WUNSCH_ABGELEHNT"))
        await telegram_helper.send_sklave(context.bot, fallback)
