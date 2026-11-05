"""
Tägliche/wöchentliche Jobs: Rollenspiel, Lernkurve, Training, Stimmung, Ziele, Geheimnis, Wochenplanung, Kommentare, Resurface.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
import random
from datetime import datetime, timezone, timedelta
from telegram import Bot
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, kategorie_logik
from bot.prompts import followup as fp
from bot.prompts import coach_persona
from bot.messages import t
from bot.scheduler import basis, schablonen

logger = logging.getLogger(__name__)


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def rollenspiel_vorschlag_job(bot: Bot) -> None:
    """Fr+Sa 18:00 – schlägt passendes Rollenspiel-Szenario vor wenn keines aktiv."""
    from bot.handlers.rollenspiel import SZENARIEN_BIBLIOTHEK
    domina_chat = paare.dom_chat_id()
    s = state.get(domina_chat)

    # Kein Vorschlag, wenn ein Rollenspiel wirklich LEBT (Mode aktiv und nicht
    # stale; das Fenster zählt seit D9/M1 ab der letzten Nachricht, 3 Tage –
    # s. state.STALE_ROLLENSPIEL_SECONDS). Ein eingeschlafenes Spiel wird hier
    # sauber beendet + gemeldet; verwaiste szenario_*-Keys ohne lebenden Mode
    # (Alt-Bestand/Kanten) ebenso – sonst unterdrücken sie jeden Fr/Sa-Vorschlag
    # für immer still (Trace 06.07.).
    if s.get("szenario_name"):
        verwaist_name = s.get("szenario_name", "")
        stale = state.clear_if_stale(domina_chat)  # räumt Mode + szenario_*-Keys
        if not stale and state.get_mode(domina_chat) == "rollenspiel_aktiv":
            logger.info("Rollenspiel bereits aktiv – kein Vorschlag.")
            return
        from bot.handlers.rollenspiel import _clear_rollenspiel_state
        _clear_rollenspiel_state(s)
        logger.info("Eingeschlafenes/verwaistes Rollenspiel '%s' auto-beendet.", verwaist_name)
        try:
            await telegram_helper.send_domina(bot, t("ROLLENSPIEL_AUTO_BEENDET", name=verwaist_name))
        except Exception:
            logger.exception("Auto-Beendet-Hinweis nicht zustellbar – Vorschlag läuft trotzdem.")

    if basis._flow_aktiv(domina_chat, "Rollenspiel-Vorschlag"):
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    interessen = [i.lower() for i in domina_profile.get("interessen", [])]

    # Passendes Szenario suchen (kat.lower(): die Szenario-Kategorien sind
    # kapitalisiert, die Interessen lowercased – ohne lower() matchte hier NIE
    # etwas und es kam immer das Zufalls-Szenario).
    passende = [
        sz for sz in SZENARIEN_BIBLIOTHEK.values()
        if any(kat.lower() in interessen for kat in sz["aufgaben_kategorien"])
    ]
    szenario = random.choice(passende) if passende else random.choice(list(SZENARIEN_BIBLIOTHEK.values()))

    system = (
        f"Schlag der Domina ein Rollenspiel-Szenario für heute Abend vor – wie eine vertraute Freundin, die eine Idee hat.\n\n"
        f"{coach_persona.fuer_coach_prompt()}\n\n"
        f"{fp._formel_verbot()}\n\n"
        f"Drei bis vier Sätze, locker. Erwähne dass sie es mit /rollenspiel starten kann. "
        f"Keine Rückfrage, ob ihr die Idee gefällt."
    )
    prompt = (
        f"Szenario: '{szenario['name']}' – {szenario['beschreibung']}\n"
        f"Ton: {szenario['ton']}"
    )

    try:
        vorschlag = await grok.simple(prompt, system=system)
        # Gleiche Detektor-Mechanik wie beim Aufgaben-Vorschlag (Live 18.09.: die Idee
        # begann mit dem verbotenen Einstieg und endete mit einer Profil-Rückfrage).
        funde = schablonen._formel_verstoesse(vorschlag or "")
        if funde:
            logger.info("Rollenspiel-Idee nutzt verbotene Schablonen (%s) – generiere einmal neu.",
                        "; ".join(funde))
            neu = await grok.simple(
                prompt + "\n\nACHTUNG: Dein letzter Entwurf hat diese VERBOTENEN Schablonen "
                "benutzt: " + "; ".join(funde) + ". Formuliere die Idee neu – anderer Einstieg, "
                "kein Profil-Abgleich, keine Rückfrage.", system=system)
            if neu and len(schablonen._formel_verstoesse(neu)) <= len(funde):
                vorschlag = neu
        vorschlag = schablonen._meta_rueckfrage_entfernen(schablonen._begruendungs_formel_entfernen(vorschlag or ""))
        if basis._nach_llm_verworfen(domina_chat, "Rollenspiel-Vorschlag"):
            return
        await telegram_helper.send_domina(bot, t("ROLLENSPIEL_IDEE", vorschlag=vorschlag), parse_mode="Markdown")
        logger.info("Rollenspiel-Vorschlag gesendet: %s", szenario["name"])
    except Exception as e:
        logger.exception("Fehler beim Rollenspiel-Vorschlag")


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def lernkurve_job(bot: Bot) -> None:
    """Alle 2 Wochen (gerade ISO-Wochen) – Analyse der Lernkurve an Domina."""
    if not basis._zweiwochen_takt():
        logger.info("Lernkurve-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    if basis._flow_aktiv(paare.dom_chat_id(), "Lernkurve"):
        return
    logger.info("Lernkurve-Job gestartet.")
    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)
    daten = await qdrant.get_lernkurve_daten("sklave")

    # Leer-Guard (Review D8/N6, wie die anderen Verdichtungs-Jobs): bei null
    # Aktivität in 2 Wochen weder Reasoning-Call noch Analyse-über-nichts senden.
    # Versäumnis der Herrin ⏳ (14 Tage): zählt als Aktivität, aber nicht gegen den Sub.
    from bot.handlers import herrin_versaeumnis
    versaeumt_str = await herrin_versaeumnis.prompt_fakten(14)
    if daten["erledigt"] + daten["nicht_erledigt"] == 0 and not versaeumt_str:
        logger.info("Lernkurve-Job übersprungen – keine Task-Aktivität im Zeitraum.")
        return

    system = f"""Du schaust mit der Domina auf die letzten 2 Wochen – wie eine vertraute Freundin, die ihr ehrliches Feedback gibt.

