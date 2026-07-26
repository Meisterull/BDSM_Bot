"""
Safeword Handler – wird ZUERST geprüft, vor allen anderen Handlern.
"""
from telegram import Update, Bot
from bot import state
from bot.services import paare, persona_config, qdrant, telegram_helper
from bot.messages import t


async def check_and_handle(update: Update, bot: Bot) -> bool:
    """
    Prüft ob die Nachricht das Safeword oder das Resume-Wort DES PAARES enthält
    (persona_config, leer = Env-Defaults; über /einstellungen Feld 8 änderbar).
    Gibt True zurück wenn die Nachricht verarbeitet wurde (kein weiterer Handler nötig).
    """
    text = (update.message.text or "").strip().lower()

    if text == persona_config.safeword():
        await _pause_all(bot)
        return True

    if text == persona_config.resume_wort() and state.is_paused():
        await _resume(bot)
        return True

    # Im pausierten Modus: keine normalen Antworten
    if state.is_paused():
        await update.message.reply_text(
            t("SAFEWORD_PAUSIERT_HINWEIS", wort=persona_config.resume_wort())
        )
        return True

    return False


async def _pause_all(bot: Bot) -> None:
    """Alle offenen Tasks DES PAARES pausieren, dessen Pause-Flag setzen.

    Paar-scoped (Multiuser Schritt 6): Task-Query und Pause-Flag laufen über den
    Paar-Kontext (gesetzt pro Update in main.py) – das Safeword eines Paares
    stoppt nie die anderen."""
    # serie_wartend/kette_wartend mitpausieren – sonst aktiviert _process_serie_tasks
    # bzw. die Ketten-Freischaltung sie während der Safeword-Pause weiter.
    # (reaktion_pending entfernt, Review D8: war nie ein Task-Status, nur ein Mode.)
    pausierbare_status = [
        "offen", "gefragt", "gefuehl_pending",
        "serie_wartend", "kette_wartend",
    ]
    tasks = await qdrant.get_tasks_by_status(pausierbare_status)

    for task in tasks:
        point_id = task.get("qdrant_point_id")
        if not point_id:
            continue
        if task.get("quelle") == "blitz" and task.get("status") == "offen":
            # Laufenden Blitz-Countdown ENDGÜLTIG abbrechen statt pausieren:
            # die Deadline läuft real weiter, und nach dem Resume würde der
            # Ablauf-Sweep sonst als Erstes den Verpasst-Spott schicken –
            # direkt nach einem Safeword der falscheste Ton (Trace 06.07., Lücke 8).
            await qdrant.update_task(point_id, {"status": "blitz_abgebrochen"})
            continue
        # Vorherigen Status merken, damit _resume ihn wiederherstellen kann.
        await qdrant.update_task(point_id, {
            "status": "pausiert",
            "status_vor_pause": task.get("status"),
        })

    paar = paare.paar_im_kontext()
    state.set_paused(True)
    state.set_mode(paar.dom_chat_id, "pausiert")
    state.set_mode(paar.sub_chat_id, "pausiert")

    msg = t("SAFEWORD_PAUSIERT", wort=persona_config.resume_wort())
    await telegram_helper.send_domina(bot, msg)
    await telegram_helper.send_sklave(bot, msg)


async def _resume(bot: Bot) -> None:
    """Pause DES PAARES aufheben, dessen pausierte Tasks zurücksetzen.

    Manuell geparkte Aufgaben (/loeschen … p) tragen status_vor_pause="pausiert"
    und bleiben dadurch geparkt (Review D8/M7) – nur Safeword-pausierte kehren
    in ihren echten Vorher-Status zurück."""
    for task in await qdrant.get_tasks_by_status(["pausiert"]):
        point_id = task.get("qdrant_point_id")
        if point_id:
            vorher = task.get("status_vor_pause") or "offen"
            if vorher != "pausiert":
                await qdrant.update_task(point_id, {"status": vorher})

    paar = paare.paar_im_kontext()
    state.set_paused(False)
    # Not-Stopp bleibt Not-Stopp: Modes hart auf chat – aber die State-Keys des
    # abgebrochenen Flows mit aufräumen, sonst fehlrouten Leichen (z.B.
    # bewertung_task_id) die nächste Eingabe (Review D6).
    state.set_mode(paar.dom_chat_id, "chat")
    state.set_mode(paar.sub_chat_id, "chat")
    state.clear_flow_keys(paar.dom_chat_id)
    state.clear_flow_keys(paar.sub_chat_id)

    msg = t("SAFEWORD_AKTIV")
    await telegram_helper.send_domina(bot, msg)
    await telegram_helper.send_sklave(bot, msg)
