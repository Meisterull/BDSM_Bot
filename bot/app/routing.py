"""
Nachrichten-Routing: Text, Voice, Medien, falsche Rolle, Ausgangs-Logging.

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes, Application
from bot import commands_katalog as ck
from bot import config, state
from bot.services import paare, qdrant, telegram_helper
from bot.handlers import (
    safeword,
    domina,
    sklave,
    followup_response,
    reaktion,
    gefuehl,
    profil,
    aufgaben,
    vorlagen,
    stimmung,
    inspiration,
    training,
    stats,
    bewertung,
    rollenspiel,
    wochenplanung,
    wunsch,
    kommentar,
    geheimnis,
    wunschkategorien,
    privileg,
    quiz,
    coach_quiz,
    tiny_task_feedback,
    stille_checkin,
    skill,
    einstellungen,
    pairing,
    inventar,
    herrin_versaeumnis,
    waehrung as waehrung_h,
)
from bot.handlers import serie_handler
from bot.messages import t
from bot.app import gates

logger = logging.getLogger(__name__)



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = str(update.effective_chat.id)

    ctx = paare.resolve(chat_id)
    if ctx is None:
        if config.PAIRING_ENABLED:
            # Unbekannter Chat im Pairing-Flow (Rollenwahl/Code) oder Hinweis auf /start
            state.clear_if_stale(chat_id)
            if state.get_mode(chat_id) in pairing.PAIRING_MODES:
                await pairing.handle(update, context)
            else:
                await update.message.reply_text(t("PAIRING_HINWEIS_START"))
            return
        await update.message.reply_text(t("COMMON_NICHT_AUTORISIERT"))
        return
    paar, rolle = ctx

    # 1. Safeword (IMMER vor der Budget-Bremse – Safety geht vor Kosten)
    if await safeword.check_and_handle(update, context.bot):
        return

    # 1b. Missbrauchs-/Kosten-Bremse: Tages-Budget an Chat-Nachrichten pro Paar
    # (Env LLM_BUDGET_PRO_TAG, 0 = aus). Commands laufen nicht durch diesen
    # Handler und bleiben nutzbar; die Budget-Antwort selbst kostet kein LLM.
    if config.LLM_BUDGET_PRO_TAG > 0:
        if state.zaehle_tagesnachricht(paar.paar_id) > config.LLM_BUDGET_PRO_TAG:
            await update.message.reply_text(t("BUDGET_ERSCHOEPFT"))
            return

    # 2. State Check (zuvor: abgebrochene/liegengelassene UI-Flows auto-zurücksetzen)
    state.clear_if_stale(chat_id)
    mode = state.get_mode(chat_id)

    if mode in ("profil_wahl", "profil_eingabe"):
        await profil.handle(update, context)
        return

    if mode in ("vorlage_wahl", "vorlage_name", "vorlage_text"):
        await vorlagen.handle(update, context)
        return

    if mode in ("einstellungen_wahl", "einstellungen_eingabe"):
        await einstellungen.handle(update, context)
        return

    # Inventar-Pflege (/inventar) – beide Rollen, gleiche Paar-Listen
    if mode == inventar.MODE:
        await inventar.handle(update, context)
        return

    if rolle == paare.ROLLE_DOM:
        if mode == "wochenplanung_thema":
            await wochenplanung.handle(update, context)
            return
        if mode in ("inspiration_wahl", "inspiration_nummer", "inspiration_feedback"):
            await inspiration.handle(update, context)
            return
        if mode == "aufgabe_bestaetigung":
            await domina.handle(update, context)
            return
        if mode == "aufgabe_termin":
            await domina.handle_aufgabe_termin(update, context)
            return
        if mode == "serie_wahl":
            await serie_handler.handle_serie_wahl(update, context)
            return
        if mode == "reaktion_pending":
            await reaktion.handle(update, context)
            return
        if mode == "reaktion_alternativ":
            await reaktion.handle_alternativ(update, context)
            return
        if mode == waehrung_h.MODE_STRAFE_EIGEN:
            await waehrung_h.handle_strafe_eigen(update, context)
            return
        if mode == "aufgabe_loeschen":
            await aufgaben.handle_loeschen(update, context)
            return
        if mode == "training_antwort":
            await training.handle_antwort(update, context)
            return
        if mode == "aufgabe_bewertung":
            await bewertung.handle(update, context)
            return
        if mode in ("rollenspiel_wahl", "rollenspiel_intensitaet"):
            await rollenspiel.handle(update, context)
            return
        if mode == "rollenspiel_aktiv":
            # Stale-Fenster ab der LETZTEN Nachricht statt ab Szenario-Start –
            # sonst stirbt ein bespieltes Rollenspiel nach 30 Min (D9/M1).
            state.touch_mode(chat_id)
            await domina.handle(update, context)
            return
        if mode == "wunsch_entscheidung":
            await wunsch.handle_entscheidung(update, context)
            return
        if mode == "privileg_entscheidung":
            await privileg.handle_entscheidung(update, context)
            return
        if mode == "tiny_task_feedback":
            await tiny_task_feedback.handle(update, context)
            return
        if mode == stille_checkin.MODE:
            await stille_checkin.handle(update, context)
            return
        if mode == "aufgabe_kommentar":
            await kommentar.handle(update, context)
            return
        if mode in ("geheimnis_text", "geheimnis_datum"):
            await geheimnis.handle(update, context)
            return
        if mode == "kette_frage":
            await domina.handle_kette_frage(update, context)
            return
        if mode == "kette_aufgaben":
            await domina.handle_kette_aufgaben(update, context)
            return
        if mode == "skill_edit":
            await skill.handle_edit(update, context)
            return

    if rolle == paare.ROLLE_SUB:
        if mode == "followup":
            await followup_response.handle(update, context)
            return
        if mode == "gefuehl":
            await gefuehl.handle(update, context)
            return
        if mode == herrin_versaeumnis.MODE:
            await herrin_versaeumnis.handle(update, context)
            return
        if mode == "stimmung":
            await stimmung.handle_antwort(update, context)
            return
        if mode == "wunsch_eingabe":
            await wunsch.handle(update, context)
            return
        if mode == "wunschkategorien_wahl":
            await wunschkategorien.handle(update, context)
            return
        if mode == "privileg_wahl":
            await privileg.handle(update, context)
            return
        if mode == "frei_aufgabe_vorschlag":
            await privileg.handle_frei_aufgabe(update, context)
            return
        if mode == "quiz_antwort":
            await quiz.handle_antwort(update, context)
            return
        if mode == "coach_quiz_antwort":
            await coach_quiz.handle_antwort(update, context)
            return

    # 3. Normaler Chat
    if rolle == paare.ROLLE_DOM:
        await domina.handle(update, context)
    else:  # ROLLE_SUB (resolve() lässt nur die zwei Rollen durch)
        # Recovery (eval-6): Wartet ein Task auf eine Antwort (Status 'gefragt'),
        # hat der flüchtige State das Followup aber verloren (nach /abbrechen,
        # Neustart oder Mode-Wechsel), dann die Nachricht korrekt als Followup-
        # Antwort routen statt als Freitext – sonst antwortet die Herrin ohne
        # Aufgaben-Kontext und verdreht die Szene.
        sub_key = paar.user_id(paare.ROLLE_SUB)
        if mode == "chat" and await qdrant.count_tasks_by_status(["gefragt"], user_id=sub_key):
            # Count-Vorprüfung: der Recovery-Fall ist selten, der billige Count erspart
            # den Payload-Scroll bei jeder normalen Chat-Nachricht.
            gefragt = await qdrant.get_tasks_by_status(["gefragt"], user_id=sub_key)
            if gefragt:
                neuester = max(gefragt, key=lambda t: t.get("erteilt_am", ""))
                point_id = neuester.get("qdrant_point_id")
                if point_id:
                    state.set_followup_task(chat_id, point_id)
                    logger.info("Followup-State aus Qdrant wiederhergestellt (Task %s)", point_id)
                    await followup_response.handle(update, context)
                    return
        # Offene Stimmungsfrage (Merkmarke, kein Mode – s. handlers/stimmung):
        # die erste freie Nachricht im Fenster ist die Antwort darauf.
        if mode == "chat" and stimmung.wartet_auf_antwort(chat_id):
            await stimmung.handle_antwort(update, context)
            return
        await sklave.handle(update, context)


async def falsche_rolle_hinweis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Einheitlicher Hinweis, wenn eine Rolle einen Command der ANDEREN Rolle
    aufruft (Test-Befund F8: /stats antwortete, alle anderen blieben stumm).

    Läuft in group=1 NACH den eigentlichen Command-Handlern: deren Rollen-Guards
    returnen still, dieser Handler liefert dann den Hinweis. Für eigene Commands
    der richtigen Rolle greift keine der beiden Bedingungen – kein Doppel-Reply.
    Fremde Chats bleiben bewusst still (kein Leak, dass es den Bot-Setup gibt)."""
    msg = update.message
    if not msg or not msg.text:
        return
    ctx = paare.resolve(update.effective_chat.id)
    if ctx is None:
        return
    _, rolle = ctx
    cmd = msg.text.split()[0].lstrip("/").split("@")[0].lower()
    nur_domina = ck.alle_domina_commands() - ck.alle_sklave_commands()
    nur_sklave = ck.alle_sklave_commands() - ck.alle_domina_commands()
    if rolle == paare.ROLLE_SUB and (cmd in nur_domina or cmd.startswith("aufgaben_")):
        await msg.reply_text(t("COMMON_NICHT_FUER_DICH"))
    elif rolle == paare.ROLLE_DOM and cmd in nur_sklave:
        await msg.reply_text(t("COMMON_NICHT_FUER_DICH"))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sprachnachrichten: lokal transkribieren (wyoming-whisper, services/stt.py)
    und wie getippten Text durch die normale Pipeline schicken – inkl. Safeword-
    Check. Ohne STT-Konfiguration altes Verhalten (Medien-Weiterleitung)."""
    if not update.message or not update.message.voice:
        return
    from bot.services import stt
    if not stt.aktiv():
        await handle_media(update, context)
        return
    chat_id = str(update.effective_chat.id)
    if not paare.ist_autorisiert(chat_id):
        return
    if (update.message.voice.duration or 0) > config.STT_MAX_SEKUNDEN:
        await update.message.reply_text(t("VOICE_ZU_LANG", sekunden=config.STT_MAX_SEKUNDEN))
        return

    text = None
    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            f = await context.bot.get_file(update.message.voice.file_id)
            ogg = bytes(await f.download_as_bytearray())
            text = await stt.transcribe(ogg)
    except Exception:
        logger.exception("Voice-Transkription fehlgeschlagen")
    if not text:
        await update.message.reply_text(t("VOICE_NICHT_VERSTANDEN"))
        return

    # Echo des Verstandenen (wichtig fürs Vertrauen – gerade beim Safeword),
    # dann Transkript in die Text-Pipeline einspeisen. Message ist frozen →
    # kurz auftauen (gleiche Technik wie _wrap_outgoing_logging am Bot-Objekt).
    await update.message.reply_text(t("VOICE_VERSTANDEN", text=text))
    with update.message._unfrozen():
        update.message.text = text
    # „Telefonieren": Wer spricht, bekommt die Antwort auch gesprochen. Die
    # Chat-Handler (sklave/domina) lesen das Flag und hängen eine Voice-Bubble
    # an; finally räumt auf, damit getippte Folge-Nachrichten stumm bleiben.
    context.chat_data["voice_eingang"] = True
    try:
        await handle_message(update, context)
    finally:
        context.chat_data.pop("voice_eingang", None)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nicht-Text-Nachrichten (Foto, Video, Sprachnachricht, Dokument, Sticker, …)
    werden DIREKT an den jeweils anderen Chat durchgereicht – Telegram-seitig kopiert.
    Bewusst OHNE Speicherung, ohne Qdrant, ohne LLM (z.B. Beweisbilder Sklave→Herrin)."""
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    ctx = paare.resolve(chat_id)
    if ctx is None:
        return
    paar, rolle = ctx

    ziel = paar.partner_chat_id(chat_id)
    if rolle == paare.ROLLE_SUB:
        kopf = t("MEDIEN_VON_SKLAVE")
        bestaetigung = t("MEDIEN_AN_HERRIN_WEITERGELEITET")
    else:
        kopf = t("MEDIEN_VON_HERRIN")
        bestaetigung = t("MEDIEN_AN_SKLAVEN_WEITERGELEITET")

    try:
        await context.bot.send_message(chat_id=ziel, text=kopf)
        await context.bot.copy_message(
            chat_id=ziel,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        await update.message.reply_text(bestaetigung)
    except Exception:
        logger.exception("Medien-Weiterleitung fehlgeschlagen")
        try:
            await update.message.reply_text(t("MEDIEN_FEHLER"))
        except Exception:
            pass


def _wrap_outgoing_logging(application: Application) -> None:
    """Wrappt bot.send_message + copy_message, um JEDE ausgehende Nachricht zu loggen."""
    bot = application.bot
    _orig_send = bot.send_message
    _orig_copy = bot.copy_message

    async def _logged_send(*args, **kwargs):
        cid = kwargs.get("chat_id", args[0] if args else None)
        txt = kwargs.get("text", args[1] if len(args) > 1 else "")
        # Inhalt nur auf DEBUG (Logdatei/Logserver), nicht in die Docker-json-Logs.
        logger.info("GESENDET → %s | (%d Zeichen)", gates._rolle(cid), len(str(txt)))
        logger.debug("GESENDET-INHALT → %s | %s", gates._rolle(cid), str(txt).replace("\n", " ⏎ "))
        return await _orig_send(*args, **kwargs)

    async def _logged_copy(*args, **kwargs):
        cid = kwargs.get("chat_id", args[0] if args else None)
        logger.info("GESENDET → %s | [medien-weiterleitung]", gates._rolle(cid))
        return await _orig_copy(*args, **kwargs)

    # PTB friert die Bot-Attribute nach Init ein; _unfrozen() erlaubt das
    # legale Setzen von Instanz-Attributen (überschreibt die gebundenen Methoden).
    with bot._unfrozen():
        bot.send_message = _logged_send
        bot.copy_message = _logged_copy


async def app_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/app – Mini-App (Cockpit + Sprachnachrichten-Studio) im Chat öffnen.
    Der web_app-Knopf lädt MINIAPP_URL in Telegrams In-App-Ansicht; die Seite
    weist sich dort selbst über signierte initData aus (services/miniapp/)."""
    chat_id = str(update.effective_chat.id)
    if not paare.ist_autorisiert(chat_id):
        return
    if not (config.MINIAPP_PORT and config.MINIAPP_URL):
        await update.message.reply_text(t("MINIAPP_AUS"))
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        t("MINIAPP_KNOPF"), web_app=WebAppInfo(url=config.MINIAPP_URL))]])
    await update.message.reply_text(t("MINIAPP_OEFFNEN"), reply_markup=kb)
