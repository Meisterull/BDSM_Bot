"""
BDSM Coach Bot – Entry Point
"""
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes, Application, TypeHandler,
    ApplicationHandlerStop,
)
from telegram.error import NetworkError, TimedOut, RetryAfter, BadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import commands_katalog as ck
from bot import config, state
from bot.services import paare, qdrant, telegram_helper
from bot.handlers import (
    safeword, domina, sklave, followup_response,
    reaktion, gefuehl, profil, aufgaben, vorlagen,
    stimmung, inspiration, ziele, rueckblick, training,
    stats, bewertung, rollenspiel, wochenplanung,
    wunsch, kommentar, geheimnis, strafen_protokoll, tinytask,
    wuerfel, wunschkategorien, privileg, wette, blitz, arc, event_arc, roulette, dauer, quiz, advent, tiny_task_feedback, hilfe, resurface,
    lerntagebuch, coach_regeln, skill, kette_adaptiv, dossier, namen, meine_aufgaben,
    einstellungen, luecke, pairing, admin, abwesenheit,
)


from bot.handlers import serie_handler
from bot.scheduler.followup import (
    followup_job, stimmung_job, ziel_erinnerung_job, training_job,
    lernkurve_job, rollenspiel_vorschlag_job, wochenplanung_job, geheimnis_job,
    kommentar_analyse_job, training_erinnerung_job, tiny_task_feedback_job,
    tiny_task_vorschlag_job, resurface_job, lerntagebuch_job,
    coach_reflexion_job, profil_pflege_job, backup_job, sklave_dossier_job,
    offene_faeden_job, luecken_check_job, luecken_zustellung_job,
    blitz_check_job, blitz_ablauf_job, event_check_job, dauer_job, kalender_job,
)
from bot.handlers.profil import abbrechen_command
from bot.messages import t