{coach_persona.fuer_coach_prompt()}

Inhaltlich: was lief gut, was nicht, was wäre als nächstes dran. Aber NICHT als nummerierte Liste mit "1. Was gut lief: …". Lass es fließen wie ein Gespräch. 4-6 Sätze insgesamt. Konkret, nicht hohl. Kein [AUFGABE: ...] Tag."""
    prompt = f"""{coach_persona.level_zeile(level)}
Erledigte Tasks: {daten['erledigt']}
Nicht erledigte Tasks (lag am Sklaven): {daten['nicht_erledigt']}
An der Domina selbst hängen geblieben (keine Zeit – zählt nicht gegen den Sklaven):
{versaeumt_str or 'nichts'}
Kategorien: {daten['kategorien']}
Durchschnittliche Bewertung: {daten['avg_bewertung']}★
Beispiel-Tasks: {', '.join(daten['task_details'])}"""

    try:
        analyse = await grok.simple(prompt, system=system, reasoning=True)
        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Lernkurve"):
            return
        await telegram_helper.send_domina(bot, t("LERNKURVE_PREFIX", analyse=analyse), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Fehler beim Lernkurve-Job")


@basis._job_guard
async def training_job(bot: Bot) -> None:
    """Tägliches Psycho-Training – 5 Minuten nach Follow-up."""
    from bot.handlers.training import daily_training
    logger.info("Training-Job gestartet.")
    await daily_training(bot)


@basis._job_guard
async def stimmung_job(bot: Bot) -> None:
    from bot.handlers.stimmung import frage_stellen
    logger.info("Stimmungs-Job gestartet.")
    await frage_stellen(bot)


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def ziel_erinnerung_job(bot: Bot) -> None:
    from bot.handlers.ziele import send_ziel_erinnerung
    # Bisher der einzige Domina-Send-Job ganz ohne Flow-Check (Trace 06.07.)
    if basis._flow_aktiv(paare.dom_chat_id(), "Ziel-Erinnerung"):
        return
    logger.info("Ziel-Erinnerungs-Job gestartet.")
    await send_ziel_erinnerung(bot)


@basis._job_guard
async def geheimnis_job(bot: Bot) -> None:
    """Alle 30 Minuten – enthüllt fällige Geheimnisse an den Sklaven."""
    geheimnisse = await qdrant.get_faellige_geheimnisse()
    for g in geheimnisse:
        point_id = g.get("qdrant_point_id")
        text = g.get("text", "")

        # Nicht in einen aktiven Sklaven-Flow hineinplatzen.
        if state.get_mode(paare.sub_chat_id()) not in ("chat", None):
            logger.info("Geheimnis-Enthüllung übersprungen – Sklave in aktivem Flow.")
            break

        # ZUERST als enthüllt markieren: schlägt das fehl, überspringen wir (kein
        # Senden) und versuchen es nächsten Lauf erneut. So wird das Geheimnis nie
        # alle 30 Min erneut enthüllt, falls das Status-Update nach dem Senden scheitert.
        try:
            await qdrant.run_io(client.set_payload, 
                collection_name="geheimnisse",
                payload={"status": "enthuellt"},
                points=[point_id],
            )
        except Exception:
            logger.exception("Geheimnis-Status konnte nicht gesetzt werden – überspringe (kein Spam)")
            continue

        from bot.prompts import persona
        system = f"""Du bist die Herrin. Enthülle deinem Sklaven jetzt das folgende Geheimnis – als bedeutungsvolle Enthüllung, persönlich und direkt.

