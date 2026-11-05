"""
Rollen-Auflösung, Paar-Kontext, Eingangs-Log, Callback-Gate, Pause-Guard, Fehler-Handler.

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop
from telegram.error import NetworkError, TimedOut, RetryAfter, BadRequest
from bot import config, state
from bot.services import paare
from bot.handlers import (
    safeword,
    wunsch,
    wuerfel,
    privileg,
    wette,
    blitz,
    roulette,
    resurface,
    luecke,
)
from bot.scheduler.wartung import backup_job
from bot.messages import t

logger = logging.getLogger(__name__)

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
        # Eingangsstempel (Stille-Check-in): JEDE Eingabe eines Paar-Mitglieds –
        # Text, Medien oder Button – zählt als Lebenszeichen.
        if update.effective_chat and paare.resolve(update.effective_chat.id) is not None:
            state.touch_eingang(update.effective_chat.id)
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


# D9/S1: Rollen-Karte der Callback-Präfixe (spezifischere Präfixe zuerst).
# Telegram validiert callback_data nicht – ein modifizierter Client kann
# beliebige Daten an den Bot schicken, und die Privileg-IDs sind statisch
# ratbar (pause_tag, easy_mode, …). Fremde Chats werden im Gate hart gestoppt,
# paar-intern muss die Rolle des Drückers zum Button-Typ passen.
_CALLBACK_ROLLEN = (
    ("privileg:einloesen:", paare.ROLLE_SUB),   # Sklave löst ein …
    ("privileg:",           paare.ROLLE_DOM),   # … die Domina entscheidet
    ("wunsch:",             paare.ROLLE_DOM),
    ("tinyfb:",             paare.ROLLE_DOM),
    ("wuerfel:",            paare.ROLLE_DOM),
    ("roulette:",           paare.ROLLE_DOM),
    ("luecke:",             paare.ROLLE_DOM),
    ("resurface:",          paare.ROLLE_DOM),
    ("coachregel:",         paare.ROLLE_DOM),
    ("ketteanpass:",        paare.ROLLE_DOM),
    ("kettefail:",          paare.ROLLE_DOM),
    ("wochenplan:",         paare.ROLLE_DOM),
    ("stille:",             paare.ROLLE_DOM),
    ("herrinfehl:",         paare.ROLLE_DOM),   # Versäumnis: nachholen/streichen
    ("snaufgabe:",          paare.ROLLE_DOM),   # Sprachnachricht → Aufgabe ja/nein
    ("wettidee:",           paare.ROLLE_DOM),   # Wettvorschlag: senden / andere Idee
    ("punkteabzug:",        paare.ROLLE_DOM),   # Währung: −25/−50 unter dem Strafvorschlag
    ("wunschpreis:",        paare.ROLLE_DOM),   # Währung: Preis eines Wunsches
    ("wunschziel:",         paare.ROLLE_DOM),   # Währung: Sparziel gewähren / später
    ("wetteeinspruch:",     paare.ROLLE_DOM),   # Währung: Wett-Urteil kippen
    ("wetteurteil:",        paare.ROLLE_SUB),   # Währung: gewonnen / verloren
    ("wetteantwort:",       paare.ROLLE_SUB),   # Währung: Wette annehmen / ablehnen
    ("wetteablehnung:",     paare.ROLLE_DOM),   # Währung: Abzug + Strafwahl nach Ablehnung
    ("herrin:",             paare.ROLLE_SUB),   # Versäumnis: an mir / an ihr
    ("wette:",              paare.ROLLE_SUB),
    ("blitz:",              paare.ROLLE_SUB),
    ("followup:",           paare.ROLLE_SUB),
    ("meinetask:",          paare.ROLLE_SUB),
    ("wunschdel:",          paare.ROLLE_SUB),
)


async def _callback_gate(update: Update) -> None:
    """Autorisiert JEDE callback_query (D9/S1): Chat muss zu einem Paar gehören,
    Rolle muss zum Button-Präfix passen. Verstoß → still verwerfen (nur
    query.answer, kein Inhalt) + ApplicationHandlerStop. Unbekannte Präfixe
    laufen durch (Paar-Zugehörigkeit ist dann trotzdem geprüft)."""
    q = update.callback_query
    data = q.data if isinstance(q.data, str) else ""
    prefix_log = data.split(":", 1)[0]
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    ctx = paare.resolve(chat_id)
    if ctx is None:
        logger.warning("Callback '%s:…' aus nicht autorisiertem Chat verworfen.", prefix_log)
        try:
            await q.answer()
        except Exception:
            pass
        raise ApplicationHandlerStop
    rolle = ctx[1]
    for prefix, erlaubt in _CALLBACK_ROLLEN:
        if data.startswith(prefix):
            if rolle != erlaubt:
                logger.warning("Callback '%s:…' von falscher Rolle (%s) verworfen.", prefix_log, rolle)
                try:
                    await q.answer()
                except Exception:
                    pass
                raise ApplicationHandlerStop
            break


async def pause_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zentraler Guard (group=-1, VOR allen Handlern): erst die
    Callback-Autorisierung (D9/S1, läuft IMMER), dann der Safeword-Pause-Teil.

    Die Pause blockiert Commands, Inline-Button-Callbacks und Medien –
    sonst könnten z.B. liegengebliebene Würfel-/Wochenplan-Buttons trotz Pause
    neue Aufgaben an den Sklaven senden. Normale Text-Nachrichten laufen durch,
    damit safeword.check_and_handle das RESUME-Wort verarbeiten und den
    Pausen-Hinweis senden kann."""
    if update.callback_query is not None:
        await _callback_gate(update)
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
