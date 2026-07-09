"""
Würfel Handler – generiert eine zufällige Surprise-Aufgabe.

Ignoriert Rotation und Score-Anpassung. Reines Risiko-Element.
Nach Generierung kann die Domina per Inline-Button die Aufgabe
direkt als echten Task an den Sklaven erteilen.
"""
import asyncio
import logging
import random
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check, kategorie_logik
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wuerfel – würfelt eine zufällige Kategorie und generiert eine Surprise-Aufgabe."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}
    hard_limits = sklave_profile.get("hard_limits", []) or []
    domina_grenzen = domina_profile.get("grenzen", []) or []

    # Pool (Katalog + eigene Kategorien); Dislike-Filter: Kategorien ausschließen,
    # die der Sklave wiederholt ablehnt
    pool = kategorie_logik.alle_kategorien(sklave_profile)
    dislike_kategorien = kategorie_logik.dislike_kategorien(sklave_profile)
    verfuegbar = [k for k in pool if k not in dislike_kategorien]
    if not verfuegbar:
        verfuegbar = pool

    # Echter Telegram-Würfel 🎲: 6 Kandidaten auslosen, der animierte Wurf
    # entscheidet. Der Wert steht sofort in der API-Antwort – die Auflösung
    # wartet auf das Ende der Animation (~4s), sonst spoilert der Text.
    kandidaten = random.sample(verfuegbar, k=min(6, len(verfuegbar)))
    try:
        dice_msg = await update.message.reply_dice(emoji="🎲")
        wert = dice_msg.dice.value  # 1-6
        await asyncio.sleep(4)
        kategorie = kandidaten[(wert - 1) % len(kandidaten)]
        await update.message.reply_text(
            t("WUERFEL_GEFALLEN_WURF", wert=wert,
              kategorie=kategorie_logik.anzeige_name(kategorie)), parse_mode="Markdown",
        )
    except Exception as e:
        # Dice nicht verfügbar (z.B. API-Fehler) → altes Verhalten ohne Animation
        logger.warning("reply_dice fehlgeschlagen, nutze Zufall ohne Animation: %s", e)
        kategorie = random.choice(verfuegbar)
        await update.message.reply_text(
            t("WUERFEL_GEFALLEN", kategorie=kategorie_logik.anzeige_name(kategorie)), parse_mode="Markdown",
        )

    from bot.prompts import coach_persona
    system = (
        f"Du würfelst spontan eine überraschende Aufgabe für ihren Sklaven aus.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Die Aufgabe soll:\n"
        f"- Direkt umsetzbar sein (15-30 Minuten)\n"
        f"- Etwas Risiko / Kribbeln haben (Würfel-Charakter)\n"
        f"- Aus der vorgegebenen Kategorie stammen\n"
        f"- Zu IHM passen – nicht generisch für irgendeinen Sklaven\n\n"
        f"Antworte NUR mit dem reinen Aufgaben-Text (1-3 Sätze), keine Einleitung, kein Markdown, keine Anführungszeichen."
    )
    prompt = (
        f"Pflicht-Kategorie: {kategorie}\n"
        f"{coach_persona.sklaven_kontext_block(sklave_profile, domina_grenzen)}"
    )
    skill_block = await coach_persona.skill_kontext_block([kategorie])
    if skill_block:
        prompt += "\n\n" + skill_block

    try:
        aufgabe_text = grok.clean_text(await grok.simple(prompt, system=system))

        # Limits-Check (beide Profile)
        treffer = await limits_check.verletzungen(aufgabe_text, hard_limits, domina_grenzen)
        if treffer:
            # Nur Anzahl/Quelle ins Log – die konkreten Limit-Begriffe (intim) gehören
            # nicht auf WARNING (Logserver!), nur in die Nachricht an die Domina.
            _quellen = sorted({tr["quelle"] for tr in treffer})
            logger.warning("Würfel-Aufgabe verletzt %d Grenze(n) [%s] – verworfen.",
                           len(treffer), ", ".join(_quellen))
            logger.debug("Würfel-Verletzungen: %s", limits_check.format_verletzungen(treffer))
            await update.message.reply_text(
                t("WUERFEL_GRENZEN", treffer=limits_check.format_verletzungen(treffer))
            )
            return

        s = state.get(chat_id)
        s["wuerfel_kategorie"] = kategorie
        s["wuerfel_aufgabe"] = aufgabe_text
        # Nonce in die Callback-Daten: ein liegengebliebener Button eines
        # FRÜHEREN Wurfs darf nicht den neuesten State-Inhalt erteilen.
        nonce = uuid.uuid4().hex[:8]
        s["wuerfel_nonce"] = nonce

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ALS_TASK_ERTEILEN"), callback_data=f"wuerfel:erteilen:{nonce}"),
            InlineKeyboardButton(t("BUTTON_VERWERFEN"), callback_data=f"wuerfel:verwerfen:{nonce}"),
        ]])
        await telegram_helper.send_domina(
            context.bot,
            t("WUERFEL_VORSCHLAG", kategorie=kategorie_logik.anzeige_name(kategorie), aufgabe=aufgabe_text),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        logger.info("Würfel-Aufgabe generiert (Kategorie: %s)", kategorie)
    except Exception as e:
        logger.error("Fehler bei Würfel-Aufgabe: %s", e)
        await update.message.reply_text(t("WUERFEL_FEHLER"))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button: Würfel-Aufgabe erteilen oder verwerfen."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    nonce = parts[2] if len(parts) > 2 else ""

    s = state.get(paare.dom_chat_id())
    aufgabe_text = s.get("wuerfel_aufgabe", "")
    kategorie = s.get("wuerfel_kategorie", "allgemein")

    await query.edit_message_reply_markup(reply_markup=None)

    # Veralteter Button (anderer/kein Nonce): NICHT den neuesten State-Inhalt
    # erteilen – der Button zeigt eine andere Aufgabe als im Speicher liegt.
    if nonce != s.get("wuerfel_nonce"):
        await query.message.reply_text(t("WUERFEL_STATE_WEG"))
        return

    if action == "verwerfen":
        s.pop("wuerfel_kategorie", None)
        s.pop("wuerfel_aufgabe", None)
        s.pop("wuerfel_nonce", None)
        await query.message.reply_text(t("WUERFEL_VERWORFEN"))
        return

    if not aufgabe_text:
        await query.message.reply_text(t("WUERFEL_STATE_WEG"))
        return

    # Als echten Task speichern (gemeinsame Factory) – Level aus dem Profil
    # statt des früher hartkodierten 3.
    domina_profil = await qdrant.get_user_profile("domina") or {}
    level = domina_profil.get("aktuelles_level", 3)
    await qdrant.erstelle_task(aufgabe_text, kategorie, level, quelle="wuerfel")

    s.pop("wuerfel_kategorie", None)
    s.pop("wuerfel_aufgabe", None)

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe_text), max_tokens=250)
    except Exception as e:
        logger.error("aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
        anweisung = aufgabe_text
    await telegram_helper.send_sklave(context.bot, t("WUERFEL_BEFEHL_PREFIX", anweisung=anweisung),
                                      voice_text=anweisung)
    await query.message.reply_text(
        t("WUERFEL_ERTEILT", kategorie=kategorie_logik.anzeige_name(kategorie)), parse_mode="Markdown",
    )
    logger.info("Würfel-Aufgabe als echter Task gespeichert (Kategorie: %s)", kategorie)
