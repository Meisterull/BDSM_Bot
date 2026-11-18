"""
Follow-up Response Handler.
Verarbeitet die Ja/Nein Antwort des Sklaven auf die Follow-up Frage.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, punkte, synonyme, kategorie_logik
from bot.services import sticker_reaktionen
from bot.prompts import followup as fp
from bot.prompts import bestrafung as bp
from bot.handlers import waehrung as waehrung_h
from bot.messages import t

logger = logging.getLogger(__name__)


def frage_buttons(task_id: str) -> InlineKeyboardMarkup:
    """Zwei Buttons für die Followup-Frage (erledigt / nicht erledigt)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ERLEDIGT"), callback_data=f"followup:ja:{task_id}"),
        InlineKeyboardButton(t("BUTTON_NICHT_ERLEDIGT"), callback_data=f"followup:nein:{task_id}"),
    ]])


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text-Pfad (ja/nein getippt). Die Buttons laufen über callback()."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip().lower()
    s = state.get(chat_id)
    task_id = s.get("followup_task_id")
    if not task_id:
        state.set_mode(chat_id, "chat")
        return
    task = await qdrant.get_task(task_id)
    if not task:
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
        return
    aufgabe = task.get("aufgabe", "")
    antwort = synonyme.ja_nein(text)
    if antwort == "ja":
        await _handle_yes(update.message, context, chat_id, task_id, aufgabe, s,
                          erteilt_am=task.get("erteilt_am", ""))
    elif antwort == "nein":
        await _handle_no(update.message, context, chat_id, task_id, aufgabe, task)
    else:
        await update.message.reply_text(t("FOLLOWUP_KLARSTELLUNG"))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Button-Pfad: ✅ Erledigt / ❌ Nicht erledigt."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, task_id = query.data.split(":", 2)
    except ValueError:
        return
    chat_id = str(query.message.chat_id)
    # Steckt der Sklave gerade in einer Gefühl-Abfrage für eine ANDERE Aufgabe,
    # den Tap NICHT durchlassen (Buttons bleiben erhalten): sonst überschreibt er
    # followup_task_id und die Gefühl-Antwort landet beim falschen Task.
    if (state.get_mode(chat_id) == "gefuehl"
            and state.get(chat_id).get("followup_task_id") not in (None, task_id)):
        await query.message.reply_text(t("FOLLOWUP_ERST_BEANTWORTEN"))
        return
    await query.edit_message_reply_markup(reply_markup=None)
    task = await qdrant.get_task(task_id)
    if not task:
        await query.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
        return
    # Status-Guard: für denselben Task können mehrere Button-Nachrichten leben
    # (Abend-Job + /meineaufgaben). Ein alter Tap darf einen abgeschlossenen
    # Task nicht erneut umschalten (❌ → Streak-Reset/Bestrafung, ✅ → doppelte
    # Punkte) und während der Safeword-Pause keinen pausierten Task abschließen.
    if task.get("status") not in ("offen", "gefragt"):
        await query.message.reply_text(t("MEINEAUFGABEN_NICHT_OFFEN"))
        return
    aufgabe = task.get("aufgabe", "")
    s = state.get(chat_id)
    s["followup_task_id"] = task_id
    if action == "ja":
        await _handle_yes(query.message, context, chat_id, task_id, aufgabe, s,
                          erteilt_am=task.get("erteilt_am", ""))
    elif action == "nein":
        await _handle_no(query.message, context, chat_id, task_id, aufgabe, task)


async def _handle_yes(
    message,
    context,
    chat_id: str,
    task_id: str,
    aufgabe: str,
    s: dict,
    erteilt_am: str = "",
) -> None:
    """Ja: Gefühl abfragen."""
    await qdrant.update_task(task_id, {"status": "gefuehl_pending"})
    state.set_gefuehl_pending(chat_id, task_id)
    prompt = fp.gefuehl_abfragen(aufgabe, erteilt_am=erteilt_am)
    try:
        frage = await grok.simple(prompt, max_tokens=250)
    except Exception as e:
        logger.error("Gefühl-Frage fehlgeschlagen, sende Fallback: %s", e)
        frage = t("FALLBACK_GEFUEHL_FRAGE")
    # Die tatsächlich gestellte Frage merken: seine Antwort wird später MIT ihr
    # gespeichert und ausgewertet (eine knappe Antwort ist ohne sie nicht deutbar).
    s["gefuehl_frage"] = grok.clean_text(frage)[:400]
    await message.reply_text(frage)


