"""
Währung ⭐ – Nachrichten-, Button- und Job-Schicht zu services/waehrung
(Bauplan 16.09.2026, vom Owner abgenommen):

  * Rangwechsel (auf/ab) und Schwellen-Push (jede volle Hundert, nur aufwärts)
    an den Sub – der Push nennt Stand, Sparziel und die zwei teuersten
    leistbaren Privilegien mit den vorhandenen privileg:einloesen-Buttons
  * Abzüge: „nicht erledigt" (fest, malus_kette) und Strafe-Buttons −25/−50
    unter dem Strafvorschlag der Dom-Seite (einmal pro Vorschlag)
  * Sparziel: neuer Wunsch → Preis-Buttons 200/300/500 an die Dom-Seite (ohne
    Tipp kein Preis); Stand ≥ Preis → „gewähren / noch nicht" (14 Tage Ruhe)
  * Herrin-Wette (📨 aus dem Wettvorschlag): fester Einsatz obendrauf, Frist
    aus der Idee, Urteil per Sub-Buttons, Einspruch der Dom-Seite (24 h),
    keine Meldung nach 3 Tagen = verloren; höchstens eine laufende Wette
  * Session-Privileg: nach ihrer Bestätigung eine offene Aufgabe mit
    7-Tage-Nachfrage (scheitert sie an ihr → herrin_versaeumnis, kein Malus)

Alles Ein-Tipp für die Dom-Seite, kein Freitext. Fehler hier dürfen nie den
auslösenden Flow (Erledigt-Meldung, Malus-Kette, Wettvorschlag) töten – jede
öffentliche Funktion fängt selbst.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import inventar, paare, qdrant, telegram_helper, waehrung
from bot.services import sticker_reaktionen
from bot.prompts import rollen
from bot.messages import t

logger = logging.getLogger(__name__)

_BOT = None                      # Bot-Handle für Hook (neuer Wunsch) – main.start()
FELD_WETTE = "herrin_wette"      # Sub-Profil
FELD_SCHWELLE = "punkte_schwelle_gemeldet"
FELD_RUHE = "sparziel_ruhe"      # Dom-Profil: {wunsch_lower: iso}
FELD_FRAGE_AM = "sparziel_frage_am"
_OFFEN_MAX = 10


def start(bot) -> None:
    """Beim Bot-Start: Bot-Handle merken und den Neuer-Wunsch-Hook einhängen."""
    global _BOT
    _BOT = bot
    inventar.NEUER_WUNSCH_HOOK = frage_wunschpreis


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Zentrale Nachbereitung jeder Punkteänderung
# ---------------------------------------------------------------------------

async def nach_punkteaenderung(bot, alt: int, neu: int, *, push: bool = True) -> None:
    """Rang (auf/ab) melden, Schwellen-Push (nur bei push=True und aufwärts),
    Sparziel prüfen. Jeder Teil best-effort."""
    try:
        await _rang_melden(bot, alt, neu)
    except Exception:
        logger.exception("Rang-Meldung fehlgeschlagen")
    if push:
        try:
            await _schwelle_melden(bot, alt, neu)
        except Exception:
            logger.exception("Schwellen-Push fehlgeschlagen")
    try:
        await sparziel_pruefen(bot, neu)
    except Exception:
        logger.exception("Sparziel-Prüfung fehlgeschlagen")


def rang_zeile(punkte: int) -> str:
    """„Diener (500) · nächster Rang Leibdiener ab 1000" für /stats & Co."""
    _, titel = waehrung.rang(punkte)
    naechster = waehrung.naechster_rang(punkte)
    if naechster:
        return t("RANG_ZEILE_MIT_NAECHSTEM", titel=titel, naechster=naechster[1], ab=naechster[0])
    return t("RANG_ZEILE_HOECHSTER", titel=titel)


def sparziel_zeile(punkte: int) -> str:
    """„Käfig · 210/300 P" oder leer."""
    ziel = inventar.sparziel()
    if not ziel:
        return ""
    return t("SPARZIEL_ZEILE", wunsch=ziel[0], stand=min(punkte, ziel[1]), preis=ziel[1])


