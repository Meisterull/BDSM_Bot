"""
Herrin-Wette: Urteil, Einspruch, Abschluss.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot.services import qdrant, telegram_helper, waehrung
from bot.services import sticker_reaktionen
from bot.prompts import rollen
from bot.messages import t
from bot.handlers.waehrung import kern, wette_ablehnung, wette_annahme

logger = logging.getLogger(__name__)



def _urteil_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WETTE_GEWONNEN"), callback_data=f"wetteurteil:gewonnen:{kennung}"),
        InlineKeyboardButton(t("BUTTON_WETTE_VERLOREN"), callback_data=f"wetteurteil:verloren:{kennung}"),
    ]])


async def wette_urteil_job(bot) -> None:
    """Alle 15 Min (main, _pro_paar): angeboten → Erinnerung nach 4 h, nach 24 h
    angenommen; abgelehnt ohne Wahl der Dom-Seite → nach 24 h Default-Abzug;
    Frist um → Urteils-Frage an den Sub; 3 Tage ohne Meldung → verloren.
    Sub-Nachrichten der Automatik nur tagsüber (waehrung.TAGSUEBER)."""
    try:
        profil = await qdrant.get_user_profile("sklave") or {}
        w = profil.get(kern.FELD_WETTE) or {}
        if not w:
            return
        jetzt = kern._jetzt()
        if w.get("status") == "angeboten":
            angeboten = kern._parse(w.get("angeboten_am", ""))
            if not angeboten or not wette_annahme._tagsueber():
                return
            if jetzt >= angeboten + timedelta(hours=waehrung.WETT_ANNAHME_AUTO_STUNDEN):
                await wette_annahme._wette_annehmen(bot, dict(w), automatisch=True)
            elif not w.get("erinnert") and jetzt >= angeboten + timedelta(hours=waehrung.WETT_ANNAHME_ERINNERUNG_STUNDEN):
                rest = angeboten + timedelta(hours=waehrung.WETT_ANNAHME_AUTO_STUNDEN) - jetzt
                await telegram_helper.send_sklave(
                    bot, t("WETTE_ERINNERUNG_SUB", stunden=max(1, round(rest.total_seconds() / 3600))),
                    reply_markup=wette_annahme._annahme_buttons(w.get("kennung", "")))
                w = dict(w, erinnert=True)
                await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
                logger.info("Wett-Annahme erinnert (%s).", w.get("kennung"))
        elif w.get("status") == "abgelehnt":
            letzte = kern._parse(w.get("letzte_aktion_am") or w.get("abgelehnt_am", ""))
            if letzte and wette_annahme._tagsueber() and jetzt >= letzte + timedelta(hours=waehrung.WETT_ABLEHNUNG_AUTO_STUNDEN):
                await wette_ablehnung._ablehnung_automatisch_abschliessen(bot, dict(w))
        elif w.get("status") == "laeuft":
            frist = kern._parse(w.get("frist", ""))
            if frist and jetzt >= frist:
                await telegram_helper.send_sklave(
                    bot, t("WETTE_URTEIL_FRAGE", einsatz=w.get("einsatz", waehrung.WETT_EINSATZ)),
                    reply_markup=_urteil_buttons(w.get("kennung", "")))
                w.update({"status": "gefragt", "gefragt_am": jetzt.isoformat()})
                await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
                logger.info("Wett-Urteil erfragt (%s).", w.get("kennung"))
        elif w.get("status") == "gefragt":
            gefragt = kern._parse(w.get("gefragt_am", ""))
            if gefragt and jetzt >= gefragt + timedelta(days=waehrung.WETT_MELDEFRIST_TAGE):
                await _wette_abschliessen(bot, "verloren", auto=True)
    except Exception:
        logger.exception("wette_urteil_job fehlgeschlagen")


async def callback_wetteurteil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """gewonnen / verloren (Rolle: Sub)."""
    query = update.callback_query
    await query.answer()
    try:
        _, ergebnis, kennung = query.data.split(":", 2)
    except ValueError:
        return
    if ergebnis not in ("gewonnen", "verloren"):
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    profil = await qdrant.get_user_profile("sklave") or {}
    w = profil.get(kern.FELD_WETTE) or {}
    if w.get("kennung") != kennung or w.get("status") != "gefragt":
        await query.message.reply_text(t("WETTE_URTEIL_VERALTET"))
        return
    await _wette_abschliessen(context.bot, ergebnis)


async def _wette_abschliessen(bot, ergebnis: str, auto: bool = False) -> None:
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(kern.FELD_WETTE) or {})
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    delta = einsatz if ergebnis == "gewonnen" else -einsatz
    buchung = await waehrung.buchen(delta, f"Herrin-Wette {ergebnis}{' (verfallen)' if auto else ''}")
    jetzt = kern._jetzt()
    w.update({"status": "entschieden", "ergebnis": ergebnis, "entschieden_am": jetzt.isoformat(),
              "einspruch_bis": (jetzt + timedelta(hours=waehrung.EINSPRUCH_STUNDEN)).isoformat(),
              "verfallen": bool(auto)})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
    key = "WETTE_VERFALLEN_SUB" if auto else (
        "WETTE_GEWONNEN_SUB" if ergebnis == "gewonnen" else "WETTE_VERLOREN_SUB")
    try:
        await sticker_reaktionen.sende_sklave(
            bot, sticker_reaktionen.SCHICKSAL if ergebnis == "gewonnen" else sticker_reaktionen.SPOTT)
    except Exception:
        pass
    try:
        await telegram_helper.send_sklave(bot, t(key, einsatz=einsatz, stand=buchung["neu"]))
    except Exception:
        logger.exception("Wett-Ergebnis an den Sub fehlgeschlagen")
    kennung = w.get("kennung", "")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(
        t("BUTTON_WETTE_EINSPRUCH"), callback_data=f"wetteeinspruch:{kennung}")]])
    try:
        await telegram_helper.send_domina(
            bot, t("WETTE_ERGEBNIS_DOM", ergebnis=t("WETTE_WORT_" + ergebnis.upper()),
                   einsatz=einsatz, stand=buchung["neu"], stunden=waehrung.EINSPRUCH_STUNDEN,
                   sub_nom=rollen.sub()["label_nom"], verfallen=t("WETTE_VERFALLEN_ZUSATZ") if auto else "",
                   abmachung=await wette_annahme._einsatz_text(w, ergebnis, automatisch=auto)),
            reply_markup=markup)
    except Exception:
        logger.exception("Wett-Ergebnis an die Dom-Seite fehlgeschlagen")
    await wette_annahme._verlauf_eintragen(w, "verfallen" if auto else ergebnis)
    logger.info("Herrin-Wette %s (%s%s).", ergebnis, kennung, ", verfallen" if auto else "")
    await kern.nach_punkteaenderung(bot, buchung["alt"], buchung["neu"], push=(ergebnis == "gewonnen"))


async def callback_wetteeinspruch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Einspruch der Dom-Seite: Urteil kippen, Punkte umbuchen (24 h)."""
    query = update.callback_query
    await query.answer()
    try:
        _, kennung = query.data.split(":", 1)
    except ValueError:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(kern.FELD_WETTE) or {})
    bis = kern._parse(w.get("einspruch_bis", ""))
    if w.get("kennung") != kennung or w.get("status") != "entschieden" or not bis or kern._jetzt() > bis:
        await query.message.reply_text(t("WETTE_EINSPRUCH_VERALTET"))
        return
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    neu_ergebnis = "verloren" if w.get("ergebnis") == "gewonnen" else "gewonnen"
    delta = 2 * einsatz if neu_ergebnis == "gewonnen" else -2 * einsatz
    buchung = await waehrung.buchen(delta, f"Einspruch → {neu_ergebnis}")
    w.update({"status": "gekippt", "ergebnis": neu_ergebnis, "gekippt_am": kern._jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
    await query.message.reply_text(
        t("WETTE_EINSPRUCH_OK", ergebnis=t("WETTE_WORT_" + neu_ergebnis.upper()), stand=buchung["neu"],
          abmachung=await wette_annahme._einsatz_text(w, neu_ergebnis)))
    try:
        await telegram_helper.send_sklave(
            context.bot, t("WETTE_EINSPRUCH_SUB", ergebnis=t("WETTE_WORT_" + neu_ergebnis.upper()),
                           stand=buchung["neu"]))
    except Exception:
        logger.exception("Einspruch-Meldung an den Sub fehlgeschlagen")
    await wette_annahme._verlauf_eintragen(w, neu_ergebnis)
    logger.info("Herrin-Wette gekippt → %s (%s).", neu_ergebnis, kennung)
    await kern.nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)
