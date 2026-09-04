"""
/wette – „Doppelt oder nichts": der Sklave setzt Punkte darauf, dass er seine
nächste fällige Aufgabe schafft.

Der Einsatz wird beim Setzen abgezogen (Profil-Feld `wette`). Aufgelöst wird
beim nächsten Task-Ausgang: erledigt → punkte.task_erledigt schreibt den
doppelten Einsatz als Bonus gut; nicht erledigt → punkte.task_nicht_erledigt
verfällt den Einsatz. Maximal eine aktive Wette; nur mit offener Aufgabe setzbar
(sonst wäre es eine Wette auf nichts).
"""
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.services import sticker_reaktionen
from bot.prompts import persona
from bot.messages import t

logger = logging.getLogger(__name__)

EINSAETZE = (10, 25, 50)


async def _angebots_lage(profil: dict) -> str:
    """Prüft, ob dem Sklaven eine Wette angeboten werden kann.
    'ok' | 'aktiv' (Wette läuft schon) | 'keine_aufgabe' | 'zu_wenig' (Punkte).
    Blitzaufgaben zählen nicht als wettbare Aufgabe (Review D8/M10): sie sind
    aus der Wett-Auflösung in punkte.task_erledigt/task_nicht_erledigt bewusst
    ausgenommen – besteht das "offen" nur aus einem Blitz, hinge der Einsatz
    sonst fest, bis irgendwann ein regulärer Task ausgeht."""
    if (profil.get("wette") or {}).get("einsatz"):
        return "aktiv"
    offene = await qdrant.get_tasks_by_status(["offen", "gefragt"], limit=20)
    if not any(task.get("quelle") != "blitz" for task in offene):
        return "keine_aufgabe"
    if not any(e <= profil.get("punkte", 0) for e in EINSAETZE):
        return "zu_wenig"
    return "ok"


def _angebot_bauen(chat_id: str, punkte: int) -> tuple[str, InlineKeyboardMarkup]:
    """Angebots-Text + Einsatz-Buttons. Nonce wie beim Würfel: ein liegen-
    gebliebener Button darf später nicht unbemerkt eine neue Wette platzieren."""
    nonce = uuid.uuid4().hex[:8]
    state.get(chat_id)["wette_nonce"] = nonce
    moeglich = [e for e in EINSAETZE if e <= punkte]
    # Locale-Key statt Hardcoding (D9/A4) – EN-Betrieb zeigte deutsche Buttons.
    buttons = [[InlineKeyboardButton(t("BUTTON_WETTE_EINSATZ", punkte=e),
                                     callback_data=f"wette:setzen:{e}:{nonce}")
                for e in moeglich]]
    return t("WETTE_ANGEBOT", punkte=punkte), InlineKeyboardMarkup(buttons)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wette – Einsatz-Auswahl per Inline-Buttons."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    profil = await qdrant.get_user_profile("sklave") or {}
    punkte = profil.get("punkte", 0)
    lage = await _angebots_lage(profil)
    if lage == "aktiv":
        await update.message.reply_text(
            t("WETTE_SCHON_AKTIV", einsatz=profil["wette"]["einsatz"]), parse_mode="Markdown")
        return
    if lage == "keine_aufgabe":
        await update.message.reply_text(t("WETTE_KEINE_AUFGABE"))
        return
    if lage == "zu_wenig":
        await update.message.reply_text(
            t("WETTE_ZU_WENIG_PUNKTE", punkte=punkte, minimum=EINSAETZE[0]))
        return

    text, markup = _angebot_bauen(chat_id, punkte)
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=markup,
    )


async def angebot_moeglich(profil: dict) -> bool:
    """Für den Spiel-Impuls: kann die Herrin gerade eine Wette anbieten?"""
    return await _angebots_lage(profil) == "ok"


async def sende_spontanes_angebot(bot) -> bool:
    """Spiel-Impuls 🎲 (scheduler.spiel_impuls_job): die Herrin bietet UNGEFRAGT
    "Doppelt oder nichts" an – gleiche Buttons/Callbacks wie /wette. Lage wird
    frisch geprüft (zwischen Job-Check und Send kann sich das Profil ändern).
    True nur bei Versand."""
    chat_id = paare.sub_chat_id()
    profil = await qdrant.get_user_profile("sklave") or {}
    if await _angebots_lage(profil) != "ok":
        return False
    text, markup = _angebot_bauen(chat_id, profil.get("punkte", 0))
    # Schicksals-Sticker als Auftakt (Würfel/Roulette/Wette) – best-effort intern
    await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.SCHICKSAL)
    await telegram_helper.send_sklave(
        bot, t("SPIEL_IMPULS_WETTE") + "\n\n" + text,
        parse_mode="Markdown", reply_markup=markup)
    return True


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button: Wette platzieren."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    einsatz = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    nonce = parts[3] if len(parts) > 3 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    s = state.get(paare.sub_chat_id())
    if nonce != s.get("wette_nonce") or einsatz not in EINSAETZE:
        await query.message.reply_text(t("WETTE_STATE_WEG"))
        return
    s.pop("wette_nonce", None)

    # Frisch lesen – Punkte/Wette können sich seit dem Angebot geändert haben.
    profil = await qdrant.get_user_profile("sklave") or {}
    punkte = profil.get("punkte", 0)
    if (profil.get("wette") or {}).get("einsatz"):
        await query.message.reply_text(
            t("WETTE_SCHON_AKTIV", einsatz=profil["wette"]["einsatz"]), parse_mode="Markdown")
        return
    if punkte < einsatz:
        await query.message.reply_text(
            t("WETTE_ZU_WENIG_PUNKTE", punkte=punkte, minimum=einsatz))
        return

    await qdrant.patch_profile_fields("sklave", {
        "punkte": punkte - einsatz,
        "wette": {"einsatz": einsatz,
                  "gesetzt_am": datetime.now(timezone.utc).isoformat()},
    })
    logger.info("Wette platziert: %d Punkte", einsatz)

    # Die Herrin spielt es aus – best-effort, Fallback ist der nüchterne Text.
    try:
        system = (
            "Du sprichst direkt mit ihm. Er hat gerade Punkte darauf gewettet, "
            "dass er seine nächste Aufgabe schafft – Einsatz weg bei Versagen, "
            "das Doppelte zurück bei Erfolg. Reagiere in ein bis zwei Sätzen: "
            "amüsiert, provozierend, mit Lust auf beide Ausgänge. Keine Floskel, "
            "kein Markdown.\n\n" + persona.fuer_sklaven_prompt()
        )
        reaktion = grok.clean_text(await grok.simple(
            f"Sein Einsatz: {einsatz} Punkte.", system=system, max_tokens=150))
    except Exception as e:
        logger.error("Wett-Reaktion fehlgeschlagen, sende Fallback: %s", e)
        reaktion = ""
    await query.message.reply_text(
        t("WETTE_PLATZIERT", einsatz=einsatz, rest=punkte - einsatz), parse_mode="Markdown")
    if reaktion:
        await query.message.reply_text(reaktion)

    # Domina beiläufig informieren (best-effort)
    try:
        await telegram_helper.send_domina(
            context.bot, t("WETTE_INFO_DOMINA", einsatz=einsatz), parse_mode="Markdown")
    except Exception as e:
        logger.error("Wett-Info an Domina fehlgeschlagen: %s", e)