async def _rang_melden(bot, alt: int, neu: int) -> None:
    wechsel = waehrung.rang_wechsel(alt, neu)
    if not wechsel:
        return
    _, titel = waehrung.rang(neu)
    key = "RANG_AUF" if wechsel == "auf" else "RANG_AB"
    await telegram_helper.send_sklave(bot, t(key, titel=titel, stand=neu))
    logger.info("Rangwechsel %s → %s (%d → %d Punkte)", wechsel, titel, alt, neu)


async def _schwelle_melden(bot, alt: int, neu: int) -> None:
    schwelle = waehrung.schwelle_ueberschritten(alt, neu)
    if not schwelle:
        return
    profil = await qdrant.get_user_profile("sklave") or {}
    if int(profil.get(FELD_SCHWELLE, 0) or 0) >= schwelle:
        return
    from bot.handlers.privileg import PRIVILEGIEN  # lazy (Zyklus)
    leistbar = sorted((p for p in PRIVILEGIEN if p["kosten"] <= neu),
                      key=lambda p: p["kosten"], reverse=True)[:2]
    zeilen = [t("SCHWELLE_KOPF", schwelle=schwelle, stand=neu, rang=waehrung.rang(neu)[1])]
    sz = sparziel_zeile(neu)
    if sz:
        zeilen.append(t("SCHWELLE_SPARZIEL", zeile=sz))
    markup = None
    if leistbar:
        zeilen.append(t("SCHWELLE_SHOP"))
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🎁 {p['name']} ({p['kosten']}P)",
                                 callback_data=f"privileg:einloesen:{p['id']}")
        ] for p in leistbar])
    await telegram_helper.send_sklave(bot, "\n".join(zeilen), reply_markup=markup)
    await qdrant.patch_profile_fields("sklave", {FELD_SCHWELLE: schwelle})
    logger.info("Schwellen-Push %d (Stand %d, %d Privileg-Buttons)", schwelle, neu, len(leistbar))


# ---------------------------------------------------------------------------
# Abzüge
# ---------------------------------------------------------------------------

async def abzug_nicht_erledigt() -> dict | None:
    """Fester Abzug bei „nicht erledigt" (malus_kette). None bei 0/Fehler."""
    if waehrung.ABZUG_NICHT_ERLEDIGT <= 0:
        return None
    try:
        return await waehrung.buchen(-waehrung.ABZUG_NICHT_ERLEDIGT, "nicht erledigt")
    except Exception:
        logger.exception("Abzug nicht-erledigt fehlgeschlagen")
        return None


def abzug_buttons(strafe_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ABZUG", betrag=b), callback_data=f"punkteabzug:{b}:{strafe_id}")
        for b in waehrung.STRAFE_ABZUEGE
    ]])


def abzug_anbieten(strafe_id: str) -> None:
    """Strafvorschlag-Kennung als offen merken (Dom-State, einmal pro Vorschlag)."""
    s = state.get(paare.dom_chat_id())
    offen = [x for x in (s.get("abzug_offen") or []) if x != strafe_id]
    s["abzug_offen"] = (offen + [strafe_id])[-_OFFEN_MAX:]


