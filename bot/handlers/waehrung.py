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
  * Herrin-Wette (📨 aus dem Wettvorschlag): der Sub muss annehmen oder ablehnen
    (Erinnerung nach 4 h, nach 24 h gilt sie als angenommen); angenommen: fester
    Einsatz obendrauf, Frist ab Annahme, Urteil per Sub-Buttons, Einspruch der
    Dom-Seite (24 h), keine Meldung nach 3 Tagen = verloren; abgelehnt: die
    Dom-Seite wählt den Abzug (−25/−50/−100) und eine Strafe (3 Vorschläge, 🎲,
    eigene oder keine), die als Aufgabe angelegt wird; höchstens eine offene Wette
  * Session-Privileg: nach ihrer Bestätigung eine offene Aufgabe mit
    7-Tage-Nachfrage (scheitert sie an ihr → herrin_versaeumnis, kein Malus)

Alles Ein-Tipp für die Dom-Seite; Freitext nur, wenn sie ✍️ Eigene Strafe wählt. Fehler hier dürfen nie den
auslösenden Flow (Erledigt-Meldung, Malus-Kette, Wettvorschlag) töten – jede
öffentliche Funktion fängt selbst.
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import grok, inventar, limits_check, paare, qdrant, telegram_helper, tts, waehrung
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

# Lebenslauf im Sub-Profil (FELD_WETTE.status):
#   angeboten ─✅ / 24 h ohne Antwort─▶ laeuft ─Frist─▶ gefragt ─▶ entschieden (─Einspruch─▶ gekippt)
#   angeboten ─❌─▶ abgelehnt ─Abzug + Strafe/keine, oder 24 h ohne Wahl─▶ abgelehnt_erledigt
_OFFENE_STATUS = ("angeboten", "laeuft", "gefragt", "abgelehnt")
MODE_STRAFE_EIGEN = "wette_strafe_eigen"   # Dom-Chat: nächste Nachricht = eigene Strafe
_HINTERGRUND: set = set()


def im_hintergrund(coro) -> None:
    """LLM-lastige Folgeschritte (Reasoning, 30–70 s) aus dem Handler lösen:
    Updates desselben Paares laufen strikt seriell (main._paar_update_prozessor),
    abgewartet würde die Generierung auch den Chat der anderen Seite blockieren.
    Die Paar-ContextVar wandert mit (asyncio kopiert den Kontext in den Task)."""
    async def _sicher():
        try:
            await coro
        except Exception:
            logger.exception("Hintergrund-Schritt fehlgeschlagen")
    task = asyncio.get_running_loop().create_task(_sicher())
    _HINTERGRUND.add(task)
    task.add_done_callback(_HINTERGRUND.discard)


def wette_laeuft(profil: dict | None) -> bool:
    """Angenommen und noch nicht entschieden (Anzeige in /stats und Mini-App)."""
    w = (profil or {}).get(FELD_WETTE) or {}
    return w.get("status") in ("laeuft", "gefragt")


def wette_offen(profil: dict | None) -> bool:
    """Blockiert neue Wettvorschläge: angeboten, laufend, im Urteil – oder
    abgelehnt, solange die Dom-Seite noch über Abzug/Strafe entscheidet."""
    w = (profil or {}).get(FELD_WETTE) or {}
    return w.get("status") in _OFFENE_STATUS


def _datum_lokal(iso: str) -> str:
    d = _parse(iso)
    return d.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.") if d else "?"


def lage_text(profil: dict | None) -> str:
    """Kurzbeschreibung einer offenen Wette für die Dom-Seite (/wette, Mini-App)."""
    w = (profil or {}).get(FELD_WETTE) or {}
    status = w.get("status")
    if status == "angeboten":
        return t("WETTE_LAGE_ANGEBOTEN")
    if status in ("laeuft", "gefragt"):
        return t("WETTE_LAGE_LAEUFT", datum=_datum_lokal(w.get("frist", "")))
    if status == "abgelehnt":
        return t("WETTE_LAGE_ABGELEHNT")
    return ""


def _tagsueber() -> bool:
    von, bis = waehrung.TAGSUEBER
    return von <= datetime.now(ZoneInfo(config.TIMEZONE)).hour < bis


