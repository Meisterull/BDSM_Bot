"""
Herrin-Wette: Ablehnung, Strafwahl, eigene Strafe, Auto-Abschluss.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
import asyncio
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot import state
from bot.services import grok, limits_check, qdrant, telegram_helper, tts, waehrung
from bot.services import sticker_reaktionen
from bot.messages import t
from bot.handlers.waehrung import kern, wette_annahme

logger = logging.getLogger(__name__)



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


async def _nur_machbare(optionen: list[str]) -> tuple[list[str], list[str]]:
    """Machbarkeits-Prüfung je Strafe (parallel, services/machbarkeit).
    Rückgabe: (stimmige Strafen, Mängel der aussortierten – für den Retry-Prompt)."""
    from bot.services import machbarkeit
    ergebnisse = await asyncio.gather(*(machbarkeit.maengel(o, art="strafe") for o in optionen))
    gut = [o for o, m in zip(optionen, ergebnisse) if not m]
    maengel = [f"„{o[:80]}“: {m[0]}" for o, m in zip(optionen, ergebnisse) if m]
    return gut, maengel


async def _ablehnungs_strafen_generieren(idee: str) -> list[str]:
    """Drei kurze Strafen (Reasoning, Limits-Retry), jede einzeln auf körperliche
    Machbarkeit geprüft (18.09.2026). Bleiben weniger als zwei stimmige, gibt es
    EINEN neuen Satz mit der Mängelliste; gezeigt werden nur stimmige (höchstens
    drei). Leer = nichts Brauchbares – die Dom-Seite kann würfeln oder selbst schreiben."""
    roh = await _ablehnungs_strafen_roh(idee)
    if not roh:
        return []
    gut, maengel = await _nur_machbare(roh)
    if len(gut) >= 2 or not maengel:
        return gut[:3]
    logger.info("Strafvorschläge körperlich nicht stimmig (%s) – generiere einmal neu.", "; ".join(maengel))
    neu, _ = await _nur_machbare(await _ablehnungs_strafen_roh(idee, maengel))
    return (gut + [o for o in neu if o not in gut])[:3]


async def _ablehnungs_strafen_roh(idee: str, maengel: list[str] | None = None) -> list[str]:
    """Rohe Strafen-Liste aus dem LLM; `maengel` = Hinweise aus der Prüfung des ersten Satzes."""
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
    system, user = bestrafung.ablehnungs_strafen(
        idee, hl, sp.get("vorlieben", []) or [], sp.get("kategorie_reaktionen", {}) or {},
        letzte, sp.get("dossier", "") or "")
    if maengel:
        user += ("\n\nACHTUNG: Diese Strafen aus deinem letzten Entwurf waren körperlich nicht "
                 "stimmig – schlag andere, einfachere vor:\n" + "\n".join(f"- {m}" for m in maengel))
    roh = await limits_check.generate_mit_limit_retry(
        (system, user), sklave_hard_limits=hl, domina_grenzen=gr, reasoning=True, max_tokens=600)
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
    w = dict(profil.get(kern.FELD_WETTE) or {})
    if w.get("kennung") != kennung or w.get("status") != "abgelehnt":
        return
    w.update({"strafe_optionen": optionen, "letzte_aktion_am": kern._jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
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
                                  "datum": kern._jetzt().isoformat(), "status": "angeordnet"})
    except Exception:
        logger.exception("Strafen-Protokoll-Eintrag fehlgeschlagen")
    w.update({"status": "abgelehnt_erledigt", "strafe": strafe[:400], "strafe_task_id": task_id,
              "erledigt_am": kern._jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
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
    w = dict(profil.get(kern.FELD_WETTE) or {})
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
        w.update({"abzug": betrag, "letzte_aktion_am": kern._jetzt().isoformat(),
                  "dom_kopf": t("WETTE_ABLEHNUNG_ABZUG_DOM", sub_gross=wette_annahme._sub_gross(),
                                betrag=betrag, stand=buchung["neu"])})
        await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w['dom_kopf']}\n\n{t('WETTE_STRAFE_SUCHT')}", reply_markup=None)
        except Exception:
            pass
        try:
            await telegram_helper.send_sklave(context.bot, t("WETTE_ABZUG_SUB", betrag=betrag, stand=buchung["neu"]))
        except Exception:
            logger.exception("Abzug-Meldung an den Sub fehlgeschlagen")
        await kern.nach_punkteaenderung(context.bot, buchung["alt"], buchung["neu"], push=False)
        wette_annahme.im_hintergrund(_strafen_zeigen(context.bot, query, kennung, w.get("idee", "")))
        return

    if w.get("abzug") is None:
        return  # Strafwahl erst nach dem Abzug
    if aktion == "neu":
        if int(w.get("strafe_neu", 0) or 0) >= waehrung.WETT_STRAFE_MAX_NEU:
            await query.message.reply_text(t("WETTE_STRAFE_NEU_LIMIT"))
            return
        w.update({"strafe_neu": int(w.get("strafe_neu", 0) or 0) + 1, "letzte_aktion_am": kern._jetzt().isoformat()})
        await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w.get('dom_kopf', '')}\n\n{t('WETTE_STRAFE_SUCHT')}", reply_markup=None)
        except Exception:
            pass
        wette_annahme.im_hintergrund(_strafen_zeigen(context.bot, query, kennung, w.get("idee", "")))
        return
    if aktion == "keine":
        w.update({"status": "abgelehnt_erledigt", "strafe": None, "erledigt_am": kern._jetzt().isoformat()})
        await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
        try:
            await query.edit_message_text(f"{w.get('dom_kopf', '')}\n\n{t('WETTE_STRAFE_KEINE')}", reply_markup=None)
        except Exception:
            await query.message.reply_text(t("WETTE_STRAFE_KEINE"))
        logger.info("Ablehnung ohne Strafe abgeschlossen (%s).", kennung)
        return
    if aktion == "eigene":
        chat_id = str(update.effective_chat.id)
        state.set_mode(chat_id, wette_annahme.MODE_STRAFE_EIGEN)
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
    w = dict(profil.get(kern.FELD_WETTE) or {})
    if not kennung or w.get("kennung") != kennung or w.get("status") != "abgelehnt" or w.get("abzug") is None:
        await update.message.reply_text(t("WETTE_ABLEHNUNG_VERALTET"))
        return
    fehler = await _strafe_anordnen(context.bot, w, text[:400])
    if fehler:
        # nochmal schreiben lassen – Modus bleibt für den nächsten Versuch
        state.set_mode(chat_id, wette_annahme.MODE_STRAFE_EIGEN)
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
        await kern.nach_punkteaenderung(bot, buchung["alt"], buchung["neu"], push=False)
    w.update({"status": "abgelehnt_erledigt", "strafe": None, "automatisch": True,
              "erledigt_am": kern._jetzt().isoformat()})
    await qdrant.patch_profile_fields("sklave", {kern.FELD_WETTE: w})
    logger.info("Ablehnung automatisch abgeschlossen (%s, Abzug %s).", w.get("kennung"), w.get("abzug"))