{persona.fuer_sklaven_prompt()}

Zwei bis vier Sätze. Kein [AUFGABE: ...] Tag."""

        try:
            nachricht = await grok.simple(f"Geheimnis: {text}", system=system)
            # Re-Check NACH dem LLM-Await (TOCTOU): Pause/Flow kann sich im Fenster
            # geändert haben → Status zurück auf 'wartend', nächster Lauf probiert erneut.
            if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
                logger.info("Geheimnis-Enthüllung nach Generierung verworfen – Pause/Mode geändert.")
                raise RuntimeError("Pause/Mode im LLM-Fenster geändert")
            await telegram_helper.send_sklave(bot, t("GEHEIMNIS_PREFIX", nachricht=nachricht), parse_mode="Markdown")
        except Exception:
            # Send/LLM fehlgeschlagen → nichts ist beim Sklaven angekommen. Status auf
            # 'wartend' zurücksetzen, damit die Enthüllung (transiente Grok-Ausfälle)
            # nächsten Lauf erneut versucht wird – statt dauerhaft verloren zu gehen.
            logger.exception("Fehler bei Geheimnis-Enthüllung – Status zurück auf 'wartend' für Retry")
            try:
                await qdrant.run_io(client.set_payload,
                    collection_name="geheimnisse",
                    payload={"status": "wartend"},
                    points=[point_id],
                )
            except Exception:
                logger.exception("Geheimnis-Status-Rücksetzung fehlgeschlagen")


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def wochenplanung_job(bot: Bot) -> None:
    """Sonntags 10:00 – automatischer Wochenplan für die Domina."""
    from bot.handlers import wochenplanung
    logger.info("Wochenplanung-Job gestartet.")
    if basis._flow_aktiv(paare.dom_chat_id(), "Wochenplanung"):
        return
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_kategorien = await qdrant.get_recent_task_kategorien("sklave", limit=7)
    bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave")

    try:
        plan = await wochenplanung._generiere_wochenplan(
            domina_profile=domina_profile,
            sklave_profile=sklave_profile,
            thema="",
            letzte_kategorien=letzte_kategorien,
            bewertungs_kontext=bewertungs_kontext,
        )
        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Wochenplanung"):
            return
        await wochenplanung.sende_plan(
            bot, paare.dom_chat_id(), plan, t("WOCHENPLAN_TITEL")
        )
        logger.info("Wochenplan gesendet.")
    except Exception as e:
        logger.exception("Fehler beim Wochenplan")


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def kommentar_analyse_job(bot: Bot) -> None:
    """Wöchentlich – analysiert Domina-Kommentare und aktualisiert Persönlichkeitsprofil."""
    if basis._flow_aktiv(paare.dom_chat_id(), "Kommentar-Analyse"):
        return
    logger.info("Kommentar-Analyse-Job gestartet.")
    sieben_tage_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    try:
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                    qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
                ],
                # should = OR: maßgeblich ist, WANN kommentiert wurde – eine vor
                # 10 Tagen erteilte, diese Woche kommentierte Aufgabe fiel mit dem
                # reinen erteilt_am-Filter für immer durch (Trace 06.07.). Der
                # erteilt_am-Zweig bleibt als Fallback für Bestand ohne kommentar_am.
                should=[
                    qm.FieldCondition(key="kommentar_am", range=qm.DatetimeRange(gte=sieben_tage_ago)),
                    qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=sieben_tage_ago)),
                ],
            ),
            limit=50, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
            with_payload=True, with_vectors=False,
        )
        kommentare = [
            {"aufgabe": r.payload.get("aufgabe", ""), "kategorie": r.payload.get("kategorie", ""),
             "kommentar": r.payload.get("domina_kommentar", ""), "datum": r.payload.get("erteilt_am", "")}
            for r in results
            if r.payload.get("domina_kommentar")
        ]
        if not kommentare:
            logger.info("Keine neuen Kommentare diese Woche.")
            return

        kommentar_text = "\n".join(
            f"- Kategorie {k['kategorie']}: \"{k['kommentar']}\""
            for k in kommentare
        )
        system = f"""Du schaust mit der Domina auf ihre Kommentare zu Aufgaben der letzten Woche.

