"""
Blitzaufgaben ⚡ – unangekündigte Mini-Aufgabe mit Countdown.

Opt-in durch die Domina (/blitz toggelt `blitz_aktiv` im Domina-Profil,
Default AUS). Der Scheduler (blitz_check_job) entscheidet zufällig innerhalb
des erlaubten Fensters (BLITZ_FENSTER ∩ kinderfreie Zeiten); die Aufgabe geht
DIREKT an den Sklaven – die Grundsatz-Freigabe ist das Opt-in, jede einzelne
Aufgabe läuft durch den deterministischen limits_check. Eine Einzel-Freigabe
wie beim Lücken-Füller würde den Überraschungs-/Timer-Charakter zerstören.

Geschafft-Button vor Ablauf → Bonuspunkte (punkte.py, quelle='blitz').
Verpasst (Sweep blitz_ablauf_job) → Status 'blitz_verpasst' + spöttische
Herrin-Nachricht; bewusst KEIN Streak-Reset und KEIN Eskalations-Zähler
(Überraschung verpasst ist kein Ungehorsam wie eine verweigerte Aufgabe).
Wetten (/wette) bleiben von Blitzaufgaben unberührt (beide Richtungen).
"""
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check, kategorie_logik, punkte
from bot.services import sticker_reaktionen
from bot.prompts import followup as fp
from bot.prompts import persona, coach_persona
from bot.messages import t

logger = logging.getLogger(__name__)


async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/blitz – Domina schaltet Blitzaufgaben an/aus."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    profil = await qdrant.get_user_profile("domina") or {}
    neu = not profil.get("blitz_aktiv", False)
    await qdrant.patch_profile_fields("domina", {"blitz_aktiv": neu})
    if neu:
        await update.message.reply_text(
            t("BLITZ_AN", minuten=config.BLITZ_COUNTDOWN_MINUTEN), parse_mode="Markdown")
    else:
        await update.message.reply_text(t("BLITZ_AUS"))


async def _generiere_blitz(domina_profile: dict, sklave_profile: dict) -> tuple[str | None, str]:
    """Erzeugt EINE limit-saubere Mini-Aufgabe (sofort machbar, ohne Vorbereitung).
    Gibt (text, kategorie) zurück; text=None bei Fehler/Grenzverletzung."""
    import random
    pool = kategorie_logik.alle_kategorien(sklave_profile)
    dislikes = kategorie_logik.dislike_kategorien(sklave_profile)
    verfuegbar = [k for k in pool if k not in dislikes] or pool
    kategorie = random.choice(verfuegbar)

    system = (
        "Du erzeugst eine BLITZAUFGABE für ihren Sklaven: eine Mini-Aufgabe, die er "
        "SOFORT und in maximal 10-15 Minuten erledigen kann – ohne Vorbereitung, ohne "
        "Einkauf, ohne dass er dafür das Haus verlassen muss.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        "Die Aufgabe soll:\n"
        "- Klein, konkret und sofort umsetzbar sein\n"
        "- Ein Kribbeln haben (Überraschungsmoment nutzen)\n"
        "- Aus der vorgegebenen Kategorie stammen\n"
        "- Zu IHM passen – nicht generisch\n\n"
        "Antworte NUR mit dem reinen Aufgaben-Text (1-2 Sätze), keine Einleitung, "
        "kein Markdown, keine Anführungszeichen."
    )
    prompt = (
        f"Pflicht-Kategorie: {kategorie}\n"
        f"{coach_persona.sklaven_kontext_block(sklave_profile, domina_profile.get('grenzen', []) or [])}"
    )
    try:
        sk_hl = sklave_profile.get("hard_limits", []) or []
        do_gr = domina_profile.get("grenzen", []) or []
        text = await limits_check.generate_mit_limit_retry(prompt, sk_hl, do_gr, system=system)
        return (grok.clean_text(text) or None) if text else None, kategorie
    except Exception:
        logger.exception("Blitz-Generierung fehlgeschlagen")
        return None, kategorie