def _sub_gross() -> str:
    nom = rollen.sub()["label_nom"]
    return nom[:1].upper() + nom[1:]


def _abmachung(w: dict) -> str:
    """Die vereinbarte Wette für Ergebnis-Meldungen an die Dom-Seite – sie soll
    wissen, was sie jetzt eintreibt bzw. einlöst (Owner-Wunsch 17.09.2026: die
    Meldung nannte nur die Punkte). Die Idee ist schon an sie formuliert;
    Rückfragen/Vorworte aus älteren Ideen fallen weg."""
    idee = (w.get("idee") or "").strip()
    if not idee:
        return ""
    from bot.handlers import coach_quiz  # lazy: coach_quiz importiert dieses Modul
    idee = coach_quiz._meta_schluss_entfernen(
        coach_quiz._nachsatz_entfernen(coach_quiz._vorwort_entfernen(idee)))
    return t("WETTE_ABMACHUNG", idee=idee) if idee else ""


def _annahme_buttons(kennung: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WETTE_ANNEHMEN"), callback_data=f"wetteantwort:annehmen:{kennung}"),
        InlineKeyboardButton(t("BUTTON_WETTE_ABLEHNEN"), callback_data=f"wetteantwort:ablehnen:{kennung}"),
    ]])


async def wette_anbieten(bot, idee: str, ansage: str, kennung: str) -> None:
    """📨 der Dom-Seite: Herrin-Ansage (Text ohne Sprech-Tags + Stimme) mit ✅/❌
    an den Sub, Wette als „angeboten" parken. Geparkt wird VOR dem Versand (ein
    schneller Tipp findet sie schon); scheitert der Versand, kommt der vorige
    Eintrag zurück und der Fehler geht an den Aufrufer."""
    profil = await qdrant.get_user_profile("sklave") or {}
    vorher = profil.get(FELD_WETTE)
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: {
        "kennung": kennung, "idee": idee[:600], "ansage": ansage[:900],
        "einsatz": waehrung.WETT_EINSATZ, "angeboten_am": _jetzt().isoformat(),
        "status": "angeboten",
    }})
    try:
        await telegram_helper.send_sklave(bot, tts.entferne_sprech_tags(ansage),
                                          reply_markup=_annahme_buttons(kennung), voice_text=ansage)
    except Exception:
        await qdrant.patch_profile_fields("sklave", {FELD_WETTE: vorher})
        raise
    logger.info("Herrin-Wette angeboten (%s, Einsatz %d).", kennung, waehrung.WETT_EINSATZ)


async def _wette_annehmen(bot, w: dict, automatisch: bool) -> None:
    tage = waehrung.wett_frist_tage(w.get("idee", ""))
    frist = waehrung.frist_iso(tage)
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    w.update({"status": "laeuft", "gestartet_am": _jetzt().isoformat(), "frist": frist,
              "angenommen": "automatisch" if automatisch else "tipp"})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    logger.info("Herrin-Wette angenommen (%s, %s, Frist %d Tag(e)).",
                w.get("kennung"), "automatisch" if automatisch else "Tipp", tage)
    try:
        await telegram_helper.send_sklave(
            bot, t("WETTE_AUTO_ANGENOMMEN_SUB" if automatisch else "WETTE_ANGENOMMEN_SUB",
                   tage=tage, einsatz=einsatz))
    except Exception:
        logger.exception("Annahme-Meldung an den Sub fehlgeschlagen")
    try:
        await telegram_helper.send_domina(
            bot, t("WETTE_ANGENOMMEN_DOM", sub_gross=_sub_gross(), datum=_datum_lokal(frist),
                   zusatz=t("WETTE_AUTOMATISCH_ZUSATZ") if automatisch else ""))
    except Exception:
        logger.exception("Annahme-Meldung an die Dom-Seite fehlgeschlagen")


