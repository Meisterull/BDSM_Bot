"""
Vanilla-Modus: Spaß + Planung für BEIDE Seiten (Etappe 3, 05.11.2026).

Ein Modul statt Rollen-Fädelung durch den BDSM-Stack: /meineaufgaben,
/wuerfel, /quiz, /wette, /wunsch, /privileg (Gutscheine), /stats, /termin,
/wochenplanung – alle für beide Slots, alle ohne Genehmigung durch die andere
Seite (Entscheidungen 05.11.2026). Punkte/Streak/Wette je Slot in
services/vanilla_punkte, Prompts in prompts/vanilla, Jobs in scheduler/vanilla.

Callback-Präfixe (beide Rollen, Paar-Gate in gates._callback_gate):
  vtask:<id>            Aufgabe aus /meineaufgaben abschließen → Nachfrage
  vfollow:ja|nein:<id>  Antwort auf die Nachfrage (auch vom Job)
  vwette:<einsatz>:<n>  Wette platzieren
  vwunsch:gern|spaeter:<id>  Antwort der anderen Seite auf einen Wunsch
  vgut:<id>:<n>         Gutschein einlösen
"""
import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare, modi, qdrant, grok, telegram_helper, limits_check, kategorie_logik
from bot.services import vanilla_punkte as vpkt
from bot.prompts import vanilla as vp
from bot.messages import t

logger = logging.getLogger(__name__)

MODE_QUIZ = "vquiz_antwort"
MODE_WUNSCH = "vwunsch_text"
MODE_TERMIN = "vtermin_text"
MODE_WOCHENPLAN = "vwochenplan_thema"
MODES = (MODE_QUIZ, MODE_WUNSCH, MODE_TERMIN, MODE_WOCHENPLAN)

_OFFENE_STATUS = ["offen", "gefragt"]
QUELLEN_AUFGABE = ("vanilla_impuls", "vanilla_wuerfel")


def _rolle(chat_id: str) -> str | None:
    ctx = paare.resolve(chat_id)
    return ctx[1] if ctx else None


def _darf(update: Update) -> str | None:
    """Rolle des Absenders, wenn Paar-Mitglied im Vanilla-Modus – sonst None."""
    if not modi.ist_vanilla():
        return None
    return _rolle(str(update.effective_chat.id))


async def _offene_aufgaben(rolle: str) -> list[dict]:
    tasks = await qdrant.get_tasks_by_status(_OFFENE_STATUS, user_id=rolle)
    return sorted([x for x in tasks if x.get("qdrant_point_id")], key=lambda x: x.get("erteilt_am", ""))


def follow_buttons(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ERLEDIGT"), callback_data=f"vfollow:ja:{task_id}"),
        InlineKeyboardButton(t("BUTTON_NICHT_ERLEDIGT"), callback_data=f"vfollow:nein:{task_id}"),
    ]])


async def _llm(system: str, user: str, fallback: str, max_tokens: int = 250) -> str:
    try:
        text = grok.clean_text(await grok.simple(user, system=system, max_tokens=max_tokens))
        return text or fallback
    except Exception as e:
        logger.error("Vanilla-LLM-Text fehlgeschlagen, Fallback: %s", e)
        return fallback


# ---------------------------------------------------------------- /meineaufgaben + Nachfrage

