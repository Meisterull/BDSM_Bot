"""
Strafen-Roulette 🎰 – die Slot-Machine entscheidet über Gnade oder Härte.

/roulette (Domina): echte Telegram-Slot-Machine-Animation; der Wert (1-64)
bestimmt den Schweregrad – 64 = Jackpot = GNADE (keine Strafe), darunter
mild/mittel/hart. Die generierte Strafe (limit-geprüft) geht wie beim Würfel
erst als Vorschau an die Domina (Erteilen/Verwerfen-Buttons, Nonce-gesichert);
bei Gnade kann sie die Begnadigung verkünden lassen.

Erteilte Strafen laufen als normaler Task (quelle='roulette') durch den
Followup-Zyklus.
"""
import asyncio
import logging
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check
from bot.services import sticker_reaktionen
from bot.prompts import followup as fp
from bot.prompts import persona, coach_persona
from bot.messages import t

logger = logging.getLogger(__name__)

# Wert 1-64 → Schweregrad. 64 = Jackpot (drei Siebener) = Gnade.
_STUFEN = (
    (64, "gnade", None),
    (43, "mild", "eine kleine, eher symbolische Bestrafung (5-15 Minuten) – der Denkzettel zählt, nicht die Härte"),
    (22, "mittel", "eine spürbare Bestrafung (15-30 Minuten) – unbequem genug, dass er sie nicht vergisst"),
    (1, "hart", "eine ernsthafte, fordernde Bestrafung (30-60 Minuten oder entsprechend unangenehm) – heute kennt die Maschine keine Milde"),
)


