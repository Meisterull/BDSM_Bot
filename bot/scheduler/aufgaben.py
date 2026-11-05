"""
Follow-up-Fragen zu offenen Aufgaben, Serien- und Ketten-Fortschritt.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.services import sticker_reaktionen
from bot.prompts import followup as fp
from bot.messages import t
from bot.scheduler import basis, tiny_task

logger = logging.getLogger(__name__)


async def _process_serie_tasks(bot: Bot) -> bool:
    """Aktiviert fällige Serie-Tasks. True = in diesem Lauf ging eine Serien-
    Aufgabe an den Sub (dann ist sein Mode 'followup' kein blockierter Lauf)."""
    gesendet = False
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                # Mandanten-Filter: läuft pro Paar (followup_job) – ohne ihn
                # würde Paar 1 die Serie-Tasks ALLER Paare aktivieren und in
                # seinem Kontext zustellen (Trace 06.07., Lücke 10).
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="serie_wartend")),
                qm.FieldCondition(key="follow_up_datum", range=qm.DatetimeRange(lte=now_iso)),
            ]),
            # Aufsteigend nach Fälligkeit (Review D8/M11): sind nach Downtime
            # mehrere Serien-Tage fällig, wird sonst pro Lauf ein willkürlicher
            # aktiviert – Tag 3 könnte vor Tag 2 kommen, obwohl Serien als
            # aufbauender Bogen generiert werden.
            order_by=qm.OrderBy(key="follow_up_datum", direction="asc"),
            limit=50, with_payload=True, with_vectors=False,
        )
        tasks = [r.payload for r in results]

        for task in tasks:
            point_id = task.get("qdrant_point_id")
            aufgabe = task.get("aufgabe", "")
            tag = task.get("serie_tag", "?")
            gesamt = task.get("serie_gesamt", "?")

            # Mode-Check: nicht senden wenn Sklave in aktivem Flow
            sklave_mode = state.get_mode(paare.sub_chat_id())
            if sklave_mode not in ("chat", None):
                logger.info("Serie Task übersprungen – Sklave in Mode '%s'", sklave_mode)
                if not gesendet:
                    basis._melde_blockiert(paare.sub_chat_id())
                break

            anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe), max_tokens=250)
            # Re-Check NACH dem LLM-Await (TOCTOU): Pause/Flow kann sich im
            # Fenster geändert haben.
            if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
                logger.info("Serie Task nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
                if not gesendet and not state.is_paused():
                    basis._melde_blockiert(paare.sub_chat_id())
                break
            await telegram_helper.send_sklave(bot, anweisung, voice_text=anweisung)
            gesendet = True
            # Status/State erst NACH erfolgreichem Senden – sonst landet der Sklave
            # im Followup-Mode für eine Aufgabe, die er (bei Sende-/LLM-Fehler) nie erhält.
            sklave_chat = paare.sub_chat_id()
            await qdrant.update_task(point_id, {"status": "offen"})
            # Returnwert beachten (Review D6): schlägt set_followup_task fehl
            # (Mode-Wechsel im Sende-Fenster), zeigt der In-Memory-State nicht
            # auf den Task und die Antwort würde als Freitext geroutet –
            # restore_state beim nächsten Start bzw. followup_job (status offen +
            # follow_up_datum) holen die Frage dann nach.
            if not state.set_followup_task(sklave_chat, point_id):
                logger.warning("Serie Task %s gesendet, aber Followup-State blockiert – "
                               "Nachfrage kommt über den regulären Followup-Zyklus.", point_id)
            logger.info("Serie Task aktiviert: Tag %s/%s", tag, gesamt)
    except Exception as e:
        logger.exception("Fehler bei Serie Tasks")
    return gesendet


# Hängende Ketten: so lange wartet der Sweep ab der ersten Beobachtung (bzw.
# zwischen zwei Nachfragen), bevor er die Domina (erneut) fragt. Puffer, damit
# frische Weiter/Abbruch- und Anpassungs-Entscheidungen nicht überfahren werden.
_KETTE_SWEEP_WARTE_TAGE = 2


# Glied-Status, bei denen die Kette normal weiterläuft (kein Eingriff nötig).
_KETTE_AKTIVE_STATUS = ("offen", "gefragt", "gefuehl_pending")


async def _process_kette_tasks(bot: Bot) -> None:
    """Fangnetz für hängende Ketten (Review D8/H3): der Ketten-Fortschritt ist
    rein event-getrieben (Erledigt-Pfad, Fehlschlag-Buttons) – wird ein Glied
    gelöscht oder eine Entscheidungsfrage nie beantwortet, wartete der Rest
    vorher für immer auf `kette_wartend` (Live-Fall: Kette hing seit 17.05.).

    Vorgehen konservativ („nichts wird still angewendet"): eine Kette, deren
    niedrigstes wartendes Glied nur noch terminale Vorgänger hat, wird erst
    per `kette_sweep_am` beobachtet; besteht der Zustand nach
    _KETTE_SWEEP_WARTE_TAGE weiter, bekommt die Domina die bestehende
    Weiter/Abbruch-Buttonfrage (kettefail-Callback, inkl. Doppel-Tap-Guard)."""
    try:
        wartende = await qdrant.get_tasks_by_status(["kette_wartend"])
        ketten: dict[str, dict] = {}
        for task in wartende:
            kid = task.get("kette_id")
            if not kid:
                continue
            bisher = ketten.get(kid)
            if bisher is None or task.get("kette_position", 0) < bisher.get("kette_position", 0):
                ketten[kid] = task

        now = datetime.now(timezone.utc)
        from bot.handlers import kette_adaptiv  # lazy (Handler-Import im Scheduler)
        for kid, glied in ketten.items():
            alle = await qdrant.get_gruppen_glieder("kette_id", kid)
            pos = glied.get("kette_position", 0)
            vorgaenger_aktiv = any(
                g.get("kette_position", 0) < pos and g.get("status") in _KETTE_AKTIVE_STATUS
                for g in alle
            )
            if vorgaenger_aktiv:
                continue  # Kette läuft normal – Erledigt-/Fehlschlag-Pfad übernimmt

            gid = glied.get("qdrant_point_id")
            if not gid:
                continue
            sweep_am = glied.get("kette_sweep_am")
            if sweep_am:
                sweep_dt = basis._parse_datum(sweep_am)
                if now - sweep_dt < timedelta(days=_KETTE_SWEEP_WARTE_TAGE):
                    continue  # Beobachtungs-/Entscheidungsfenster läuft noch

            if not sweep_am:
                # Erst nur beobachten – die Domina kann gerade mitten in einer
                # frischen Button-Entscheidung stecken.
                await qdrant.update_task(gid, {"kette_sweep_am": now.isoformat()})
                continue

            # Nicht in einen aktiven Domina-Flow platzen (D9/N10): Frage einfach
            # beim nächsten Lauf stellen – sweep_am bleibt stehen, es geht
            # nichts verloren (kettefail-Buttons sind ohnehin status-geguardet).
            if state.get_mode(paare.dom_chat_id()) not in ("chat", None):
                logger.info("KETTE_HAENGT_FRAGE vertagt – Domina in aktivem Flow.")
                continue

            await telegram_helper.send_domina(
                bot,
                t("KETTE_HAENGT_FRAGE",
                  pos=glied.get("kette_position", "?"),
                  gesamt=glied.get("kette_gesamt", "?"),
                  naechste=glied.get("aufgabe", "")[:200]),
                parse_mode="Markdown",
                reply_markup=kette_adaptiv._fehlschlag_buttons(gid),
            )
            # Throttle: erst nach erneuter Wartezeit wieder nachfragen.
            await qdrant.update_task(gid, {"kette_sweep_am": now.isoformat()})
            logger.info("Kette %s hängt (Glied %s wartet, keine aktiven Vorgänger) – "
                        "Weiter/Abbruch-Frage an Domina gesendet.", kid, gid)
    except Exception:
        logger.exception("Fehler beim Ketten-Fangnetz")


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def followup_job(bot: Bot) -> None:
    logger.info("Follow-up Job gestartet: %s", datetime.now(timezone.utc).isoformat())

    # Liegengebliebenen UI-Flow auto-zurücksetzen, sonst blockiert er den ganzen Job (eval-6)
    state.clear_if_stale(paare.sub_chat_id())

    # Serie Tasks aktivieren
    serie_gesendet = await _process_serie_tasks(bot)

    # Hängende Ketten einsammeln (Review D8/H3)
    await _process_kette_tasks(bot)

    await _followup_fragen(bot, schon_gesendet=serie_gesendet)


    # Tiny Task für Domina läuft als separater Abend-Job (siehe tiny_task_vorschlag_job)


async def _followup_fragen(bot: Bot, schon_gesendet: bool = False) -> None:
    """Stellt die tägliche Nachfrage an den Sub. Scheitert sie an einem
    laufenden Mode, bevor in diesem Lauf irgendetwas an ihn rausging, wird der
    Lauf zum Nachholen gemeldet (live 01.10.2026: unbeantwortete Stimmungsfrage).
    `schon_gesendet`: eine Serien-Aufgabe ging in diesem Lauf bereits raus."""
    gesendet = schon_gesendet
    # Follow-up an Sklave – Existenz-Check zuerst, der Kontext (Streak/Stimmung/
    # nicht_erledigt) wird nur geladen, wenn es überhaupt offene Followups gibt.
    tasks = await qdrant.get_open_followup_tasks()
    if not tasks:
        logger.info("Keine offenen Follow-up Tasks.")
    else:
        sklave_profil = await qdrant.get_user_profile("sklave") or {}
        streak = sklave_profil.get("streak", 0)
        sieben_tage_ago = datetime.now(timezone.utc) - timedelta(days=7)
        nicht_erledigt_tasks = await qdrant.get_tasks_by_status(["nicht_erledigt"])
        letzte_nicht_erledigt = sum(
            1 for t in nicht_erledigt_tasks
            if basis._parse_datum(t.get("follow_up_datum", "")) >= sieben_tage_ago
        )
        aktuelle_stimmung = tiny_task._stimmung_aktuell(await qdrant.get_latest_stimmung("sklave"))

        sklave_chat = paare.sub_chat_id()
        # BEWUSST nur EINE automatische Followup-Frage pro Tag: nach dem ersten Send
        # setzt set_followup_task den Mode auf "followup", wodurch der Mode-Check unten
        # die Schleife abbricht. Das hält den Abend ruhig (kein Verhör). Weitere offene
        # Aufgaben kann der Sklave selbst über /meineaufgaben (meine_aufgaben.py) ansehen
        # und abschließen – jeweils mit derselben Followup-Frage.
        for task in tasks:
            point_id = task.get("qdrant_point_id")
            aufgabe = task.get("aufgabe", "")
            if not point_id or not aufgabe:
                continue
            # Mode-Check: nicht senden wenn Sklave in aktivem Flow (bricht nach der
            # ersten gestellten Frage ab – siehe Kommentar oben).
            sklave_mode = state.get_mode(sklave_chat)
            if sklave_mode not in ("chat", None):
                logger.info("Follow-up übersprungen – Sklave in Mode '%s'", sklave_mode)
                if not gesendet:
                    basis._melde_blockiert(sklave_chat)
                break
            try:
                # Zeitbezug: an welchem Tag war die Aufgabe gedacht? (Followup kommt meist
                # Folgetag.) Kalendertage in Bot-Zeitzone – UTC-Tage würden eine nach
                # Mitternacht erteilte Aufgabe fälschlich als "gestern" labeln.
                tz = ZoneInfo(config.TIMEZONE)
                erteilt = basis._parse_datum(task.get("erteilt_am", ""))
                _tage = (datetime.now(tz).date() - erteilt.astimezone(tz).date()).days
                tage_her = _tage if 0 <= _tage <= 30 else 1  # unplausibel (z.B. leeres Datum) -> Default 1
                prompt = fp.followup_frage(
                    aufgabe, streak=streak, letzte_nicht_erledigt=letzte_nicht_erledigt,
                    stimmung=aktuelle_stimmung, tage_her=tage_her,
                )
                frage = await grok.simple(prompt, max_tokens=250)
                # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann der Sklave
                # einen Flow begonnen oder ein Safeword gesendet haben.
                if state.is_paused() or state.get_mode(sklave_chat) not in ("chat", None):
                    logger.info("Follow-up nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
                    if not gesendet and not state.is_paused():
                        basis._melde_blockiert(sklave_chat)
                    break
                from bot.handlers import followup_response
                # „Ich sehe alles"-Sticker gelegentlich vor der Kontroll-Frage
                await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.AUGE, chance=0.35)
                await bot.send_message(
                    chat_id=sklave_chat, text=frage,
                    reply_markup=followup_response.frage_buttons(point_id),
                )
                # Status/State erst NACH erfolgreichem Senden – sonst holt
                # get_open_followup_tasks (filtert nur status=offen) den Task bei
                # einem Sendefehler nie wieder ab.
                await qdrant.update_task(point_id, {"status": "gefragt"})
                gesendet = True
                # Returnwert beachten (D9/A4, wie der Serie-Pfad): False heißt,
                # ein Flow kam zwischen Re-Check und Set – der Task steht auf
                # 'gefragt', die Chat-Recovery fängt ihn bei seiner nächsten
                # Nachricht ein; das Warn-Log macht den Fall sichtbar.
                if not state.set_followup_task(sklave_chat, point_id):
                    logger.warning("Follow-up %s gesendet, Mode nicht gesetzt (aktiver Flow) "
                                   "– Chat-Recovery übernimmt.", point_id)
                logger.info("Follow-up gesendet für Task: %s", point_id)
            except Exception as e:
                logger.exception("Fehler beim Follow-up für Task %s", point_id)