async def meineaufgaben(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    tasks = await _offene_aufgaben(rolle)
    if not tasks:
        await update.message.reply_text(t("VANILLA_AUFGABEN_KEINE"))
        return
    lines = [t("VANILLA_AUFGABEN_TITEL")]
    buttons = []
    for i, task in enumerate(tasks, 1):
        aufgabe = task.get("aufgabe", "")
        lines.append(f"{i}. " + (aufgabe if len(aufgabe) <= 80 else aufgabe[:77] + "…"))
        buttons.append([InlineKeyboardButton(t("BUTTON_NR_ABSCHLIESSEN", nr=i),
                                             callback_data=f"vtask:{task['qdrant_point_id']}")])
    await telegram_helper.reply_markdown_safe(update.message, "\n".join(lines),
                                              reply_markup=InlineKeyboardMarkup(buttons))


async def nachfrage_senden(bot, rolle: str, task: dict, reply=None) -> bool:
    """Nachfrage zu einer Aufgabe in der Stimme + ✅/❌ (Job und /meineaufgaben).
    Kein Chat-Mode: Ein-Tipp genügt, der Chat bleibt frei."""
    point_id = task.get("qdrant_point_id")
    profil = await qdrant.get_user_profile(rolle) or {}
    erteilt = task.get("erteilt_am", "")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.TIMEZONE)
        d = datetime.fromisoformat(erteilt) if erteilt else datetime.now(tz)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        tage = (datetime.now(tz).date() - d.astimezone(tz).date()).days
        tage_her = tage if 0 <= tage <= 30 else 1
    except (ValueError, TypeError):
        tage_her = 1
    system, user = vp.followup_frage(rolle, task.get("aufgabe", ""), tage_her, int(profil.get("streak", 0) or 0))
    frage = await _llm(system, user, t("VANILLA_FOLLOWUP_FALLBACK", aufgabe=task.get("aufgabe", "")[:200]))
    chat_id = paare.paar_im_kontext().chat_id(rolle)
    if reply is not None:
        await reply(frage, reply_markup=follow_buttons(point_id))
    else:
        await bot.send_message(chat_id=chat_id, text=frage, reply_markup=follow_buttons(point_id))
    await qdrant.update_task(point_id, {"status": "gefragt"})
    state.add_message(chat_id, "assistant", frage)
    return True


async def _callback_task(query, rolle: str, point_id: str) -> None:
    task = await qdrant.get_task(point_id)
    if not task or task.get("status") not in _OFFENE_STATUS or task.get("user_id") != qdrant.mandanten_key(rolle):
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(t("MEINEAUFGABEN_NICHT_OFFEN"))
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await nachfrage_senden(None, rolle, task, reply=query.message.reply_text)


async def aufgabe_abschliessen(bot, rolle: str, point_id: str, ja: bool) -> dict:
    """Kern für Knopf UND Mini-App: Aufgabe der Person abschließen, Punkte/Wette
    buchen, Reaktion der Stimme in den Chat. Rückgabe {fehler} oder
    {ok, reaktion, zeile, verloren, rang_neu, punkte, streak}."""
    task = await qdrant.get_task(point_id)
    if not task or task.get("user_id") != qdrant.mandanten_key(rolle):
        return {"fehler": t("COMMON_TASK_NICHT_GEFUNDEN")}
    if task.get("status") not in _OFFENE_STATUS:
        return {"fehler": t("MEINEAUFGABEN_NICHT_OFFEN")}
    aufgabe = task.get("aufgabe", "")
    paar = paare.paar_im_kontext()
    chat_id = paar.chat_id(rolle)
    if ja:
        await qdrant.update_task(point_id, {"status": "erledigt", "erledigt_am": datetime.now(timezone.utc).isoformat()})
        ergebnis = await vpkt.erledigt(rolle, task)
        system, user = vp.reaktion_erledigt(rolle, aufgabe)
        reaktion = await _llm(system, user, t("VANILLA_ERLEDIGT_FALLBACK"))
        boni = " + ".join(f"{n} {p}" for n, p in ergebnis["boni"])
        zeile = t("VANILLA_ERLEDIGT_PUNKTE", plus=ergebnis["punkte"] - ergebnis["alt"], boni=boni,
                  stand=ergebnis["punkte"], streak=ergebnis["streak"])
        await telegram_helper.send_an(bot, paar, rolle, reaktion + "\n\n" + zeile)
        state.add_message(chat_id, "assistant", reaktion)
        alt_rang, neu_rang = vpkt.rang(ergebnis["alt"]), vpkt.rang(ergebnis["punkte"])
        if neu_rang != alt_rang:
            await telegram_helper.send_an(bot, paar, rolle, t("VANILLA_RANG_AUF", rang=neu_rang), parse_mode="Markdown")
        return {"ok": True, "reaktion": reaktion, "zeile": zeile, "verloren": 0,
                "rang_neu": neu_rang if neu_rang != alt_rang else "",
                "punkte": ergebnis["punkte"], "streak": ergebnis["streak"]}
    await qdrant.update_task(point_id, {"status": "nicht_erledigt"})
    verloren = await vpkt.nicht_erledigt(rolle)
    system, user = vp.reaktion_nicht_erledigt(rolle, aufgabe)
    reaktion = await _llm(system, user, t("VANILLA_NICHT_ERLEDIGT_FALLBACK"))
    if verloren:
        reaktion += "\n\n" + t("VANILLA_WETTE_VERLOREN", einsatz=verloren)
    await telegram_helper.send_an(bot, paar, rolle, reaktion)
    state.add_message(chat_id, "assistant", reaktion)
    return {"ok": True, "reaktion": reaktion, "zeile": "", "verloren": verloren, "rang_neu": ""}