def _stufe(wert: int) -> tuple[str, str | None]:
    for schwelle, name, anweisung in _STUFEN:
        if wert >= schwelle:
            return name, anweisung
    return "hart", _STUFEN[-1][2]


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/roulette – Slot-Machine drehen, Strafe nach Schweregrad generieren."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    try:
        dice_msg = await update.message.reply_dice(emoji="🎰")
        wert = dice_msg.dice.value  # 1-64, 64 = Jackpot
        await asyncio.sleep(3)      # Animation ausrollen lassen
    except Exception as e:
        logger.warning("Slot-Machine fehlgeschlagen, nutze Zufall: %s", e)
        import random
        wert = random.randint(1, 64)

    stufe, anweisung = _stufe(wert)
    s = state.get(chat_id)
    nonce = uuid.uuid4().hex[:8]
    s["roulette_nonce"] = nonce

    if stufe == "gnade":
        s["roulette_stufe"] = "gnade"
        s.pop("roulette_strafe", None)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ROULETTE_GNADE"), callback_data=f"roulette:gnade:{nonce}"),
            InlineKeyboardButton(t("BUTTON_VERWERFEN"), callback_data=f"roulette:verwerfen:{nonce}"),
        ]])
        await update.message.reply_text(
            t("ROULETTE_JACKPOT"), parse_mode="Markdown", reply_markup=keyboard)
        return

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sk_hl = sklave_profile.get("hard_limits", []) or []
    do_gr = domina_profile.get("grenzen", []) or []

    system = (
        f"Die Slot-Machine hat über eine Bestrafung für ihren Sklaven entschieden: "
        f"Schweregrad {stufe.upper()}.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Erzeuge {anweisung}.\n"
        f"Sie muss konkret zu IHM passen – kein generischer 101-Task.\n\n"
        f"Antworte NUR mit dem reinen Strafen-Text (1-3 Sätze), keine Einleitung, "
        f"kein Markdown, keine Anführungszeichen."
    )
    prompt = coach_persona.sklaven_kontext_block(sklave_profile, do_gr)

    try:
        strafe = await limits_check.generate_mit_limit_retry(prompt, sk_hl, do_gr, system=system)
        if not strafe:
            await update.message.reply_text(t("ROULETTE_FEHLER"))
            return
        strafe = grok.clean_text(strafe)
    except Exception:
        logger.exception("Roulette-Strafe fehlgeschlagen")
        await update.message.reply_text(t("ROULETTE_FEHLER"))
        return

    s["roulette_stufe"] = stufe
    s["roulette_strafe"] = strafe
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ALS_TASK_ERTEILEN"), callback_data=f"roulette:erteilen:{nonce}"),
        InlineKeyboardButton(t("BUTTON_VERWERFEN"), callback_data=f"roulette:verwerfen:{nonce}"),
    ]])
    await telegram_helper.send_domina(
        context.bot,
        t("ROULETTE_VORSCHLAG", stufe=t(f"ROULETTE_STUFE_{stufe.upper()}"), strafe=strafe),
        parse_mode="Markdown", reply_markup=keyboard,
    )
    logger.info("Roulette: Wert %d → %s", wert, stufe)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Buttons: Strafe erteilen, Gnade verkünden oder verwerfen."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    nonce = parts[2] if len(parts) > 2 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    s = state.get(paare.dom_chat_id())
    if nonce != s.get("roulette_nonce"):
        await query.message.reply_text(t("ROULETTE_STATE_WEG"))
        return

    strafe = s.get("roulette_strafe", "")
    stufe = s.get("roulette_stufe", "")
    for k in ("roulette_nonce", "roulette_strafe", "roulette_stufe"):
        s.pop(k, None)

    if action == "verwerfen":
        await query.message.reply_text(t("ROULETTE_VERWORFEN"))
        return

    if action == "gnade":
        try:
            system = (
                "Du sprichst direkt mit ihm. Die Slot-Machine hat über eine mögliche "
                "Bestrafung entschieden – und den Jackpot gezogen: GNADE. Verkünde ihm "
                "das in ein bis zwei Sätzen: großzügig, aber mit dem Unterton, dass es "
                "reines Glück war und die Maschine beim nächsten Mal anders entscheidet. "
                "Kein Markdown.\n\n" + persona.fuer_sklaven_prompt()
            )
            meldung = grok.clean_text(await grok.simple("Die Maschine zeigt: Jackpot – Gnade.",
                                                        system=system, max_tokens=150))
        except Exception:
            logger.exception("Gnade-Verkündung fehlgeschlagen – Fallback")
            meldung = ""
        # Gnaden-Sticker vor der Verkündung
        await sticker_reaktionen.sende_sklave(context.bot, sticker_reaktionen.GNADE)
        await telegram_helper.send_sklave(
            context.bot, meldung or t("ROULETTE_GNADE_FALLBACK"), voice_text=meldung or None)
        await query.message.reply_text(t("ROULETTE_GNADE_VERKUENDET"))
        return

    # erteilen
    if not strafe:
        await query.message.reply_text(t("ROULETTE_STATE_WEG"))
        return
    domina_profil = await qdrant.get_user_profile("domina") or {}
    level = domina_profil.get("aktuelles_level", 3)
    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(strafe), max_tokens=250)
    except Exception:
        logger.exception("aufgabe_an_sklaven (Roulette) fehlgeschlagen – Rohtext")
        anweisung = strafe
    point_id = await qdrant.erstelle_task(strafe, "allgemein", level, quelle="roulette")
    # Schicksals-Sticker: die Maschine hat entschieden
    await sticker_reaktionen.sende_sklave(context.bot, sticker_reaktionen.SCHICKSAL)
    try:
        await telegram_helper.send_sklave(
            context.bot, t("ROULETTE_AN_SKLAVEN", anweisung=anweisung),
            parse_mode="Markdown", voice_text=anweisung)
    except Exception:
        # Rollback (D9/N5, Muster blitz): nie zugestellte Strafe nicht als
        # offenen Task zurücklassen.
        await qdrant.loesche_task(point_id)
        raise
    await query.message.reply_text(t("ROULETTE_ERTEILT"))
    logger.info("Roulette-Strafe erteilt (Stufe %s)", stufe)