async def callback_punkteabzug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """−25 / −50 unter dem Strafvorschlag (Rolle via main._callback_gate: Dom)."""
    query = update.callback_query
    await query.answer()
    try:
        _, betrag_s, strafe_id = query.data.split(":", 2)
        betrag = int(betrag_s)
    except ValueError:
        return
    if betrag not in waehrung.STRAFE_ABZUEGE:
        return
    s = state.get(str(update.effective_chat.id))
    offen = list(s.get("abzug_offen") or [])
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if strafe_id not in offen:
        await query.message.reply_text(t("ABZUG_VERALTET"))
        return
    s["abzug_offen"] = [x for x in offen if x != strafe_id]
    buchung = await waehrung.buchen(-betrag, f"Strafe {strafe_id[:8]}")
    await query.message.reply_text(t("ABZUG_DOM_OK", betrag=-buchung["delta"], stand=buchung["neu"]))
    try:
        await telegram_helper.send_sklave(
            context.bot, t("ABZUG_SUB", betrag=-buchung["delta"], stand=buchung["neu"]))
    except Exception:
        logger.exception("Abzug-Meldung an den Sub fehlgeschlagen")
    await nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)


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
    if _BOT is None:
        return
    s = state.get(paare.dom_chat_id())
    offen = dict(s.get("wunschpreis_offen") or {})
    kennung = uuid.uuid4().hex[:8]
    offen[kennung] = wunsch
    if len(offen) > _OFFEN_MAX:
        for k in list(offen)[:-_OFFEN_MAX]:
            offen.pop(k, None)
    s["wunschpreis_offen"] = offen
    await telegram_helper.send_domina(
        _BOT, t("WUNSCHPREIS_FRAGE", wunsch=wunsch, sub_nom=rollen.sub()["label_nom"]),
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
    ruhe_bis = _parse((dom.get(FELD_RUHE) or {}).get(wunsch.lower(), ""))
    if ruhe_bis and _jetzt() < ruhe_bis + timedelta(days=waehrung.SPARZIEL_RUHE_TAGE):
        return False
    letzte = _parse(dom.get(FELD_FRAGE_AM, ""))
    if letzte and _jetzt() < letzte + timedelta(hours=24):
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
    await qdrant.patch_profile_fields("domina", {FELD_FRAGE_AM: _jetzt().isoformat()})
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
        ruhe = dict(dom.get(FELD_RUHE) or {})
        ruhe[wunsch.lower()] = _jetzt().isoformat()
        await qdrant.patch_profile_fields("domina", {FELD_RUHE: ruhe})
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
    await nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)


# ---------------------------------------------------------------------------
# Herrin-Wette (📨 aus dem Wettvorschlag)
# ---------------------------------------------------------------------------

def wette_laeuft(profil: dict | None) -> bool:
    w = (profil or {}).get(FELD_WETTE) or {}
    return w.get("status") in ("laeuft", "gefragt")


async def wette_starten(idee: str, ansage: str, kennung: str) -> int:
    """Nach dem Versand der Herrin-Ansage: Wette mit Frist im Sub-Profil parken.
    Gibt die Frist in Tagen zurück."""
    tage = waehrung.wett_frist_tage(idee)
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: {
        "kennung": kennung, "idee": idee[:600], "ansage": ansage[:900],
        "einsatz": waehrung.WETT_EINSATZ, "gestartet_am": _jetzt().isoformat(),
        "frist": waehrung.frist_iso(tage), "status": "laeuft",
    }})
    logger.info("Herrin-Wette gestartet (%s, Frist %d Tag(e), Einsatz %d).",
                kennung, tage, waehrung.WETT_EINSATZ)
    return tage


def _urteil_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WETTE_GEWONNEN"), callback_data=f"wetteurteil:gewonnen:{kennung}"),
        InlineKeyboardButton(t("BUTTON_WETTE_VERLOREN"), callback_data=f"wetteurteil:verloren:{kennung}"),
    ]])


async def wette_urteil_job(bot) -> None:
    """Alle 15 Min (main, _pro_paar): Frist um → Urteils-Frage an den Sub;
    3 Tage ohne Meldung → verloren."""
    try:
        profil = await qdrant.get_user_profile("sklave") or {}
        w = profil.get(FELD_WETTE) or {}
        if not w:
            return
        jetzt = _jetzt()
        if w.get("status") == "laeuft":
            frist = _parse(w.get("frist", ""))
            if frist and jetzt >= frist:
                await telegram_helper.send_sklave(
                    bot, t("WETTE_URTEIL_FRAGE", einsatz=w.get("einsatz", waehrung.WETT_EINSATZ)),
                    reply_markup=_urteil_buttons(w.get("kennung", "")))
                w.update({"status": "gefragt", "gefragt_am": jetzt.isoformat()})
                await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
                logger.info("Wett-Urteil erfragt (%s).", w.get("kennung"))
        elif w.get("status") == "gefragt":
            gefragt = _parse(w.get("gefragt_am", ""))
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
    w = profil.get(FELD_WETTE) or {}
    if w.get("kennung") != kennung or w.get("status") != "gefragt":
        await query.message.reply_text(t("WETTE_URTEIL_VERALTET"))
        return
    await _wette_abschliessen(context.bot, ergebnis)