async def _handle_no(
    message,
    context,
    chat_id: str,
    task_id: str,
    aufgabe: str,
    task: dict | None = None,
) -> None:
    """Nein: erst klären, an WEM es lag (Versäumnis der Herrin ⏳, 15.09.2026):
    brauchte die Aufgabe die Dom-Seite selbst, bekommt der Sub eine Rückfrage –
    der Malus darf nicht blind laufen. Sonst direkt die Malus-Kette."""
    if config.HERRIN_VERSAEUMNIS:
        from bot.handlers import herrin_versaeumnis  # lazy: zirkulärer Import
        if task is None:
            task = await qdrant.get_task(task_id)
        if task and await herrin_versaeumnis.braucht_herrin(task):
            await herrin_versaeumnis.frage_stellen(message, chat_id, task_id)
            return
    await malus_kette(message, context, chat_id, task_id, aufgabe,
                      erteilt_am=(task or {}).get("erteilt_am", ""))


async def malus_kette(
    message,
    context,
    chat_id: str,
    task_id: str,
    aufgabe: str,
    erteilt_am: str = "",
) -> None:
    """Nicht erledigt, lag am Sub: Streak reset, Domina informieren + Bestrafungsvorschlag.
    Auch der Einstieg für herrin_versaeumnis („lag an mir"). `erteilt_am` ordnet
    in den Prompts die Zeitwörter des Aufgabentexts ein."""
    await qdrant.update_task(task_id, {"status": "nicht_erledigt"})
    state.set_mode(chat_id, "chat")
    state.get(chat_id)["followup_task_id"] = None

    # Streak vor Reset merken für Bestrafungsvorschlag
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    streak_vorher = sklave_profil.get("streak", 0)

    # Streak zurücksetzen (+ aktive Wette verfällt)
    verlorene_wette = 0
    try:
        verlorene_wette = await punkte.task_nicht_erledigt()
    except Exception as e:
        logger.error("Fehler bei Streak-Reset: %s", e)
    # Währung ⭐: fester Abzug (nie unter 0) – Meldung unten in der Herrin-Reaktion
    abzug = await waehrung_h.abzug_nicht_erledigt()
    spott_gesendet = False
    if verlorene_wette:
        # Verlorene Wette: spöttisches Amüsement der Herrin
        spott_gesendet = await sticker_reaktionen.sende_sklave(
            context.bot, sticker_reaktionen.SPOTT)
        try:
            await message.reply_text(
                t("WETTE_VERLOREN", einsatz=verlorene_wette), parse_mode="Markdown")
        except Exception as e:
            logger.error("Wett-Verlust-Meldung fehlgeschlagen: %s", e)

    # Domina State auf reaktion_pending
    domina_chat = paare.dom_chat_id()
    state.set_reaktion_pending(domina_chat, task_id)

    # EINE Nachricht an die Domina (Review D11): die Tatsache als Kopfzeile, der
    # Straf-Vorschlag darunter. Vorher ging erst ein eigener LLM-Bericht mit
    # Rückfrage raus und danach der Vorschlag mit einer zweiten Rückfrage.
    kopf = t("NICHT_ERLEDIGT_KOPF", aufgabe=telegram_helper.md_einbett_sicher(aufgabe))
    informiert = False

    # Eskalation oder normaler Bestrafungsvorschlag an Domina
    strafe_id = None
    sklave_hard_limits = sklave_profil.get("hard_limits", []) or []
    domina_profil = await qdrant.get_user_profile("domina") or {}
    domina_grenzen = domina_profil.get("grenzen", []) or []
    try:
        nicht_erledigt_streak = await qdrant.get_nicht_erledigt_streak("sklave")
        # Personalisierungs-Kontext für Bestrafungs-Prompt
        sklave_vorlieben = sklave_profil.get("vorlieben", []) or []
        kategorie_reaktionen = kategorie_logik.reaktionen_fuer_prompt(sklave_profil)
        try:
            letzte_strafen_eintraege = await qdrant.get_strafen("sklave", limit=5) or []
            letzte_strafen = [
                s.get("bestrafung_text", "") or s.get("aufgabe", "")
                for s in letzte_strafen_eintraege
            ]
        except Exception as e:
            logger.error("Fehler beim Laden letzter Strafen: %s", e)
            letzte_strafen = []

        if nicht_erledigt_streak >= 3:
            prompt_bestrafung = bp.eskalations_vorschlag(
                aufgabe, nicht_erledigt_streak, sklave_hard_limits,
                dossier=sklave_profil.get("dossier", ""),
                letzte_strafen=letzte_strafen,
                erteilt_am=erteilt_am,
            )
            label = t("BESTRAFUNG_LABEL_ESKALATION")
        else:
            from bot.services import inventar
            prompt_bestrafung = bp.bestrafungsvorschlag(
                aufgabe, streak_vorher, sklave_hard_limits,
                sklave_vorlieben=sklave_vorlieben,
                kategorie_reaktionen=kategorie_reaktionen,
                letzte_strafen=letzte_strafen,
                dossier=sklave_profil.get("dossier", ""),
                inventar_impuls=await inventar.impuls_wahl(),
                erteilt_am=erteilt_am,
            )
            label = t("BESTRAFUNG_LABEL_VORSCHLAG")
        # Limits-Check inkl. Domina-Grenzen, mit einmaliger Re-Generierung
        from bot.services import limits_check
        vorschlag = await limits_check.generate_mit_limit_retry(
            prompt_bestrafung, sklave_hard_limits, domina_grenzen, reasoning=True,
        )
        if vorschlag is None:
            # Kein Grenzen-konformer Vorschlag: Domina informieren, aber NICHT
            # zurückkehren – der Sklave bekommt unten trotzdem seine Reaktion.
            await telegram_helper.send_domina(
                context.bot, f"{kopf}\n\n{t('BESTRAFUNG_KEIN_VORSCHLAG')}", parse_mode="Markdown")
            informiert = True
        else:
            # Strafe VOR dem Send speichern: die Punkte-Abzug-Buttons (Währung ⭐)
            # hängen an der Strafe-Kennung – einmal pro Vorschlag.
            from datetime import datetime, timezone
            strafe_id = await qdrant.save_strafe({
                "user_id": "sklave",
                "aufgabe": aufgabe,
                "grund": "nicht_erledigt",
                "bestrafung_text": vorschlag,
                "datum": datetime.now(timezone.utc).isoformat(),
                "status": "vorgeschlagen",
            })
            waehrung_h.abzug_anbieten(str(strafe_id))
            await telegram_helper.send_domina(
                context.bot,
                f"{kopf}\n\n{label}\n\n{vorschlag}",
                parse_mode="Markdown",
                reply_markup=waehrung_h.abzug_buttons(str(strafe_id)),
            )
            informiert = True
    except Exception as e:
        logger.error("Fehler bei Bestrafungsvorschlag: %s", e)

    # Fangnetz: kam oben nichts bei ihr an (Fehler im Vorschlags-Pfad), erfährt
    # sie es trotzdem – als kurzer Bericht mit der Frage, ob sie ihm etwas
    # ausrichten möchte.
    if not informiert:
        try:
            bericht = await grok.simple(fp.bericht_nicht_erledigt(aufgabe, erteilt_am=erteilt_am))
        except Exception as e:
            logger.error("Bericht-nicht-erledigt fehlgeschlagen, sende Roh-Meldung: %s", e)
            bericht = kopf
        await telegram_helper.send_domina(context.bot, bericht, parse_mode="Markdown")

    # Strafe-ID im Domina-State speichern
    if strafe_id:
        domina_s = state.get(domina_chat)
        domina_s["strafe_id"] = strafe_id

    # Reaktion der Herrin direkt an den Sklaven
    try:
        reaktion = await grok.simple(
            fp.reaktion_auf_nicht_erledigt(aufgabe, erteilt_am=erteilt_am), max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Reaktion-auf-Nicht-Erledigt: %s", e)
        reaktion = t("FALLBACK_NICHT_ERLEDIGT")
    # Strenger Blick vor der Reaktion – außer der Spott-Sticker lief schon
    # oben bei der verlorenen Wette (zwei Sticker im selben Flow wären zu viel).
    if not spott_gesendet:
        await sticker_reaktionen.sende_sklave(context.bot, sticker_reaktionen.STRENG)
    if abzug and abzug.get("delta"):
        reaktion += "\n\n" + t("ABZUG_NICHT_ERLEDIGT_ZEILE", betrag=-abzug["delta"], stand=abzug["neu"])
    await message.reply_text(reaktion)
    if abzug and abzug.get("delta"):
        await waehrung_h.nach_punkteaenderung(context.bot, abzug["alt"], abzug["neu"], push=False)

    # F12: Gescheitertes Ketten-Glied darf die Kette nicht stranden lassen –
    # die Domina entscheidet per Button, ob sie weiterläuft oder abbricht.
    try:
        task = await qdrant.get_task(task_id)
        if task and task.get("kette_id"):
            from bot.handlers import kette_adaptiv
            await kette_adaptiv.frage_bei_fehlschlag(context.bot, task)
    except Exception:
        logger.exception("Kette-Fehlschlag-Entscheidung konnte nicht angestoßen werden")