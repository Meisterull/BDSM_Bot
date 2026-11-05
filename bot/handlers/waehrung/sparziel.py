"""
Wunschpreise und Sparziel.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
import uuid
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot import state
from bot.services import inventar, paare, qdrant, telegram_helper, waehrung
from bot.services import sticker_reaktionen
from bot.prompts import rollen
from bot.messages import t
from bot.handlers.waehrung import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Sparziel
# ---------------------------------------------------------------------------

def _preis_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{p} P", callback_data=f"wunschpreis:{p}:{kennung}")
        for p in waehrung.WUNSCH_PREISE
    ]])


async def frage_wunschpreis(wunsch: str) -> None:
    """Hook aus inventar.setze für jeden NEUEN Wunsch: Preis-Frage an die Dom-Seite."""
    if kern._BOT is None:
        return
    s = state.get(paare.dom_chat_id())
    offen = dict(s.get("wunschpreis_offen") or {})
    kennung = uuid.uuid4().hex[:8]
    offen[kennung] = wunsch
    if len(offen) > kern._OFFEN_MAX:
        for k in list(offen)[:-kern._OFFEN_MAX]:
            offen.pop(k, None)
    s["wunschpreis_offen"] = offen
    await telegram_helper.send_domina(
        kern._BOT, t("WUNSCHPREIS_FRAGE", wunsch=wunsch, sub_nom=rollen.sub()["label_nom"]),
        reply_markup=_preis_buttons(kennung))
    logger.info("Wunschpreis-Frage gesendet (%s).", kennung)


async def callback_wunschpreis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """200 / 300 / 500 unter der Wunsch-Frage (Rolle: Dom)."""
    query = update.callback_query
    await query.answer()
    try:
        _, preis_s, kennung = query.data.split(":", 2)
        preis = int(preis_s)
    except ValueError:
        return
    if preis not in waehrung.WUNSCH_PREISE:
        return
    s = state.get(str(update.effective_chat.id))
    offen = dict(s.get("wunschpreis_offen") or {})
    wunsch = offen.get(kennung)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if not wunsch:
        await query.message.reply_text(t("WUNSCHPREIS_VERALTET"))
        return
    kanon = await inventar.setze_preis(wunsch, preis)
    offen.pop(kennung, None)
    s["wunschpreis_offen"] = offen
    if not kanon:
        await query.message.reply_text(t("WUNSCHPREIS_WEG", wunsch=wunsch))
        return
    await query.message.reply_text(t("WUNSCHPREIS_GESETZT", wunsch=kanon, preis=preis))
    logger.info("Wunschpreis gesetzt: %d P (%s).", preis, kennung)
    profil = await qdrant.get_user_profile("sklave") or {}
    stand = int(profil.get("punkte", 0) or 0)
    ziel = inventar.sparziel()
    if ziel and ziel[0].lower() == kanon.lower():
        try:
            await telegram_helper.send_sklave(
                context.bot, t("SPARZIEL_NEU_SUB", wunsch=kanon, preis=preis, stand=min(stand, preis)))
        except Exception:
            logger.exception("Sparziel-Meldung an den Sub fehlgeschlagen")
    await sparziel_pruefen(context.bot, stand)


def _ziel_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_SPARZIEL_GEWAEHREN"), callback_data=f"wunschziel:gewaehren:{kennung}"),
        InlineKeyboardButton(t("BUTTON_SPARZIEL_SPAETER"), callback_data=f"wunschziel:spaeter:{kennung}"),
    ]])


async def sparziel_pruefen(bot, stand: int | None = None) -> bool:
    """Stand ≥ Preis des Sparziels → Ein-Tipp-Frage an die Dom-Seite. Gates:
    14 Tage Ruhe nach „noch nicht", höchstens eine Frage pro 24 h, keine
    zweite, solange eine offen ist. True nur bei gesendeter Frage."""
    ziel = inventar.sparziel()
    if not ziel:
        return False
    wunsch, preis = ziel
    if stand is None:
        profil = await qdrant.get_user_profile("sklave") or {}
        stand = int(profil.get("punkte", 0) or 0)
    if stand < preis:
        return False
    dom = await qdrant.get_user_profile("domina") or {}
    ruhe_bis = kern._parse((dom.get(kern.FELD_RUHE) or {}).get(wunsch.lower(), ""))
    if ruhe_bis and kern._jetzt() < ruhe_bis + timedelta(days=waehrung.SPARZIEL_RUHE_TAGE):
        return False
    letzte = kern._parse(dom.get(kern.FELD_FRAGE_AM, ""))
    if letzte and kern._jetzt() < letzte + timedelta(hours=24):
        return False
    s = state.get(paare.dom_chat_id())
    offen = s.get("sparziel_frage") or {}
    if offen.get("wunsch", "").lower() == wunsch.lower():
        return False
    kennung = uuid.uuid4().hex[:8]
    s["sparziel_frage"] = {"kennung": kennung, "wunsch": wunsch}
    await telegram_helper.send_domina(
        bot, t("SPARZIEL_ERREICHT", wunsch=wunsch, preis=preis, stand=stand,
               sub_nom=rollen.sub()["label_nom"]),
        reply_markup=_ziel_buttons(kennung))
    await qdrant.patch_profile_fields("domina", {kern.FELD_FRAGE_AM: kern._jetzt().isoformat()})
    logger.info("Sparziel erreicht – Frage an die Dom-Seite (%s).", kennung)
    return True


async def callback_wunschziel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """gewähren / noch nicht (Rolle: Dom)."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, kennung = query.data.split(":", 2)
    except ValueError:
        return
    if action not in ("gewaehren", "spaeter"):
        return
    s = state.get(str(update.effective_chat.id))
    offen = s.get("sparziel_frage") or {}
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if offen.get("kennung") != kennung:
        await query.message.reply_text(t("SPARZIEL_VERALTET"))
        return
    wunsch = offen.get("wunsch", "")
    s.pop("sparziel_frage", None)
    if action == "spaeter":
        dom = await qdrant.get_user_profile("domina") or {}
        ruhe = dict(dom.get(kern.FELD_RUHE) or {})
        ruhe[wunsch.lower()] = kern._jetzt().isoformat()
        await qdrant.patch_profile_fields("domina", {kern.FELD_RUHE: ruhe})
        await query.message.reply_text(t("SPARZIEL_SPAETER", tage=waehrung.SPARZIEL_RUHE_TAGE))
        logger.info("Sparziel vertagt (%s).", kennung)
        return
    preis = inventar.preis(wunsch)
    if preis is None:
        await query.message.reply_text(t("SPARZIEL_WEG", wunsch=wunsch))
        return
    profil = await qdrant.get_user_profile("sklave") or {}
    if int(profil.get("punkte", 0) or 0) < preis:
        await query.message.reply_text(
            t("SPARZIEL_ZU_WENIG", wunsch=wunsch, preis=preis, stand=profil.get("punkte", 0)))
        return
    buchung = await waehrung.buchen(-preis, f"Sparziel gewährt: {wunsch[:30]}")
    kanon = await inventar.gewaehren(wunsch) or wunsch
    await query.message.reply_text(t("SPARZIEL_GEWAEHRT_DOM", wunsch=kanon, preis=preis, stand=buchung["neu"]))
    logger.info("Sparziel gewährt: %d P abgebucht (%s).", preis, kennung)
    try:
        await sticker_reaktionen.sende_sklave(context.bot, sticker_reaktionen.GNADE)
    except Exception:
        pass
    try:
        await telegram_helper.send_sklave(
            context.bot, t("SPARZIEL_GEWAEHRT_SUB", wunsch=kanon, preis=preis, stand=buchung["neu"]))
    except Exception:
        logger.exception("Gewährt-Meldung an den Sub fehlgeschlagen")
    await kern.nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)
