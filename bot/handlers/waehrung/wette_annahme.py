"""
Herrin-Wette: Lage, Verlauf, Angebot, Annahme.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot import config
from bot.services import grok, limits_check, qdrant, telegram_helper, tts, waehrung
from bot.prompts import rollen
from bot.messages import t
from bot.handlers.waehrung import kern

logger = logging.getLogger(__name__)



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
    w = (profil or {}).get(kern.FELD_WETTE) or {}
    return w.get("status") in ("laeuft", "gefragt")


def wette_offen(profil: dict | None) -> bool:
    """Blockiert neue Wettvorschläge: angeboten, laufend, im Urteil – oder
    abgelehnt, solange die Dom-Seite noch über Abzug/Strafe entscheidet."""
    w = (profil or {}).get(kern.FELD_WETTE) or {}
    return w.get("status") in _OFFENE_STATUS


def _datum_lokal(iso: str) -> str:
    d = kern._parse(iso)
    return d.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.") if d else "?"


def lage_text(profil: dict | None) -> str:
    """Kurzbeschreibung einer offenen Wette für die Dom-Seite (/wette, Mini-App)."""
    w = (profil or {}).get(kern.FELD_WETTE) or {}
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


async def _verlauf_eintragen(w: dict, ergebnis: str) -> None:
    """Entschiedene/abgelehnte Wette in den Verlauf schreiben (Owner-Wunsch
    17.09.2026): der nächste Wettvorschlag soll Bedingung und Einsatz nicht
    wiederholen und sich auf den Ausgang beziehen können. Kippt ein Einspruch
    das Urteil, wird der vorhandene Eintrag korrigiert statt ein zweiter
    angelegt. Best-effort – ein Fehler hier darf den Flow nie stoppen."""
    idee = _idee_bereinigt(w)
    if not idee:
        return
    try:
        profil = await qdrant.get_user_profile("sklave") or {}
        verlauf = [e for e in (profil.get(kern.FELD_WETT_VERLAUF) or []) if isinstance(e, dict)]
        kennung = w.get("kennung", "")
        eintrag = {"kennung": kennung, "idee": idee[:300], "ergebnis": ergebnis,
                   "datum": kern._jetzt().astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")}
        if verlauf and verlauf[-1].get("kennung") == kennung:
            verlauf[-1] = eintrag
        else:
            verlauf.append(eintrag)
        await qdrant.patch_profile_fields(
            "sklave", {kern.FELD_WETT_VERLAUF: verlauf[-kern.WETT_VERLAUF_MAX:]})
    except Exception:
        logger.exception("Wett-Verlauf konnte nicht geschrieben werden")


def letzte_wetten(profil: dict | None) -> list[dict]:
    """Die letzten Wetten mit Ausgang – Kontext für den nächsten Vorschlag
    (handlers/coach_quiz._wett_idee_generieren)."""
    verlauf = [e for e in ((profil or {}).get(kern.FELD_WETT_VERLAUF) or []) if isinstance(e, dict)]
    return verlauf[-kern.WETT_VERLAUF_MAX:]


def _idee_bereinigt(w: dict) -> str:
    """Gespeicherte Wett-Idee ohne Rückfragen/Vorworte älterer Ideen."""
    idee = (w.get("idee") or "").strip()
    if not idee:
        return ""
    from bot.handlers import coach_quiz  # lazy: coach_quiz importiert dieses Modul
    return coach_quiz._meta_schluss_entfernen(
        coach_quiz._nachsatz_entfernen(coach_quiz._vorwort_entfernen(idee)))


def _abmachung(w: dict) -> str:
    """Die vereinbarte Wette wörtlich – Fallback, wenn Grok ausfällt."""
    idee = _idee_bereinigt(w)
    return t("WETTE_ABMACHUNG", idee=idee) if idee else ""


async def _einsatz_text(w: dict, ergebnis: str, automatisch: bool = False) -> str:
    """Zusatz für Ergebnis-/Einspruch-Meldungen an die Dom-Seite: sie soll wissen,
    was sie jetzt eintreibt bzw. einlöst (Owner-Wunsch 17.09.2026: die Meldung
    nannte nur die Punkte; formuliert über Grok im Coach-Ton, gemein, wenn sie
    gewonnen hat). Limits-Treffer oder LLM-Fehler → Abmachung wörtlich."""
    idee = _idee_bereinigt(w)
    if not idee:
        return ""
    from bot.prompts import followup as fp
    try:
        text = grok.clean_text(await grok.simple(
            fp.wett_ergebnis_an_dom(idee, sub_gewonnen=(ergebnis == "gewonnen"), automatisch=automatisch),
            max_tokens=250))
        if not text:
            return _abmachung(w)
        sp = await qdrant.get_user_profile("sklave") or {}
        dp = await qdrant.get_user_profile("domina") or {}
        if await limits_check.verletzungen(text, sp.get("hard_limits", []) or [], dp.get("grenzen", []) or []):
            logger.info("Wett-Ergebnis-Text berührt Limits – Abmachung wörtlich (%s).", w.get("kennung"))
            return _abmachung(w)
        return f"\n\n{text}"
    except Exception:
        logger.exception("Wett-Ergebnis-Text (Grok) fehlgeschlagen – Abmachung wörtlich")
        return _abmachung(w)


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
    vorher = profil.get(kern.FELD_WETTE)
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: {
        "kennung": kennung, "idee": idee[:600], "ansage": ansage[:900],
        "einsatz": waehrung.WETT_EINSATZ, "angeboten_am": kern._jetzt().isoformat(),
        "status": "angeboten",
    }})
    try:
        await telegram_helper.send_sklave(bot, tts.entferne_sprech_tags(ansage),
                                          reply_markup=_annahme_buttons(kennung), voice_text=ansage)
    except Exception:
        await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: vorher})
        raise
    logger.info("Herrin-Wette angeboten (%s, Einsatz %d).", kennung, waehrung.WETT_EINSATZ)


async def _wette_annehmen(bot, w: dict, automatisch: bool) -> None:
    tage = waehrung.wett_frist_tage(w.get("idee", ""))
    frist = waehrung.frist_iso(tage)
    einsatz = int(w.get("einsatz", waehrung.WETT_EINSATZ) or waehrung.WETT_EINSATZ)
    w.update({"status": "laeuft", "gestartet_am": kern._jetzt().isoformat(), "frist": frist,
              "angenommen": "automatisch" if automatisch else "tipp"})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
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
    w = dict(profil.get(kern.FELD_WETTE) or {})
    if w.get("kennung") != kennung:
        await query.message.reply_text(t("WETTE_ANTWORT_VERALTET"))
        return
    if w.get("status") != "angeboten":
        return  # Doppel-Tap / schon automatisch angenommen: still
    if aktion == "annehmen":
        await _wette_annehmen(context.bot, w, automatisch=False)
        return
    jetzt = kern._jetzt().isoformat()
    w.update({"status": "abgelehnt", "abgelehnt_am": jetzt, "letzte_aktion_am": jetzt,
              "abzug": None, "strafe_optionen": [], "strafe_neu": 0})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
    await _verlauf_eintragen(w, "abgelehnt")
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