async def _callback_follow(query, bot, rolle: str, action: str, point_id: str) -> None:
    await query.edit_message_reply_markup(reply_markup=None)
    ergebnis = await aufgabe_abschliessen(bot, rolle, point_id, action == "ja")
    if ergebnis.get("fehler") and ergebnis["fehler"] == t("MEINEAUFGABEN_NICHT_OFFEN"):
        await query.message.reply_text(ergebnis["fehler"])


# ---------------------------------------------------------------- /wuerfel

async def wuerfel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Würfelt eine Kategorie und schickt sofort eine Überraschungs-Idee für die
    eigene Person – ohne Freigabe, direkt als Aufgabe."""
    rolle = _darf(update)
    if not rolle:
        return
    andere = vp.andere_rolle(rolle)
    profil = await qdrant.get_user_profile(rolle) or {}
    partner = await qdrant.get_user_profile(andere) or {}
    pool = kategorie_logik.katalog_kategorien()
    kandidaten = random.sample(pool, k=min(6, len(pool)))
    try:
        dice_msg = await update.message.reply_dice(emoji="🎲")
        wert = dice_msg.dice.value
        await asyncio.sleep(4)
        kategorie = kandidaten[(wert - 1) % len(kandidaten)]
        await update.message.reply_text(
            t("VANILLA_WUERFEL_GEFALLEN", wert=wert, kategorie=kategorie_logik.anzeige_name(kategorie)),
            parse_mode="Markdown")
    except Exception as e:
        logger.warning("reply_dice fehlgeschlagen, Zufall ohne Animation: %s", e)
        kategorie = random.choice(pool)
    ergebnis = await wuerfel_idee(context.bot, rolle, kategorie)
    if ergebnis.get("fehler"):
        await update.message.reply_text(t("WUERFEL_FEHLER"))


async def wuerfel_idee(bot, rolle: str, kategorie: str | None = None) -> dict:
    """Kern für /wuerfel und Mini-App: Überraschungs-Idee aus `kategorie` (sonst
    Zufall), als Aufgabe angelegt und in den Chat der Person geschickt."""
    andere = vp.andere_rolle(rolle)
    profil = await qdrant.get_user_profile(rolle) or {}
    partner = await qdrant.get_user_profile(andere) or {}
    if not kategorie:
        kategorie = random.choice(kategorie_logik.katalog_kategorien())
    from bot.scheduler import vanilla as sv
    letzte = await sv.letzte_impulse(rolle)
    system, prompt = vp.impuls(rolle, [kategorie], profil, partner, letzte_impulse=letzte)
    system += "\n\nWÜRFEL-CHARAKTER: Die Idee darf ein bisschen mutiger und überraschender sein als sonst – ein kleines Kribbeln."
    text = await limits_check.generate_mit_limit_retry(
        prompt, vp.no_gos(profil, rolle), vp.no_gos(partner, andere), system=system)
    if not text:
        return {"fehler": t("WUERFEL_FEHLER")}
    await qdrant.erstelle_task(text, kategorie, 1, quelle="vanilla_wuerfel", user_id=rolle,
                               extra={"erteilt_von": "stimme"})
    paar = paare.paar_im_kontext()
    await telegram_helper.send_an(bot, paar, rolle, t("VANILLA_WUERFEL_PREFIX") + text)
    state.add_message(paar.chat_id(rolle), "assistant", text)
    logger.info("Vanilla-Würfel (%s): Kategorie %s", rolle, kategorie)
    return {"ok": True, "kategorie": kategorie_logik.anzeige_name(kategorie), "text": text}


# ---------------------------------------------------------------- /quiz

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    chat_id = str(update.effective_chat.id)
    if state.get_mode(chat_id) != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return
    andere = vp.andere_rolle(rolle)
    partner = await qdrant.get_user_profile(andere) or {}
    kontext = vp.quiz_kontext(partner, andere)
    if len(kontext) < 20:
        await update.message.reply_text(t("VANILLA_QUIZ_ZU_WENIG"))
        return
    s = state.get(chat_id)
    letzte = s.get("quiz_letzte_fragen", [])
    system, user = vp.quiz_frage(rolle, kontext, letzte)
    try:
        daten = grok.parse_json(await grok.simple(user, system=system, temperature=0.7))
        frage = (daten.get("frage") or "").strip()
        muster = (daten.get("antwort") or "").strip()
        if not frage or not muster:
            raise ValueError("leer")
    except Exception:
        logger.exception("Vanilla-Quiz-Generierung fehlgeschlagen")
        await update.message.reply_text(t("QUIZ_FEHLER"))
        return
    s["quiz_letzte_fragen"] = (letzte + [frage])[-5:]
    s["quiz_frage"], s["quiz_musterantwort"] = frage, muster
    state.set_mode(chat_id, MODE_QUIZ)
    await update.message.reply_text(t("VANILLA_QUIZ_FRAGE", frage=frage), parse_mode="Markdown")


async def quiz_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    rolle = _rolle(chat_id)
    s = state.get(chat_id)
    text = update.message.text.strip()
    frage, muster = s.pop("quiz_frage", ""), s.pop("quiz_musterantwort", "")
    state.set_mode(chat_id, "chat")
    if text.lower() in ("/abbrechen", "abbrechen"):
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return
    if not frage or not muster or not rolle:
        return
    urteil = "FALSCH"
    try:
        bewertung = await grok.simple(
            f'Frage: "{frage}"\nMusterantwort: "{muster}"\nAntwort: """{text}"""',
            system=("Bewerte, ob die Antwort inhaltlich zur Musterantwort passt. Sei fair: andere "
                    "Formulierung mit gleichem Kern = RICHTIG; teilweise getroffen = TEILWEISE; "
                    "daneben = FALSCH. Antworte NUR mit einem Wort: RICHTIG, TEILWEISE oder FALSCH."),
            temperature=0)
        wort = (bewertung or "").strip().upper()
        if "RICHTIG" in wort and "TEILWEISE" not in wort:
            urteil = "RICHTIG"
        elif "TEILWEISE" in wort:
            urteil = "TEILWEISE"
    except Exception:
        logger.exception("Vanilla-Quiz-Bewertung fehlgeschlagen – TEILWEISE")
        urteil = "TEILWEISE"
    punkte = {"RICHTIG": vpkt.QUIZ_RICHTIG, "TEILWEISE": vpkt.QUIZ_TEILWEISE, "FALSCH": 0}[urteil]
    if punkte:
        await vpkt.buchen(rolle, punkte, f"Quiz {urteil}")
    system, user = vp.quiz_reaktion(rolle, urteil, frage, muster, text)
    reaktion = await _llm(system, user, "", max_tokens=150)
    await update.message.reply_text(
        t(f"QUIZ_{urteil}", punkte=punkte, antwort=telegram_helper.md_einbett_sicher(muster)),
        parse_mode="Markdown")
    if reaktion:
        await update.message.reply_text(reaktion)


# ---------------------------------------------------------------- /wette

async def wette(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    chat_id = str(update.effective_chat.id)
    profil = await qdrant.get_user_profile(rolle) or {}
    punkte = int(profil.get("punkte", 0) or 0)
    if (profil.get("vanilla_wette") or {}).get("einsatz"):
        await update.message.reply_text(
            t("WETTE_SCHON_AKTIV", einsatz=profil["vanilla_wette"]["einsatz"]), parse_mode="Markdown")
        return
    if not await _offene_aufgaben(rolle):
        await update.message.reply_text(t("VANILLA_WETTE_KEINE_AUFGABE"))
        return
    moeglich = [e for e in vpkt.WETT_EINSAETZE if e <= punkte]
    if not moeglich:
        await update.message.reply_text(
            t("WETTE_ZU_WENIG_PUNKTE", punkte=punkte, minimum=vpkt.WETT_EINSAETZE[0]))
        return
    nonce = uuid.uuid4().hex[:8]
    state.get(chat_id)["vwette_nonce"] = nonce
    buttons = [[InlineKeyboardButton(t("BUTTON_WETTE_EINSATZ", punkte=e), callback_data=f"vwette:{e}:{nonce}")
                for e in moeglich]]
    await update.message.reply_text(t("VANILLA_WETTE_ANGEBOT", punkte=punkte), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(buttons))


async def _callback_wette(query, bot, rolle: str, einsatz_s: str, nonce: str) -> None:
    chat_id = str(query.message.chat_id)
    s = state.get(chat_id)
    await query.edit_message_reply_markup(reply_markup=None)
    if nonce != s.get("vwette_nonce") or not einsatz_s.isdigit():
        await query.message.reply_text(t("WETTE_STATE_WEG"))
        return
    s.pop("vwette_nonce", None)
    ergebnis = await wette_platzieren(bot, rolle, int(einsatz_s))
    if ergebnis.get("fehler"):
        await query.message.reply_text(ergebnis["fehler"])


async def wette_platzieren(bot, rolle: str, einsatz: int) -> dict:
    """Kern für Knopf und Mini-App: Wette setzen, Bestätigung + Reaktion in den Chat."""
    if not await _offene_aufgaben(rolle):
        return {"fehler": t("VANILLA_WETTE_KEINE_AUFGABE")}
    ergebnis = await vpkt.wette_setzen(rolle, int(einsatz))
    if not ergebnis:
        return {"fehler": t("WETTE_STATE_WEG")}
    paar = paare.paar_im_kontext()
    await telegram_helper.send_an(bot, paar, rolle,
                                  t("WETTE_PLATZIERT", einsatz=ergebnis["einsatz"], rest=ergebnis["rest"]),
                                  parse_mode="Markdown")
    system, user = vp.wette_reaktion(rolle, ergebnis["einsatz"])
    reaktion = await _llm(system, user, "", max_tokens=150)
    if reaktion:
        await telegram_helper.send_an(bot, paar, rolle, reaktion)
    return {"ok": True, "einsatz": ergebnis["einsatz"], "rest": ergebnis["rest"], "reaktion": reaktion}


# ---------------------------------------------------------------- /wunsch

async def wunsch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    chat_id = str(update.effective_chat.id)
    if state.get_mode(chat_id) != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return
    state.set_mode(chat_id, MODE_WUNSCH)
    partner = vp.person(vp.andere_rolle(rolle))
    await update.message.reply_text(
        t("VANILLA_WUNSCH_FRAGE", partner=partner["anrede"] or ("deine Partnerin" if partner["fem"] else "dein Partner")),
        parse_mode="Markdown")


async def wunsch_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    rolle = _rolle(chat_id)
    text = update.message.text.strip()
    state.set_mode(chat_id, "chat")
    if text.lower() in ("/abbrechen", "abbrechen") or not rolle:
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return
    ergebnis = await wunsch_senden(context.bot, rolle, text)
    await update.message.reply_text(ergebnis.get("fehler") or t("VANILLA_WUNSCH_GESENDET"))


async def wunsch_senden(bot, rolle: str, text: str) -> dict:
    """Kern für /wunsch und Mini-App: Wunsch speichern und mit Gern/Später-Knöpfen
    an die andere Seite schicken."""
    text = (text or "").strip()
    if len(text) < 3:
        return {"fehler": t("VANILLA_WUNSCH_ZU_KURZ")}
    andere = vp.andere_rolle(rolle)
    point_id = await qdrant.save_wunsch(rolle, {
        "text": text[:1000], "status": "offen", "an": andere,
        "datum": datetime.now(timezone.utc).isoformat(),
    })
    ich = vp.person(rolle)
    von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WUNSCH_GERN"), callback_data=f"vwunsch:gern:{point_id}"),
        InlineKeyboardButton(t("BUTTON_WUNSCH_SPAETER"), callback_data=f"vwunsch:spaeter:{point_id}"),
    ]])
    await telegram_helper.send_an(
        bot, paare.paar_im_kontext(), andere,
        t("VANILLA_WUNSCH_AN_PARTNER", von=telegram_helper.escape_md(von),
          text=telegram_helper.md_einbett_sicher(text)),
        parse_mode="Markdown", reply_markup=buttons)
    logger.info("Vanilla-Wunsch (%s → %s) gesendet.", rolle, andere)
    return {"ok": True}


async def _callback_wunsch(query, bot, rolle: str, entscheidung: str, point_id: str) -> None:
    w = await qdrant.get_wunsch(point_id)
    await query.edit_message_reply_markup(reply_markup=None)
    if not w or w.get("an") != rolle or w.get("status") != "offen":
        return
    await qdrant.update_wunsch(point_id, {"status": entscheidung,
                                          "entschieden_am": datetime.now(timezone.utc).isoformat()})
    absender = vp.andere_rolle(rolle)
    ich = vp.person(rolle)
    von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
    await query.message.reply_text(t(f"VANILLA_WUNSCH_ENTSCHIEDEN_{entscheidung.upper()}"))
    await telegram_helper.send_an(
        bot, paare.paar_im_kontext(), absender,
        t(f"VANILLA_WUNSCH_ANTWORT_{entscheidung.upper()}", von=telegram_helper.escape_md(von),
          text=telegram_helper.md_einbett_sicher(w.get("text", "")[:300])),
        parse_mode="Markdown")


# ---------------------------------------------------------------- /privileg = Gutscheine

async def gutscheine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    chat_id = str(update.effective_chat.id)
    profil = await qdrant.get_user_profile(rolle) or {}
    punkte = int(profil.get("punkte", 0) or 0)
    nonce = uuid.uuid4().hex[:8]
    state.get(chat_id)["vgut_nonce"] = nonce
    zeilen = [t("VANILLA_GUTSCHEINE_TITEL", punkte=punkte)]
    buttons = []
    for g in vpkt.GUTSCHEINE:
        ok = punkte >= g["kosten"]
        zeilen.append(f"{'🎟' if ok else '🔒'} *{g['name']}* – {g['kosten']} Punkte\n_{g['beschreibung']}_")
        if ok:
            buttons.append([InlineKeyboardButton(f"🎟 {g['name']} ({g['kosten']})",
                                                 callback_data=f"vgut:{g['id']}:{nonce}")])
    await telegram_helper.reply_markdown_safe(update.message, "\n\n".join(zeilen),
                                              reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def _callback_gutschein(query, bot, rolle: str, gid: str, nonce: str) -> None:
    chat_id = str(query.message.chat_id)
    s = state.get(chat_id)
    await query.edit_message_reply_markup(reply_markup=None)
    g = vpkt.gutschein(gid)
    if nonce != s.get("vgut_nonce") or not g:
        await query.message.reply_text(t("WETTE_STATE_WEG"))
        return
    s.pop("vgut_nonce", None)
    ergebnis = await gutschein_einloesen(bot, rolle, gid)
    if ergebnis.get("fehler"):
        await query.message.reply_text(ergebnis["fehler"])


async def gutschein_einloesen(bot, rolle: str, gid: str) -> dict:
    """Kern für Knopf und Mini-App: Punkte abziehen, eigene Bestätigung in den
    Chat, Hinweis in der Stimme an die andere Seite."""
    g = vpkt.gutschein(gid)
    if not g:
        return {"fehler": t("WETTE_STATE_WEG")}
    profil = await qdrant.get_user_profile(rolle) or {}
    if int(profil.get("punkte", 0) or 0) < g["kosten"]:
        return {"fehler": t("VANILLA_GUTSCHEIN_ZU_WENIG")}
    ergebnis = await vpkt.buchen(rolle, -g["kosten"], f"Gutschein {gid}")
    await qdrant.patch_profile_fields(rolle, {
        "gutscheine_eingeloest": int(profil.get("gutscheine_eingeloest", 0) or 0) + 1})
    paar = paare.paar_im_kontext()
    await telegram_helper.send_an(bot, paar, rolle,
                                  t("VANILLA_GUTSCHEIN_EINGELOEST", name=g["name"], stand=ergebnis["neu"]),
                                  parse_mode="Markdown")
    andere = vp.andere_rolle(rolle)
    system, user = vp.gutschein_hinweis(rolle, g)
    hinweis = await _llm(system, user, "", max_tokens=200)
    ich = vp.person(rolle)
    von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
    await telegram_helper.send_an(
        bot, paar, andere,
        t("VANILLA_GUTSCHEIN_AN_PARTNER", von=telegram_helper.escape_md(von), name=g["name"],
          beschreibung=g["beschreibung"], hinweis=hinweis),
        parse_mode="Markdown")
    logger.info("Vanilla-Gutschein %s eingelöst (%s).", gid, rolle)
    return {"ok": True, "name": g["name"], "stand": ergebnis["neu"]}


# ---------------------------------------------------------------- /stats

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    profil = await qdrant.get_user_profile(rolle) or {}
    punkte = int(profil.get("punkte", 0) or 0)
    erledigt = await qdrant.get_completed_task_count(rolle)
    naechster = vpkt.naechster_rang(punkte)
    text = t("VANILLA_STATS", punkte=punkte, rang=vpkt.rang(punkte),
             naechster=(f"{naechster[1]} ab {naechster[0]}" if naechster else "—"),
             streak=int(profil.get("streak", 0) or 0), streak_max=int(profil.get("streak_max", 0) or 0),
             erledigt=erledigt, gutscheine=int(profil.get("gutscheine_eingeloest", 0) or 0))
    w = profil.get("vanilla_wette") or {}
    if w.get("einsatz"):
        text += "\n" + t("VANILLA_STATS_WETTE", einsatz=w["einsatz"])
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------- /termin

async def termin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        if paare.ist_autorisiert(str(update.effective_chat.id)):
            await update.message.reply_text(t("COMMON_NUR_VANILLA"))
        return
    chat_id = str(update.effective_chat.id)
    text = " ".join(context.args or []).strip()
    if text:
        await _termin_anlegen(update, context, rolle, text)
        return
    if state.get_mode(chat_id) != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return
    state.set_mode(chat_id, MODE_TERMIN)
    await update.message.reply_text(t("VANILLA_TERMIN_FRAGE"), parse_mode="Markdown")


async def termin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    rolle = _rolle(chat_id)
    text = update.message.text.strip()
    state.set_mode(chat_id, "chat")
    if text.lower() in ("/abbrechen", "abbrechen") or not rolle:
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return
    await _termin_anlegen(update, context, rolle, text)


async def _termin_anlegen(update: Update, context, rolle: str, text: str) -> None:
    ergebnis = await termin_anlegen(context.bot, rolle, text)
    if ergebnis.get("fehler"):
        await update.message.reply_text(ergebnis["fehler"], parse_mode="Markdown")
        return
    await update.message.reply_text(t("VANILLA_TERMIN_GESPEICHERT", termin=ergebnis["termin"]), parse_mode="Markdown")


async def termin_anlegen(bot, rolle: str, text: str) -> dict:
    """Kern für /termin und Mini-App: Datum erkennen, als geplante Aufgabe des
    Slots speichern (Erinnerung an beide via vanilla_termin_job), Partner informieren."""
    from bot.services import datum_erkennung, persona_config
    from zoneinfo import ZoneInfo
    treffer = datum_erkennung.finde_termin(text)
    if not treffer:
        return {"fehler": t("VANILLA_TERMIN_UNKLAR")}
    datum, ausdruck = treffer
    beschreibung = text.replace(ausdruck, "").strip(" ,:–-") or text
    tz = ZoneInfo(config.TIMEZONE)
    h, m = config.hm(persona_config.zeit("termin_zustellung_time"))
    zustellung = datetime(datum.year, datum.month, datum.day, h, m, tzinfo=tz)
    if zustellung <= datetime.now(tz):
        zustellung = datetime.now(tz)
    await qdrant.erstelle_task(
        beschreibung[:500], "Date_Night", 1, status="geplant", quelle="vanilla_termin", user_id=rolle,
        extra={"zustellung_ab": zustellung.astimezone(timezone.utc).isoformat(),
               "termin_datum": datum.isoformat(), "erteilt_von": rolle})
    wann = datum_erkennung.format_termin(datum)
    ich = vp.person(rolle)
    von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
    try:
        await telegram_helper.send_an(
            bot, paare.paar_im_kontext(), vp.andere_rolle(rolle),
            t("VANILLA_TERMIN_AN_PARTNER", von=telegram_helper.escape_md(von), termin=wann,
              text=telegram_helper.md_einbett_sicher(beschreibung[:300])),
            parse_mode="Markdown")
    except Exception:
        logger.exception("Termin-Hinweis an Partner nicht zustellbar")
    return {"ok": True, "termin": wann, "datum": datum.isoformat(), "text": beschreibung[:300]}


# ---------------------------------------------------------------- /wochenplanung

async def wochenplanung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rolle = _darf(update)
    if not rolle:
        return
    chat_id = str(update.effective_chat.id)
    if state.get_mode(chat_id) != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return
    state.set_mode(chat_id, MODE_WOCHENPLAN)
    await update.message.reply_text(t("VANILLA_WOCHENPLAN_THEMA_FRAGE"), parse_mode="Markdown")


async def wochenplan_thema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    state.set_mode(chat_id, "chat")
    if text.lower() in ("/abbrechen", "abbrechen"):
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return
    thema = "" if text.lower() in ("-", "kein", "keins", "frei", "egal") else text
    await update.message.reply_text(t("WOCHENPLAN_WARTE"))
    from bot.scheduler import vanilla as sv
    if not await sv.wochenplan_senden(context.bot, thema=thema):
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))


# ---------------------------------------------------------------- Callback-Dispatcher

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    rolle = _rolle(chat_id)
    if not rolle or not modi.ist_vanilla():
        return
    parts = (query.data or "").split(":")
    try:
        if parts[0] == "vtask" and len(parts) == 2:
            await _callback_task(query, rolle, parts[1])
        elif parts[0] == "vfollow" and len(parts) == 3:
            await _callback_follow(query, context.bot, rolle, parts[1], parts[2])
        elif parts[0] == "vwette" and len(parts) == 3:
            await _callback_wette(query, context.bot, rolle, parts[1], parts[2])
        elif parts[0] == "vwunsch" and len(parts) == 3 and parts[1] in ("gern", "spaeter"):
            await _callback_wunsch(query, context.bot, rolle, parts[1], parts[2])
        elif parts[0] == "vgut" and len(parts) == 3:
            await _callback_gutschein(query, context.bot, rolle, parts[1], parts[2])
    except Exception:
        logger.exception("Vanilla-Callback %s fehlgeschlagen", parts[0] if parts else "?")