async def sende_blitz(bot) -> bool:
    """Generiert eine Blitzaufgabe und schickt sie mit Countdown + Geschafft-Button
    an den Sklaven. Wird vom blitz_check_job aufgerufen. True bei Versand."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}

    text, kategorie = await _generiere_blitz(domina_profile, sklave_profile)
    if not text:
        return False

    # Herrin-Stimme für die Zustellung (wie Würfel/Lücke), Countdown angehängt.
    # LLM VOR der Task-Anlage: schlägt die Formulierung samt Fallback fehl,
    # existiert noch kein Task.
    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(text), max_tokens=250)
    except Exception:
        logger.exception("aufgabe_an_sklaven (Blitz) fehlgeschlagen – Rohtext")
        anweisung = text

    # TOCTOU-Re-Check nach den LLM-Awaits (D9/M7, Muster _nach_llm_verworfen):
    # im 10-60s-Generierungs-Fenster kann ein Safeword oder ein Sklaven-Flow
    # gekommen sein – dann weder Task anlegen noch senden.
    if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
        logger.info("Blitz nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
        return False

    # Task erst unmittelbar vor dem Send anlegen (Countdown startet dann auch
    # erst jetzt); der Button braucht die point_id, deshalb geht es nicht ganz
    # ohne Anlage vor dem Send – dafür Rollback bei Sendefehler.
    level = domina_profile.get("aktuelles_level", 3)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=config.BLITZ_COUNTDOWN_MINUTEN)
    point_id = await qdrant.erstelle_task(
        text, kategorie, level, quelle="blitz",
        extra={"blitz_deadline": deadline.isoformat()},
    )

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
        t("BUTTON_BLITZ_GESCHAFFT"), callback_data=f"blitz:fertig:{point_id}")]])
    # Befehls-Sticker als Aufmerksamkeits-Auftakt der Überraschungs-Aufgabe
    await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.BEFEHL)
    try:
        await telegram_helper.send_sklave(
            bot,
            t("BLITZ_AN_SKLAVEN", anweisung=anweisung, minuten=config.BLITZ_COUNTDOWN_MINUTEN),
            parse_mode="Markdown", reply_markup=keyboard, voice_text=anweisung,
        )
    except Exception:
        # Nie zugestellte Blitzaufgabe nicht als offenen Task zurücklassen –
        # sonst fragt das Followup nach einer unbekannten Aufgabe und der
        # Ablauf-Sweep schickt Spott für nichts (Trace 06.07., Lücke 5).
        await qdrant.loesche_task(point_id)
        raise
    # Throttle-Anker im Domina-Profil + Info an sie (best-effort)
    await qdrant.patch_profile_fields(
        "domina", {"blitz_letzte_am": datetime.now(timezone.utc).isoformat()})
    try:
        await telegram_helper.send_domina(
            bot, t("BLITZ_INFO_DOMINA", aufgabe=text, minuten=config.BLITZ_COUNTDOWN_MINUTEN))
    except Exception:
        logger.exception("Blitz-Info an Domina fehlgeschlagen")
    logger.info("Blitzaufgabe gesendet (Kategorie: %s, point_id=%s)", kategorie, point_id)
    return True


async def callback_fertig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Geschafft-Button: innerhalb der Deadline → Punkte + Herrin-Reaktion."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    point_id = parts[2] if len(parts) > 2 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    task = await qdrant.get_task(point_id) if point_id else None
    if not task or task.get("status") != "offen":
        await query.message.reply_text(t("BLITZ_NICHT_MEHR_OFFEN"))
        return

    jetzt = datetime.now(timezone.utc)
    deadline = task.get("blitz_deadline", "")
    try:
        abgelaufen = bool(deadline) and jetzt.isoformat() > deadline
    except TypeError:
        abgelaufen = False
    if abgelaufen:
        # Sweep war noch nicht dran – hier direkt als verpasst markieren.
        await qdrant.update_task(point_id, {"status": "blitz_verpasst"})
        await query.message.reply_text(t("BLITZ_ZU_SPAET"))
        return

    await qdrant.update_task(point_id, {"status": "erledigt"})
    ergebnis = None
    try:
        ergebnis = await punkte.task_erledigt(task)
    except Exception:
        logger.exception("Punkte für Blitzaufgabe fehlgeschlagen")

    if ergebnis:
        boni = ergebnis.get("boni", [])
        breakdown = "\n".join(f"  • {name}: +{p}" for name, p in boni) if len(boni) > 1 else ""
        msg = t("BLITZ_GESCHAFFT", punkte=ergebnis["gewonnene_punkte"], gesamt=ergebnis["punkte"])
        if breakdown:
            msg += f"\n{breakdown}"
        await query.message.reply_text(msg, parse_mode="Markdown")

    # Herrin reagiert (best-effort)
    try:
        system = (
            "Du sprichst direkt mit ihm. Er hat gerade deine Blitzaufgabe innerhalb "
            "des Countdowns geschafft. Reagiere in ein bis zwei Sätzen – anerkennend "
            "auf deine Art, ohne Zuckerguss. Kein Markdown.\n\n"
            + persona.fuer_sklaven_prompt()
        )
        reaktion = grok.clean_text(await grok.simple(
            f"Die Blitzaufgabe war: {task.get('aufgabe', '')}", system=system, max_tokens=150))
        if reaktion:
            await query.message.reply_text(reaktion)
    except Exception:
        logger.exception("Blitz-Reaktion fehlgeschlagen")

    try:
        await telegram_helper.send_domina(context.bot, t("BLITZ_GESCHAFFT_DOMINA"))
    except Exception:
        logger.exception("Blitz-Erfolg-Info an Domina fehlgeschlagen")


async def markiere_verpasst(bot, task: dict) -> None:
    """Vom Ablauf-Sweep gerufen: Status setzen + Herrin spottet, Domina erfährt es."""
    point_id = task.get("qdrant_point_id")
    if not point_id:
        return
    await qdrant.update_task(point_id, {"status": "blitz_verpasst"})

    nachricht = t("BLITZ_VERPASST")
    try:
        system = (
            "Du sprichst direkt mit ihm. Er hat deine Blitzaufgabe verstreichen lassen – "
            "der Countdown ist abgelaufen, ohne dass er sich gemeldet hat. Reagiere in ein "
            "bis zwei Sätzen: genüsslich-spöttisch, nicht wütend; lass offen, ob das noch "
            "ein Nachspiel hat. Kein Markdown.\n\n" + persona.fuer_sklaven_prompt()
        )
        reaktion = grok.clean_text(await grok.simple(
            f"Die verpasste Blitzaufgabe war: {task.get('aufgabe', '')}", system=system, max_tokens=150))
        if reaktion:
            nachricht = reaktion
    except Exception:
        logger.exception("Blitz-Spott fehlgeschlagen – nutze Fallback")
    # Re-Check nach dem LLM-Await (D9/M7): Status ist korrekt gesetzt, aber in
    # der Safeword-Pause geht gar nichts raus; bei aktivem Sklaven-Flow entfällt
    # nur der Spott (die Domina-Info unten darf trotzdem).
    if state.is_paused():
        logger.info("Blitz-Verpasst-Meldungen übersprungen – Safeword-Pause.")
        return
    if state.get_mode(paare.sub_chat_id()) not in ("chat", None):
        logger.info("Blitz-Verpasst-Spott übersprungen – Sklave in aktivem Flow.")
    else:
        # Spott-Sticker passend zur genüsslich-spöttischen Reaktion
        await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.SPOTT)
        await telegram_helper.send_sklave(bot, nachricht)
    try:
        await telegram_helper.send_domina(
            bot, t("BLITZ_VERPASST_DOMINA", aufgabe=task.get("aufgabe", "")))
    except Exception:
        logger.exception("Blitz-Verpasst-Info an Domina fehlgeschlagen")
    logger.info("Blitzaufgabe verpasst (point_id=%s)", point_id)