async def _wette_abschliessen(bot, ergebnis: str, auto: bool = False) -> None:
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(FELD_WETTE) or {})
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    delta = einsatz if ergebnis == "gewonnen" else -einsatz
    buchung = await waehrung.buchen(delta, f"Herrin-Wette {ergebnis}{' (verfallen)' if auto else ''}")
    jetzt = _jetzt()
    w.update({"status": "entschieden", "ergebnis": ergebnis, "entschieden_am": jetzt.isoformat(),
              "einspruch_bis": (jetzt + timedelta(hours=waehrung.EINSPRUCH_STUNDEN)).isoformat(),
              "verfallen": bool(auto)})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
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
                   sub_nom=rollen.sub()["label_nom"], verfallen=t("WETTE_VERFALLEN_ZUSATZ") if auto else ""),
            reply_markup=markup)
    except Exception:
        logger.exception("Wett-Ergebnis an die Dom-Seite fehlgeschlagen")
    logger.info("Herrin-Wette %s (%s%s).", ergebnis, kennung, ", verfallen" if auto else "")
    await nach_punkteaenderung(bot, buchung["alt"], buchung["neu"], push=(ergebnis == "gewonnen"))


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
    w = dict(profil.get(FELD_WETTE) or {})
    bis = _parse(w.get("einspruch_bis", ""))
    if w.get("kennung") != kennung or w.get("status") != "entschieden" or not bis or _jetzt() > bis:
        await query.message.reply_text(t("WETTE_EINSPRUCH_VERALTET"))
        return
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    neu_ergebnis = "verloren" if w.get("ergebnis") == "gewonnen" else "gewonnen"
    delta = 2 * einsatz if neu_ergebnis == "gewonnen" else -2 * einsatz
    buchung = await waehrung.buchen(delta, f"Einspruch → {neu_ergebnis}")
    w.update({"status": "gekippt", "ergebnis": neu_ergebnis, "gekippt_am": _jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    await query.message.reply_text(
        t("WETTE_EINSPRUCH_OK", ergebnis=t("WETTE_WORT_" + neu_ergebnis.upper()), stand=buchung["neu"]))
    try:
        await telegram_helper.send_sklave(
            context.bot, t("WETTE_EINSPRUCH_SUB", ergebnis=t("WETTE_WORT_" + neu_ergebnis.upper()),
                           stand=buchung["neu"]))
    except Exception:
        logger.exception("Einspruch-Meldung an den Sub fehlgeschlagen")
    logger.info("Herrin-Wette gekippt → %s (%s).", neu_ergebnis, kennung)
    await nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)


# ---------------------------------------------------------------------------
# Session-Privileg
# ---------------------------------------------------------------------------

async def session_wunsch_task() -> str | None:
    """Nach Bestätigung des Privilegs „Session nach Wunsch": offene Aufgabe mit
    7-Tage-Nachfrage – Kategorien aus den Wunsch-Kategorien des Subs."""
    try:
        profil = await qdrant.get_user_profile("sklave") or {}
        dom = await qdrant.get_user_profile("domina") or {}
        kats = list(profil.get("wunsch_kategorien", []) or [])
        from bot.services import kategorie_logik
        anzeige = kategorie_logik.anzeige_liste(kats) if kats else t("SESSION_WUNSCH_OHNE_KATEGORIE")
        text = t("SESSION_WUNSCH_AUFGABE", kategorien=anzeige)
        task_id = await qdrant.erstelle_task(
            text, kats[0] if kats else "allgemein", int(dom.get("aktuelles_level", 1) or 1),
            status="offen", quelle="privileg", followup_in_tagen=7,
            extra={"privileg_id": "session_wunsch"})
        logger.info("Session-nach-Wunsch-Aufgabe angelegt (Task %s).", task_id)
        return task_id
    except Exception:
        logger.exception("Session-nach-Wunsch-Aufgabe konnte nicht angelegt werden")
        return None