async def callback_wetteantwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✅ Annehmen / ❌ Ablehnen unter der Wett-Ansage (Rolle via main._callback_gate: Sub)."""
    query = update.callback_query
    await query.answer()
    try:
        _, aktion, kennung = query.data.split(":", 2)
    except ValueError:
        return
    if aktion not in ("annehmen", "ablehnen"):
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(FELD_WETTE) or {})
    if w.get("kennung") != kennung:
        await query.message.reply_text(t("WETTE_ANTWORT_VERALTET"))
        return
    if w.get("status") != "angeboten":
        return  # Doppel-Tap / schon automatisch angenommen: still
    if aktion == "annehmen":
        await _wette_annehmen(context.bot, w, automatisch=False)
        return
    jetzt = _jetzt().isoformat()
    w.update({"status": "abgelehnt", "abgelehnt_am": jetzt, "letzte_aktion_am": jetzt,
              "abzug": None, "strafe_optionen": [], "strafe_neu": 0})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    logger.info("Herrin-Wette abgelehnt (%s).", kennung)
    try:
        await telegram_helper.send_sklave(context.bot, t("WETTE_ABGELEHNT_SUB"))
    except Exception:
        logger.exception("Ablehnungs-Meldung an den Sub fehlgeschlagen")
    await telegram_helper.send_domina(
        context.bot, t("WETTE_ABGELEHNT_DOM", sub_gross=_sub_gross()),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ABZUG", betrag=b), callback_data=f"wetteablehnung:punkte:{b}:{kennung}")
            for b in waehrung.WETT_ABLEHNUNG_ABZUEGE]]))


# --- Ablehnung: Abzug + Strafwahl der Dom-Seite -----------------------------

def _strafe_buttons(kennung: str, anzahl: int) -> InlineKeyboardMarkup:
    reihen = []
    if anzahl:
        reihen.append([InlineKeyboardButton(str(i + 1), callback_data=f"wetteablehnung:strafe:{i}:{kennung}")
                       for i in range(anzahl)])
    reihen.append([
        InlineKeyboardButton(t("BUTTON_WETTE_STRAFE_NEU"), callback_data=f"wetteablehnung:neu:{kennung}"),
        InlineKeyboardButton(t("BUTTON_WETTE_STRAFE_EIGENE"), callback_data=f"wetteablehnung:eigene:{kennung}"),
    ])
    reihen.append([InlineKeyboardButton(t("BUTTON_WETTE_STRAFE_KEINE"), callback_data=f"wetteablehnung:keine:{kennung}")])
    return InlineKeyboardMarkup(reihen)


def _strafe_wahl_text(w: dict) -> str:
    kopf = w.get("dom_kopf", "")
    optionen = w.get("strafe_optionen") or []
    if not optionen:
        return f"{kopf}\n\n{t('WETTE_STRAFE_KEINE_VORSCHLAEGE')}"
    liste = "\n\n".join(f"{i + 1}. {o}" for i, o in enumerate(optionen))
    return f"{kopf}\n\n{t('WETTE_STRAFE_WAHL')}\n\n{liste}"


async def _ablehnungs_strafen_generieren(idee: str) -> list[str]:
    """Drei kurze Strafen (Reasoning, Limits-Retry). Leer = nichts Brauchbares."""
    from bot.prompts import bestrafung
    sp = await qdrant.get_user_profile("sklave") or {}
    dp = await qdrant.get_user_profile("domina") or {}
    try:
        letzte = [e.get("bestrafung_text", "") or e.get("aufgabe", "")
                  for e in (await qdrant.get_strafen("sklave", limit=5) or [])]
    except Exception:
        logger.exception("Strafen-Historie nicht lesbar – ohne")
        letzte = []
    hl, gr = sp.get("hard_limits", []) or [], dp.get("grenzen", []) or []
    roh = await limits_check.generate_mit_limit_retry(
        bestrafung.ablehnungs_strafen(
            idee, hl, sp.get("vorlieben", []) or [], sp.get("kategorie_reaktionen", {}) or {},
            letzte, sp.get("dossier", "") or ""),
        sklave_hard_limits=hl, domina_grenzen=gr, reasoning=True, max_tokens=600)
    if not roh:
        return []
    try:
        daten = grok.parse_json(roh)
        liste = daten.get("strafen", []) if isinstance(daten, dict) else daten
    except Exception:
        logger.exception("Strafvorschläge nicht parsebar")
        return []
    optionen = [re.sub(r"\s+", " ", str(o)).strip() for o in (liste or []) if str(o).strip()]
    return [o for o in optionen if len(o) <= 240][:3]


async def _strafen_zeigen(bot, query, kennung: str, idee: str) -> None:
    """Strafvorschläge erzeugen und die Dom-Nachricht damit umbauen (Hintergrund)."""
    optionen = await _ablehnungs_strafen_generieren(idee)
    if state.is_paused():
        return
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(FELD_WETTE) or {})
    if w.get("kennung") != kennung or w.get("status") != "abgelehnt":
        return
    w.update({"strafe_optionen": optionen, "letzte_aktion_am": _jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    text, markup = _strafe_wahl_text(w), _strafe_buttons(kennung, len(optionen))
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except Exception:
        await telegram_helper.send_domina(bot, text, reply_markup=markup)


async def _strafe_anordnen(bot, w: dict, strafe: str) -> tuple[str, str] | None:
    """Strafe prüfen, in der Herrin-Stimme anordnen, als Aufgabe anlegen, im
    Strafen-Protokoll vermerken, Ablehnung abschließen. None = erledigt, sonst
    (fehlerart, detail) mit fehlerart „limit" oder „fehler"."""
    from bot.prompts import followup as fp
    sp = await qdrant.get_user_profile("sklave") or {}
    dp = await qdrant.get_user_profile("domina") or {}
    hl, gr = sp.get("hard_limits", []) or [], dp.get("grenzen", []) or []
    try:
        treffer = await limits_check.verletzungen(strafe, hl, gr)
        if treffer:
            return "limit", ", ".join(sorted({v["limit"] for v in treffer}))
        anordnung = grok.clean_text(await grok.simple(fp.strafe_fuer_ablehnung(strafe), max_tokens=300))
        if not anordnung:
            return "fehler", ""
        treffer = await limits_check.verletzungen(anordnung, hl, gr)
        if treffer:
            return "limit", ", ".join(sorted({v["limit"] for v in treffer}))
    except Exception:
        logger.exception("Strafe fürs Ablehnen nicht formulierbar")
        return "fehler", ""
    task_id = await qdrant.erstelle_task(strafe, "allgemein", dp.get("aktuelles_level", 1) or 1,
                                         quelle="wette_ablehnung")
    try:
        try:
            await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.STRAFE)
        except Exception:
            pass
        await telegram_helper.send_sklave(bot, tts.entferne_sprech_tags(anordnung), voice_text=anordnung)
    except Exception:
        logger.exception("Strafe fürs Ablehnen nicht zustellbar – Aufgabe zurückgerollt")
        await qdrant.loesche_task(task_id)
        return "fehler", ""
    try:
        await qdrant.save_strafe({"user_id": "sklave", "aufgabe": t("WETTE_STRAFE_PROTOKOLL"),
                                  "grund": "wette_abgelehnt", "bestrafung_text": strafe,
                                  "datum": _jetzt().isoformat(), "status": "angeordnet"})
    except Exception:
        logger.exception("Strafen-Protokoll-Eintrag fehlgeschlagen")
    w.update({"status": "abgelehnt_erledigt", "strafe": strafe[:400], "strafe_task_id": task_id,
              "erledigt_am": _jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    logger.info("Strafe fürs Ablehnen angeordnet (%s, Aufgabe %s).", w.get("kennung"), task_id)
    return None


async def callback_wetteablehnung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dom-Seite nach ❌ (Rolle via main._callback_gate: Dom):
    wetteablehnung:punkte:<betrag>:<kennung> → Abzug, dann Strafvorschläge;
    wetteablehnung:strafe:<i>:<kennung> | neu | eigene | keine."""
    query = update.callback_query
    teile = (query.data or "").split(":")
    if len(teile) not in (3, 4):
        await query.answer()
        return
    aktion, kennung = teile[1], teile[-1]
    wert = teile[2] if len(teile) == 4 else ""
    toast = {"punkte": "WETTE_STRAFE_SUCHT_TOAST", "neu": "WETTE_STRAFE_SUCHT_TOAST",
             "strafe": "COACH_WETTIDEE_SCHICKT"}.get(aktion)
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(FELD_WETTE) or {})
    if w.get("kennung") != kennung:
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(t("WETTE_ABLEHNUNG_VERALTET"))
        return
    if w.get("status") != "abgelehnt":
        await query.answer()  # Doppel-Tap nach dem Abschluss: still
        return
    await query.answer(t(toast) if toast else None)

    if aktion == "punkte":
        if w.get("abzug") is not None:
            return
        try:
            betrag = int(wert)
        except ValueError:
            return
        if betrag not in waehrung.WETT_ABLEHNUNG_ABZUEGE:
            return
        buchung = await waehrung.buchen(-betrag, "Wette abgelehnt")
        w.update({"abzug": betrag, "letzte_aktion_am": _jetzt().isoformat(),
                  "dom_kopf": t("WETTE_ABLEHNUNG_ABZUG_DOM", sub_gross=_sub_gross(),
                                betrag=betrag, stand=buchung["neu"])})
        await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w['dom_kopf']}\n\n{t('WETTE_STRAFE_SUCHT')}", reply_markup=None)
        except Exception:
            pass
        try:
            await telegram_helper.send_sklave(context.bot, t("WETTE_ABZUG_SUB", betrag=betrag, stand=buchung["neu"]))
        except Exception:
            logger.exception("Abzug-Meldung an den Sub fehlgeschlagen")
        await nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)
        im_hintergrund(_strafen_zeigen(context.bot, query, kennung, w.get("idee", "")))
        return

    if w.get("abzug") is None:
        return  # Strafwahl erst nach dem Abzug
    if aktion == "neu":
        if int(w.get("strafe_neu", 0) or 0) >= waehrung.WETT_STRAFE_MAX_NEU:
            await query.message.reply_text(t("WETTE_STRAFE_NEU_LIMIT"))
            return
        w.update({"strafe_neu": int(w.get("strafe_neu", 0) or 0) + 1, "letzte_aktion_am": _jetzt().isoformat()})
        await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w.get('dom_kopf', '')}\n\n{t('WETTE_STRAFE_SUCHT')}", reply_markup=None)
        except Exception:
            pass
        im_hintergrund(_strafen_zeigen(context.bot, query, kennung, w.get("idee", "")))
        return
    if aktion == "keine":
        w.update({"status": "abgelehnt_erledigt", "strafe": None, "erledigt_am": _jetzt().isoformat()})
        await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w.get('dom_kopf', '')}\n\n{t('WETTE_STRAFE_KEINE')}", reply_markup=None)
        except Exception:
            await query.message.reply_text(t("WETTE_STRAFE_KEINE"))
        logger.info("Ablehnung ohne Strafe abgeschlossen (%s).", kennung)
        return
    if aktion == "eigene":
        chat_id = str(update.effective_chat.id)
        state.set_mode(chat_id, MODE_STRAFE_EIGEN)
        s = state.get(chat_id)
        s["wette_strafe_kennung"] = kennung
        s["wette_strafe_msg"] = getattr(query.message, "message_id", None)
        await query.message.reply_text(t("WETTE_STRAFE_EIGENE_FRAGE"))
        return
    if aktion == "strafe":
        optionen = w.get("strafe_optionen") or []
        try:
            strafe = optionen[int(wert)]
        except (ValueError, IndexError):
            return
        fehler = await _strafe_anordnen(context.bot, w, strafe)
        if fehler:
            await query.message.reply_text(
                t("WETTE_STRAFE_LIMIT", begriffe=fehler[1]) if fehler[0] == "limit" else t("WETTE_STRAFE_FEHLER"))
            return
        try:
            await query.edit_message_text(
                f"{w.get('dom_kopf', '')}\n\n{t('WETTE_STRAFE_ANGEORDNET')}\n\n{strafe}", reply_markup=None)
        except Exception:
            await query.message.reply_text(t("WETTE_STRAFE_ANGEORDNET"))