def _setup_logging() -> None:
    """Console (INFO) + rotierende Logdatei (DEBUG) – komplettes Logging zur Analyse."""
    import os
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    handlers = [console]
    try:
        os.makedirs(os.path.dirname(config.LOG_FILE) or ".", exist_ok=True)
        fileh = RotatingFileHandler(
            config.LOG_FILE, maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        fileh.setLevel(logging.DEBUG)
        fileh.setFormatter(fmt)
        handlers.append(fileh)
        # Logdatei enthält intime Inhalte (DEBUG) → nur für den Bot-User lesbar.
        try:
            os.chmod(config.LOG_FILE, 0o600)
        except OSError:
            pass
    except Exception as e:
        console.handle(logging.makeLogRecord(
            {"msg": f"Logdatei nicht initialisierbar: {e}", "levelno": logging.ERROR, "levelname": "ERROR", "name": "logging"}))
    root.handlers = handlers
    # Laute Bibliotheken dämpfen, damit GESENDET/EMPFANGEN/Fehler/LLM-Calls
    # nicht im httpcore-DEBUG-Spam untergehen (sonst rotiert die Logdatei in Minuten).
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Eigene Module bleiben gesprächig (Aufrufe, LLM-Calls zur Analyse).
    logging.getLogger("bot").setLevel(logging.DEBUG)
    logging.getLogger("bot.services.grok").setLevel(logging.DEBUG)


_setup_logging()
logger = logging.getLogger(__name__)


def _rolle(chat_id) -> str:
    ctx = paare.resolve(chat_id)
    if ctx is None:
        return f"chat:{chat_id}"
    paar, rolle = ctx
    name = "DOMINA" if rolle == paare.ROLLE_DOM else "SKLAVE"
    # Beim Legacy-Paar bleibt das Log-Format wie bisher; weitere Paare
    # bekommen die paar_id als Suffix, damit Logs eindeutig bleiben.
    if paar.paar_id != paare.LEGACY_PAAR_ID:
        name += f"[{paar.paar_id}]"
    return name


async def paar_kontext_setzen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Setzt den Paar-Kontext für dieses Update (group=-3, VOR allem anderen).

    Alle nachgelagerten Handler desselben Updates laufen im selben async-Task,
    sehen also diesen Kontext – so lösen die synchronen persona_config-Getter
    tief in den Prompt-Buildern den richtigen Mandanten auf, ohne dass jede
    Signatur einen Paar-Parameter braucht. IMMER setzen (unautorisierte Chats
    → Legacy-Default): ohne concurrent_updates laufen aufeinanderfolgende
    Updates im SELBEN Task, ein alter Kontext darf nicht ins nächste Update
    lecken."""
    try:
        ctx = paare.resolve(update.effective_chat.id) if update.effective_chat else None
        paare.set_kontext(ctx[0].paar_id if ctx else paare.LEGACY_PAAR_ID)
    except Exception:
        logger.exception("paar_kontext_setzen fehlgeschlagen")


async def log_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loggt JEDE eingehende Nachricht/Callback zentral (group=-2, blockiert nicht)."""
    try:
        if update.callback_query:
            logger.info("EMPFANGEN ← %s | [button] %s",
                        _rolle(update.effective_chat.id), update.callback_query.data)
        elif update.message:
            if update.message.text:
                inhalt = update.message.text
            else:
                inhalt = "[medien/non-text]"
            # Nachrichten-Inhalt nur auf DEBUG → bleibt in der (0600-)Logdatei für die
            # Analyse, landet aber NICHT in den Docker-json-Logs (docker logs).
            logger.info("EMPFANGEN ← %s | (%d Zeichen)", _rolle(update.effective_chat.id), len(inhalt))
            logger.debug("EMPFANGEN-INHALT ← %s | %s", _rolle(update.effective_chat.id), inhalt)
    except Exception:
        logger.exception("log_incoming fehlgeschlagen")

async def pause_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zentraler Safeword-Pause-Guard (group=-1, VOR allen Handlern).

    Blockiert während der Pause Commands, Inline-Button-Callbacks und Medien –
    sonst könnten z.B. liegengebliebene Würfel-/Wochenplan-Buttons trotz Pause
    neue Aufgaben an den Sklaven senden. Normale Text-Nachrichten laufen durch,
    damit safeword.check_and_handle das RESUME-Wort verarbeiten und den
    Pausen-Hinweis senden kann."""
    if not state.is_paused():
        return
    msg = update.effective_message
    # Normale Texte (kein Command) durchlassen → Resume-/Hinweis-Pfad in safeword.py
    if msg is not None and msg.text and not msg.text.startswith("/"):
        return
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if paare.ist_autorisiert(chat_id):
        from bot.services import persona_config
        try:
            if update.callback_query:
                await update.callback_query.answer(
                    t("SAFEWORD_PAUSIERT_HINWEIS", wort=persona_config.resume_wort()),
                    show_alert=True,
                )
            elif msg is not None:
                await msg.reply_text(t("SAFEWORD_PAUSIERT_HINWEIS", wort=persona_config.resume_wort()))
        except Exception:
            logger.exception("Pause-Guard: Hinweis konnte nicht gesendet werden")
    elif config.PAIRING_ENABLED:
        # Fremde Chats dürfen trotz Pause (die ist paar-scoped und gehört dem
        # Kontext-/Legacy-Paar) den Pairing-Flow nutzen – /start durchlassen.
        return
    # Fremde Chats: still blocken (kein Leak, dass es diesen Bot-Zustand gibt).
    raise ApplicationHandlerStop


scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)


def _pro_paar(job):
    """Führt einen Scheduler-Job einmal PRO aktivem Paar aus, jeweils im
    Paar-Kontext (Multiuser Schritt 5): send_domina/send_sklave, die
    persona_config-Getter und die Qdrant-Mandanten-Grenze lösen darüber das
    richtige Paar auf. Fehler eines Paares überspringen nie die anderen.
    Heute existiert nur das Env-Paar – eine Iteration, Verhalten identisch.
    Bewusst NICHT für backup_job (Betreiber-Job, läuft global)."""
    import functools

    @functools.wraps(job)
    async def wrapper(bot):
        for paar in paare.alle_paare():
            with paare.kontext(paar.paar_id):
                try:
                    await job(bot)
                except Exception:
                    logger.exception("Job %s für Paar %s fehlgeschlagen",
                                     getattr(job, "__name__", job), paar.paar_id)
    return wrapper


# Transiente Netz-/Server-Fehler (Telegram-seitige 502er, kurze TLS-/DNS-Aussetzer,
# Timeouts). PTB versucht es selbst automatisch erneut – daher hier nur eine
# einzeilige Warnung statt eines (vorher doppelten) Tracebacks.
# ACHTUNG: BadRequest erbt von NetworkError, ist aber ein ECHTER Fehler (z.B.
# Nachricht zu lang/fehlformatiert) – daher explizit ausgenommen.
TRANSIENTE_NETZFEHLER = (NetworkError, TimedOut, RetryAfter)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, TRANSIENTE_NETZFEHLER) and not isinstance(err, BadRequest):
        logger.warning("Transienter Netzfehler (PTB-Retry läuft): %s: %s",
                       type(err).__name__, err)
        return
    logger.error("Unbehandelter Fehler:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(t("FEHLER_ALLGEMEIN"))
        except Exception as inner:
            logger.error("Konnte Fehler-Nachricht nicht senden: %s", inner)


async def restore_state() -> None:
    # Pro Paar mit dessen Mandanten-Key – ein 'gefragt'-Task eines Paares landet
    # nie mehr im State eines anderen (Audit-Befund "Recovery ohne Paar-Filter").
    for paar in paare.alle_paare():
        await _restore_paar_state(paar)


async def _restore_paar_state(paar: "paare.Paar") -> None:
    sub_key = paar.user_id(paare.ROLLE_SUB)
    pending = await qdrant.get_tasks_by_status(
        ["gefragt", "gefuehl_pending", "reaktion_pending"], user_id=sub_key)
    sklave_chat = paar.sub_chat_id
    domina_chat = paar.dom_chat_id

    # Neueste zuerst: bei mehreren 'gefragt'-Tasks gewinnt deterministisch der
    # jüngste (konsistent mit der Recovery in handle_message, die max(erteilt_am)
    # nimmt) – nicht die willkürliche Scroll-Reihenfolge.
    pending.sort(key=lambda task: task.get("erteilt_am", ""), reverse=True)

    for task in pending:
        point_id = task.get("qdrant_point_id")
        status = task.get("status")
        if status == "gefragt":
            state.set_followup_task(sklave_chat, point_id)
        elif status == "gefuehl_pending":
            state.set_gefuehl_pending(sklave_chat, point_id)
        elif status == "reaktion_pending":
            state.set_reaktion_pending(domina_chat, point_id)

    # Aktiven Kette-Task wiederherstellen: ein "offen" Task mit kette_id, der noch
    # nicht 'gefragt' wurde (z.B. weil der Bot zwischen Freischaltung und Followup-Job
    # crashte). Den jüngsten setzen wir als Followup-Task UND auf Status 'gefragt',
    # damit Sklaven-Antwort sauber zugeordnet wird und der DB-Status mit dem
    # In-Memory-State synchron bleibt.
    offene = await qdrant.get_tasks_by_status(["offen"], user_id=sub_key)
    offene_ketten = [t for t in offene if t.get("kette_id")]
    if offene_ketten:
        offene_ketten.sort(key=lambda t: t.get("erteilt_am", ""), reverse=True)
        aktiv = offene_ketten[0]
        aktiv_id = aktiv.get("qdrant_point_id")
        # Status nur auf 'gefragt' setzen, wenn der In-Memory-State wirklich
        # gesetzt wurde – set_followup_task skippt still, wenn die Pending-
        # Schleife oben bereits einen Mode gesetzt hat (sonst Status-Drift).
        if state.set_followup_task(sklave_chat, aktiv_id):
            try:
                await qdrant.update_task(aktiv_id, {"status": "gefragt"})
            except Exception as e:
                logger.error("Restore: konnte Kette-Task %s nicht auf 'gefragt' setzen: %s", aktiv_id, e)
        else:
            logger.info("Restore: Kette-Task %s bleibt 'offen' – Sklave-Mode bereits belegt.", aktiv_id)


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
        if mode == "quiz_antwort":
            await quiz.handle_antwort(update, context)
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
    await handle_message(update, context)


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
        logger.info("GESENDET → %s | (%d Zeichen)", _rolle(cid), len(str(txt)))
        logger.debug("GESENDET-INHALT → %s | %s", _rolle(cid), str(txt).replace("\n", " ⏎ "))
        return await _orig_send(*args, **kwargs)

    async def _logged_copy(*args, **kwargs):
        cid = kwargs.get("chat_id", args[0] if args else None)
        logger.info("GESENDET → %s | [medien-weiterleitung]", _rolle(cid))
        return await _orig_copy(*args, **kwargs)

    # PTB friert die Bot-Attribute nach Init ein; _unfrozen() erlaubt das
    # legale Setzen von Instanz-Attributen (überschreibt die gebundenen Methoden).
    with bot._unfrozen():
        bot.send_message = _logged_send
        bot.copy_message = _logged_copy


async def post_init(application: Application) -> None:
    from telegram import BotCommand, BotCommandScopeChat
    from bot import commands_katalog
    from bot.services import logserver

    _wrap_outgoing_logging(application)
    logserver.start()

    _max_versuche = 3
    _pause = 5
    for versuch in range(1, _max_versuche + 1):
        try:
            qdrant.ensure_collections()
            break
        except Exception as e:
            logger.error(
                "Qdrant ensure_collections fehlgeschlagen (Versuch %d/%d): %s",
                versuch, _max_versuche, e,
            )
            if versuch == _max_versuche:
                logger.critical(
                    "Qdrant nicht erreichbar nach %d Versuchen – Bot-Start wird abgebrochen.",
                    _max_versuche,
                )
                raise SystemExit(1) from e
            import asyncio
            await asyncio.sleep(_pause)

    try:
        from bot.services import persona_config
        await persona_config.load()    # Persona-Felder ALLER Paare in die Caches
        state.load_persisted()   # message_history + Pause-Flag aus STATE_FILE
        await restore_state()          # Modi/Tasks aus Qdrant
    except Exception as e:
        logger.error("State-Wiederherstellung Fehler: %s", e)

    # Rollenspezifische Menüs pro Paar in DESSEN UI-Locale (gemeinsame
    # Datenquelle mit /hilfe: bot/commands_katalog.py). Bewusst NACH
    # persona_config.load() – vorher wären die Locale-Caches noch leer.
    try:
        for paar in paare.alle_paare():
            with paare.kontext(paar.paar_id):
                domina_commands = [BotCommand(c, b) for c, b in commands_katalog.domina_menue()]
                sklave_commands = [BotCommand(c, b) for c, b in commands_katalog.sklave_menue()]
            await application.bot.set_my_commands(
                domina_commands,
                scope=BotCommandScopeChat(chat_id=int(paar.dom_chat_id)),
            )
            await application.bot.set_my_commands(
                sklave_commands,
                scope=BotCommandScopeChat(chat_id=int(paar.sub_chat_id)),
            )
        # Fallback (für unbekannte Chats): /hilfe, bei aktivem Pairing auch /start
        await application.bot.set_my_commands([
            BotCommand(c, b) for c, b in commands_katalog.fallback_menue()
        ])
    except Exception as e:
        logger.error("Fehler beim Setzen der Bot-Commands: %s", e)

    # Nutzer-konfigurierbare Tages-Jobs: pro Paar als eigener Cron zu DESSEN
    # Zeiten (persona_config, /einstellungen Feld 7; leer = Env-Default).
    for paar in paare.alle_paare():
        try:
            plane_zeit_jobs(application.bot, paar)
        except Exception:
            logger.exception("Zeit-Jobs für Paar %s nicht planbar", paar.paar_id)

    # 2-Wochen-Jobs: cron (wöchentlich) + ISO-Wochen-Parität IM Job (_zweiwochen_takt).
    # IntervalTrigger verlor bei jedem Deploy seine Phase (Anker = nächster Wochentag
    # → bei häufigen Deploys faktisch wöchentlich) und drifte nach DST-Umstellung um 1h.
    scheduler.add_job(_pro_paar(lernkurve_job), "cron", day_of_week="mon", hour=20, minute=30,
                      args=[application.bot], id="lernkurve", replace_existing=True)

    scheduler.add_job(_pro_paar(geheimnis_job), "interval", minutes=30,
                      args=[application.bot], id="geheimnis_check", replace_existing=True)

    scheduler.add_job(_pro_paar(kommentar_analyse_job), "cron", day_of_week="sat", hour=19, minute=30,
                      args=[application.bot], id="kommentar_analyse", replace_existing=True)

    scheduler.add_job(_pro_paar(resurface_job), "cron", day_of_week="thu", hour=20, minute=0,
                      args=[application.bot], id="resurface", replace_existing=True)

    # Lerntagebuch: jeden Sonntag 23:00 Wochen-Verdichtung der Domina-Coach-Gespräche
    scheduler.add_job(_pro_paar(lerntagebuch_job), "cron", day_of_week="sun", hour=23, minute=0,
                      args=[application.bot], id="lerntagebuch", replace_existing=True)

    # Coach-Reflexion: alle 2 Wochen Sonntag 22:00 – schlägt neue Coach-Regeln vor
    # (Parität siehe lernkurve-Kommentar oben)
    scheduler.add_job(_pro_paar(coach_reflexion_job), "cron", day_of_week="sun", hour=22, minute=0,
                      args=[application.bot], id="coach_reflexion", replace_existing=True)

    # Auto-Profil-Pflege: alle 2 Wochen Sonntag 21:00 – schlägt Profil-Updates vor
    # (Parität siehe lernkurve-Kommentar oben)
    scheduler.add_job(_pro_paar(profil_pflege_job), "cron", day_of_week="sun", hour=21, minute=0,
                      args=[application.bot], id="profil_pflege", replace_existing=True)

    # Tägliches Qdrant-Backup 03:00 (JSON-Export + Snapshots)
    scheduler.add_job(backup_job, "cron", hour=3, minute=0,
                      args=[application.bot], id="qdrant_backup", replace_existing=True)

    # Sklaven-Dossier wöchentlich (So 23:30) – verdichtet das Wissen über ihn
    scheduler.add_job(_pro_paar(sklave_dossier_job), "cron", day_of_week="sun", hour=23, minute=30,
                      args=[application.bot], id="sklave_dossier", replace_existing=True)

    # Offene Fäden täglich (16:00) – woran die Herrin von sich aus anknüpfen kann
    scheduler.add_job(_pro_paar(offene_faeden_job), "cron", hour=16, minute=0,
                      args=[application.bot], id="offene_faeden", replace_existing=True)

    # Lücken-Füller: alle 15 Min Zustellung freigegebener 'heute Abend'-Aufgaben
    # (der tägliche Check läuft pro Paar in plane_zeit_jobs).
    scheduler.add_job(_pro_paar(luecken_zustellung_job), "interval", minutes=15,
                      args=[application.bot], id="luecken_zustellung", replace_existing=True)
    scheduler.add_job(_pro_paar(blitz_check_job), "interval", minutes=30,
                      args=[application.bot], id="blitz_check", replace_existing=True)
    scheduler.add_job(_pro_paar(blitz_ablauf_job), "interval", minutes=5,
                      args=[application.bot], id="blitz_ablauf", replace_existing=True)
    scheduler.add_job(_pro_paar(event_check_job), "cron", hour=8, minute=30,
                      args=[application.bot], id="event_check", replace_existing=True)
    scheduler.add_job(_pro_paar(dauer_job), "interval", minutes=15,
                      args=[application.bot], id="dauer", replace_existing=True)
    scheduler.add_job(_pro_paar(kalender_job), "cron", hour=8, minute=0,
                      args=[application.bot], id="kalender", replace_existing=True)

    scheduler.start()
    logger.info("Scheduler gestartet: %d Jobs (%d Paare mit eigenen Zeit-Jobs)",
                len(scheduler.get_jobs()), len(paare.alle_paare()))


def register_handlers(app: Application) -> None:
    """Registriert alle Handler. Eigene Funktion, damit der Test-Harness
    (tests/harness.py) exakt dieselbe Verdrahtung nutzt wie der echte Bot."""
    app.add_error_handler(error_handler)

    # Zentrales Eingangs-Logging (läuft vor allen anderen Handlern, blockiert nicht)
    # group=-3: Paar-Kontext (Mandanten-Auflösung für persona_config & Co.);
    # group=-2: Logging zuerst (auch für Updates, die der Pause-Guard blockt);
    # group=-1: Pause-Guard – PTB führt pro Gruppe nur EINEN Handler aus, daher
    # eigene Gruppen statt zwei TypeHandler in derselben.
    app.add_handler(TypeHandler(Update, paar_kontext_setzen), group=-3)
    app.add_handler(TypeHandler(Update, log_incoming), group=-2)
    app.add_handler(TypeHandler(Update, pause_guard), group=-1)

    app.add_handler(CommandHandler(ck.aliases("profil"),      profil.show))
    app.add_handler(CommandHandler(ck.aliases("stats"),       stats.show))  # neu
    app.add_handler(CommandHandler(ck.aliases("aufgaben"),    aufgaben.show))
    app.add_handler(CommandHandler(ck.aliases("vorlagen"),    vorlagen.show))
    app.add_handler(CommandHandler(ck.aliases("inspiration"), inspiration.show))
    app.add_handler(CommandHandler(ck.aliases("ziele"),       ziele.show))
    app.add_handler(CommandHandler(ck.aliases("rueckblick"),  rueckblick.show))
    app.add_handler(CommandHandler(ck.aliases("lerntagebuch"), lerntagebuch.show))
    app.add_handler(CommandHandler(ck.aliases("dossier"),      dossier.show))
    app.add_handler(CommandHandler(ck.aliases("einstellungen"), einstellungen.show))
    app.add_handler(CommandHandler(ck.aliases("botname"),      namen.botname))
    app.add_handler(CommandHandler(ck.aliases("sklavenname"),  namen.sklavenname))
    app.add_handler(CommandHandler(ck.aliases("setup"),        namen.setup))
    app.add_handler(CommandHandler(ck.aliases("regel"),       coach_regeln.regel))
    app.add_handler(CommandHandler(ck.aliases("merken"),      coach_regeln.merken))
    app.add_handler(CommandHandler(ck.aliases("regeln"),      coach_regeln.regeln))
    app.add_handler(CommandHandler(ck.aliases("vergessen"),   coach_regeln.vergessen))
    app.add_handler(CommandHandler(ck.aliases("profil_check"), coach_regeln.profil_check))
    app.add_handler(CommandHandler(ck.aliases("lerne"),            skill.lerne))
    app.add_handler(CommandHandler(ck.aliases("lerne_neu"),        skill.lerne_neu))
    app.add_handler(CommandHandler(ck.aliases("skill_bearbeiten"), skill.skill_bearbeiten))
    app.add_handler(CommandHandler(ck.aliases("skills"),           skill.skills))
    app.add_handler(CommandHandler(ck.aliases("training"),    training.show))
    app.add_handler(CommandHandler(ck.aliases("loeschen"),    aufgaben.show_loeschen))
    app.add_handler(CommandHandler(ck.aliases("stimmung"),    stimmung.start))
    app.add_handler(CommandHandler(ck.aliases("rollenspiel"),         rollenspiel.show))
    app.add_handler(CommandHandler(ck.aliases("rollenspiel_beenden"), rollenspiel.beenden))
    app.add_handler(CommandHandler(ck.aliases("wochenplanung"),       wochenplanung.show))
    app.add_handler(CommandHandler(ck.aliases("wunsch"),              wunsch.show))
    app.add_handler(CommandHandler(ck.aliases("meinewuensche"),       wunsch.meine_wuensche))
    app.add_handler(CommandHandler(ck.aliases("meineaufgaben"),       meine_aufgaben.show))
    app.add_handler(CommandHandler(ck.aliases("geheimnis"),           geheimnis.show))
    app.add_handler(CommandHandler(ck.aliases("strafen"),             strafen_protokoll.show))
    app.add_handler(CommandHandler(ck.aliases("ueberspringen"),        kommentar.ueberspringen))
    app.add_handler(CommandHandler(ck.aliases("tinytask"),            tinytask.show))
    app.add_handler(CommandHandler(ck.aliases("tinyfb"),              tiny_task_feedback.manuelle_frage))
    app.add_handler(CommandHandler(ck.aliases("wuerfel"),             wuerfel.show))
    app.add_handler(CommandHandler(ck.aliases("roulette"),            roulette.show))
    app.add_handler(CommandHandler(ck.aliases("dauer"),               dauer.starten))
    app.add_handler(CommandHandler(ck.aliases("luecken"),             luecke.toggle))
    app.add_handler(CommandHandler(ck.aliases("abwesend"),            abwesenheit.command))
    app.add_handler(CommandHandler(ck.aliases("blitz"),               blitz.toggle))
    app.add_handler(CommandHandler(ck.aliases("arc"),                 arc.show))
    app.add_handler(CommandHandler(ck.aliases("arc_starten"),         arc.starten))
    app.add_handler(CommandHandler(ck.aliases("arc_beenden"),         arc.beenden))
    app.add_handler(CommandHandler(ck.aliases("event"),               event_arc.show))
    app.add_handler(CommandHandler(ck.aliases("event_loeschen"),      event_arc.loeschen))
    app.add_handler(CommandHandler(ck.aliases("adventskalender"),     advent.command))
    app.add_handler(CommandHandler(ck.aliases("wunschkategorien"),    wunschkategorien.show))
    app.add_handler(CommandHandler(ck.aliases("privileg"),            privileg.show))
    app.add_handler(CommandHandler(ck.aliases("wette"),               wette.show))
    app.add_handler(CommandHandler(ck.aliases("quiz"),                quiz.start))
    app.add_handler(CommandHandler(ck.aliases("hilfe"),               hilfe.show))
    app.add_handler(CommandHandler(ck.aliases("help"),                hilfe.show))
    app.add_handler(CommandHandler(ck.aliases("abbrechen"),           abbrechen_command))
    # /start: Mitglieder → Hinweis; Fremde → Pairing-Flow (nur PAIRING_ENABLED)
    app.add_handler(CommandHandler("start",                           pairing.start))
    # Betreiber-Kommandos (nur ADMIN_CHAT_ID; leer = aus)
    app.add_handler(CommandHandler("paare",                           admin.paare_liste))
    app.add_handler(CommandHandler("paar_loeschen",                   admin.paar_loeschen))

    # Kategorie-Filter Commands: EIN dynamischer Router statt ~40 fest registrierter
    # Handler – löst /aufgaben_<kategorie> zur Laufzeit gegen den Pool auf (Katalog +
    # eigene Kategorien aus dem Profil; neue eigene Kategorien ohne Bot-Neustart).
    app.add_handler(MessageHandler(
        filters.COMMAND & filters.Regex(r"^/aufgaben_"),
        aufgaben.show_kategorie_command,
    ))

    # Inline-Button Callbacks
    app.add_handler(CallbackQueryHandler(wunsch.callback_entscheidung,    pattern=r"^wunsch:"))
    app.add_handler(CallbackQueryHandler(privileg.callback_einloesen,     pattern=r"^privileg:einloesen:"))
    app.add_handler(CallbackQueryHandler(privileg.callback_entscheidung,  pattern=r"^privileg:(bestaetigen|verweigern):"))
    app.add_handler(CallbackQueryHandler(tiny_task_feedback.callback_button, pattern=r"^tinyfb:"))
    app.add_handler(CallbackQueryHandler(wuerfel.callback,                pattern=r"^wuerfel:"))
    app.add_handler(CallbackQueryHandler(wette.callback,                  pattern=r"^wette:"))
    app.add_handler(CallbackQueryHandler(blitz.callback_fertig,           pattern=r"^blitz:fertig:"))
    app.add_handler(CallbackQueryHandler(roulette.callback,               pattern=r"^roulette:"))
    app.add_handler(CallbackQueryHandler(luecke.callback,                 pattern=r"^luecke:"))
    app.add_handler(CallbackQueryHandler(resurface.callback,              pattern=r"^resurface:"))
    app.add_handler(CallbackQueryHandler(coach_regeln.callback_bestaetigen, pattern=r"^coachregel:"))
    app.add_handler(CallbackQueryHandler(kette_adaptiv.callback,            pattern=r"^ketteanpass:"))
    app.add_handler(CallbackQueryHandler(kette_adaptiv.callback_fehlschlag, pattern=r"^kettefail:"))
    app.add_handler(CallbackQueryHandler(wochenplanung.callback,            pattern=r"^wochenplan:"))
    app.add_handler(CallbackQueryHandler(followup_response.callback,        pattern=r"^followup:"))
    app.add_handler(CallbackQueryHandler(meine_aufgaben.callback,           pattern=r"^meinetask:"))
    app.add_handler(CallbackQueryHandler(wunsch.callback_loeschen,          pattern=r"^wunschdel:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Nicht-Text (Foto/Video/Voice/Dokument/…) direkt an den anderen Chat durchreichen
    app.add_handler(MessageHandler(
        filters.VOICE, handle_voice
    ))
    app.add_handler(MessageHandler(
        ~filters.TEXT & ~filters.COMMAND & ~filters.VOICE & ~filters.StatusUpdate.ALL, handle_media
    ))

    # group=1: läuft für JEDEN Command nach group 0 – einheitlicher Hinweis,
    # wenn die falsche (aber autorisierte) Rolle einen Command aufruft (F8).
    app.add_handler(MessageHandler(filters.COMMAND, falsche_rolle_hinweis), group=1)


def _paar_update_prozessor():
    """Update-Processor für concurrent_updates (Multiuser Schritt 7): Updates
    verschiedener Paare laufen parallel (ein langsamer Grok-Call eines Paares
    blockiert die anderen nicht mehr), Updates DESSELBEN Paares strikt seriell
    – das schützt die Mode-Maschine (state.get_mode → Routing → Mutation) und
    die pro-Update-Task-lokale Paar-Kontext-ContextVar bleibt sauber.

    Als Factory statt Modul-Klasse: die lokalen Tests stubben telegram.ext mit
    MagicMock, von dem sich nicht erben lässt – Import bleibt so stub-sicher."""
    from telegram.ext import SimpleUpdateProcessor

    class PaarSerialisierterProzessor(SimpleUpdateProcessor):
        async def do_process_update(self, update: object, coroutine) -> None:
            chat = getattr(update, "effective_chat", None)
            ctx = paare.resolve(chat.id) if chat is not None else None
            if ctx is None:
                # Unautorisierte/chatlose Updates: keine Mode-Maschine betroffen
                await super().do_process_update(update, coroutine)
                return
            async with paare.lock(ctx[0].paar_id):
                await super().do_process_update(update, coroutine)

    return PaarSerialisierterProzessor(max(1, config.CONCURRENT_UPDATES))


def _fuer_paar(job, paar_id: str):
    """Wie _pro_paar, aber für genau EIN Paar – für die nutzer-konfigurierbaren
    Zeit-Jobs, die pro Paar zu DESSEN Zeit als eigener Cron laufen. Läuft leer,
    wenn das Paar nicht mehr registriert ist (bis zur nächsten Neu-Planung)."""
    import functools

    @functools.wraps(job)
    async def wrapper(bot):
        if paare.get_paar(paar_id) is None:
            return
        with paare.kontext(paar_id):
            try:
                await job(bot)
            except Exception:
                logger.exception("Zeit-Job %s für Paar %s fehlgeschlagen",
                                 getattr(job, "__name__", job), paar_id)
    return wrapper


def plane_zeit_jobs(bot, paar: "paare.Paar") -> None:
    """(Re-)Plant die Tages-Jobs eines Paares zu dessen Zeiten (persona_config,
    leer = globale Env-Defaults). Aufgerufen beim Start für alle Paare, nach
    erfolgreichem Pairing und nach Zeit-Änderung in /einstellungen –
    replace_existing macht den Aufruf idempotent."""
    from bot.services import persona_config
    pid = paar.paar_id
    with paare.kontext(pid):
        z = {feld: persona_config.zeit(feld) for feld in persona_config.ZEIT_FELDER}

    hour, minute = config.hm(z["followup_time"])
    # Training Zeit = Follow-up + TRAINING_OFFSET_MINUTEN
    training_total = hour * 60 + minute + config.TRAINING_OFFSET_MINUTEN

    def _job(name, fn, zeit_feld, **cron):
        h, m = config.hm(z[zeit_feld])
        scheduler.add_job(_fuer_paar(fn, pid), "cron", hour=h, minute=m, args=[bot],
                          id=f"{name}_p{pid}", replace_existing=True, **cron)

    scheduler.add_job(_fuer_paar(followup_job, pid), "cron", hour=hour, minute=minute,
                      args=[bot], id=f"daily_followup_p{pid}", replace_existing=True)
    if config.TRAINING_ENABLED:
        scheduler.add_job(_fuer_paar(training_job, pid), "cron", day_of_week="tue,thu",
                          hour=(training_total // 60) % 24, minute=training_total % 60,
                          args=[bot], id=f"daily_training_p{pid}", replace_existing=True)
        _job("training_erinnerung", training_erinnerung_job, "training_erinnerung_time")
    if config.STIMMUNG_ENABLED:
        _job("stimmung_check", stimmung_job, "stimmung_time")
    _job("tiny_task_vorschlag", tiny_task_vorschlag_job, "tiny_task_time")
    _job("ziel_erinnerung", ziel_erinnerung_job, "ziel_erinnerung_time", day_of_week="mon")
    _job("rollenspiel_vorschlag", rollenspiel_vorschlag_job, "rollenspiel_vorschlag_time", day_of_week="fri,sat")
    _job("wochenplanung", wochenplanung_job, "wochenplanung_time", day_of_week="sun")
    _job("tiny_task_feedback", tiny_task_feedback_job, "tiny_task_feedback_time")
    _job("luecken_check", luecken_check_job, "luecken_check_time")
    logger.info("Zeit-Jobs für Paar %s geplant (Follow-up %s)", pid, z["followup_time"])


def entferne_zeit_jobs(paar_id: str) -> None:
    """Entfernt die pro-Paar-Crons eines Paares (Paar-Löschung, handlers/admin.py)."""
    suffix = f"_p{paar_id}"
    for job in scheduler.get_jobs():
        if job.id.endswith(suffix):
            job.remove()


def main() -> None:
    config.validate()  # Fail-fast: Pflicht-Env-Vars prüfen, bevor irgendwas startet
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(_paar_update_prozessor())
        .build()
    )

    register_handlers(app)

    logger.info("Bot gestartet.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()