{coach_persona.fuer_coach_prompt()}

3-4 Sätze. Was lief gut, was nicht, was wäre nächste Woche dran. Lass es fließen wie ein Gespräch, nicht als nummerierte Liste. Kein [AUFGABE: ...] Tag."""
        prompt = f"Kommentare nach Kategorie:\n{kommentar_text}"

        analyse = await grok.simple(prompt, system=system)
        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Kommentar-Analyse"):
            return
        await telegram_helper.send_domina(bot, t("KOMMENTAR_ANALYSE_PREFIX", analyse=analyse), parse_mode="Markdown")
        logger.info("Kommentar-Analyse gesendet.")
    except Exception as e:
        logger.exception("Fehler beim Kommentar-Analyse-Job")


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def resurface_job(bot: Bot) -> None:
    """Wöchentlich – holt einen positiv bewerteten Task aus ~3 Monaten zurück."""
    try:
        # Fenster: RESURFACE_TAGE_MIN bis RESURFACE_TAGE_MAX Tage alt
        ende = (datetime.now(timezone.utc) - timedelta(days=config.RESURFACE_TAGE_MIN)).isoformat()
        start = (datetime.now(timezone.utc) - timedelta(days=config.RESURFACE_TAGE_MAX)).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=start, lte=ende)),
            ]),
            limit=100, with_payload=True, with_vectors=False,
        )

        # Nur sehr gut bewertete (4-5 Sterne)
        kandidaten = [
            r.payload for r in results
            if (r.payload.get("domina_bewertung") or 0) >= 4
        ]
        if not kandidaten:
            logger.info("Resurface-Job: keine geeigneten alten Tasks gefunden.")
            return

        # Zufälligen aussuchen
        task = random.choice(kandidaten)
        task_id = task.get("qdrant_point_id", "")
        aufgabe = task.get("aufgabe", "")
        kategorie = task.get("kategorie", "?")
        bewertung = task.get("domina_bewertung", 0)
        datum = task.get("erteilt_am", "")[:10]

        # Speichere ID im Domina-State für Callback
        # Mode-Check: nicht senden wenn Domina in aktivem Flow
        if basis._flow_aktiv(paare.dom_chat_id(), "Resurface"):
            return
        domina_s = state.get(paare.dom_chat_id())
        domina_s["resurface_task_id"] = task_id

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ERNEUT_ERTEILEN"), callback_data=f"resurface:erteilen:{task_id}"),
            InlineKeyboardButton(t("BUTTON_DIESE_WOCHE_NICHT"), callback_data="resurface:skip"),
        ]])
        await telegram_helper.send_domina(
            bot,
            t(
                "RESURFACE_VORSCHLAG", datum=datum,
                kategorie=kategorie_logik.anzeige_name(kategorie),
                sterne="⭐" * bewertung, aufgabe=aufgabe[:300],
            ),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        logger.info("Resurface gesendet: Task %s (Bewertung %d)", task_id, bewertung)
    except Exception as e:
        logger.exception("Fehler beim Resurface-Job")