async def handle_strafe_eigen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dom-Chat im MODE_STRAFE_EIGEN: die Nachricht ist die selbst geschriebene Strafe."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    kennung = s.get("wette_strafe_kennung")
    msg_id = s.get("wette_strafe_msg")
    text = (update.message.text or "").strip()
    state.set_mode(chat_id, "chat")
    s.pop("wette_strafe_kennung", None)
    s.pop("wette_strafe_msg", None)
    profil = await qdrant.get_user_profile("sklave") or {}
    w = dict(profil.get(FELD_WETTE) or {})
    if not kennung or w.get("kennung") != kennung or w.get("status") != "abgelehnt" or w.get("abzug") is None:
        await update.message.reply_text(t("WETTE_ABLEHNUNG_VERALTET"))
        return
    fehler = await _strafe_anordnen(context.bot, w, text[:400])
    if fehler:
        # nochmal schreiben lassen – Modus bleibt für den nächsten Versuch
        state.set_mode(chat_id, MODE_STRAFE_EIGEN)
        s["wette_strafe_kennung"], s["wette_strafe_msg"] = kennung, msg_id
        await update.message.reply_text(
            t("WETTE_STRAFE_LIMIT", begriffe=fehler[1]) if fehler[0] == "limit" else t("WETTE_STRAFE_FEHLER"))
        return
    if msg_id:
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
        except Exception:
            pass
    await update.message.reply_text(t("WETTE_STRAFE_ANGEORDNET"))


async def _ablehnung_automatisch_abschliessen(bot, w: dict) -> None:
    """Dom-Seite hat 24 h nicht gewählt: Default-Abzug (falls noch keiner), keine Strafe."""
    if w.get("abzug") is None:
        betrag = waehrung.WETT_ABLEHNUNG_DEFAULT
        buchung = await waehrung.buchen(-betrag, "Wette abgelehnt (automatisch)")
        w["abzug"] = betrag
        try:
            await telegram_helper.send_sklave(bot, t("WETTE_ABLEHNUNG_AUTO_SUB", betrag=betrag, stand=buchung["neu"]))
        except Exception:
            logger.exception("Auto-Abzug-Meldung an den Sub fehlgeschlagen")
        await nach_punkteaenderung(bot, buchung["alt"], buchung["neu"], push=False)
    w.update({"status": "abgelehnt_erledigt", "strafe": None, "automatisch": True,
              "erledigt_am": _jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
    logger.info("Ablehnung automatisch abgeschlossen (%s, Abzug %s).", w.get("kennung"), w.get("abzug"))


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
        w = profil.get(FELD_WETTE) or {}
        if not w:
            return
        jetzt = _jetzt()
        if w.get("status") == "angeboten":
            angeboten = _parse(w.get("angeboten_am", ""))
            if not angeboten or not _tagsueber():
                return
            if jetzt >= angeboten + timedelta(hours=waehrung.WETT_ANNAHME_AUTO_STUNDEN):
                await _wette_annehmen(bot, dict(w), automatisch=True)
            elif not w.get("erinnert") and jetzt >= angeboten + timedelta(hours=waehrung.WETT_ANNAHME_ERINNERUNG_STUNDEN):
                rest = angeboten + timedelta(hours=waehrung.WETT_ANNAHME_AUTO_STUNDEN) - jetzt
                await telegram_helper.send_sklave(
                    bot, t("WETTE_ERINNERUNG_SUB", stunden=max(1, round(rest.total_seconds() / 3600))),
                    reply_markup=_annahme_buttons(w.get("kennung", "")))
                w = dict(w, erinnert=True)
                await qdrant.patch_profile_fields("sklave", {FELD_WETTE: w})
                logger.info("Wett-Annahme erinnert (%s).", w.get("kennung"))
        elif w.get("status") == "abgelehnt":
            letzte = _parse(w.get("letzte_aktion_am") or w.get("abgelehnt_am", ""))
            if letzte and _tagsueber() and jetzt >= letzte + timedelta(hours=waehrung.WETT_ABLEHNUNG_AUTO_STUNDEN):
                await _ablehnung_automatisch_abschliessen(bot, dict(w))
        elif w.get("status") == "laeuft":
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
                   sub_nom=rollen.sub()["label_nom"], verfallen=t("WETTE_VERFALLEN_ZUSATZ") if auto else "",
                   abmachung=_abmachung(w)),
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
        t("WETTE_EINSPRUCH_OK", ergebnis=t("WETTE_WORT_" + neu_ergebnis.upper()), stand=buchung["neu"],
          abmachung=_abmachung(w)))
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
