"""
Vanilla-Modus: täglicher Impuls an BEIDE Partner (Etappe 2, 05.11.2026),
seit Etappe 3 als Aufgabe gespeichert (quelle=vanilla_impuls, user_id=Slot) mit
Nachfrage am Folgetag (vanilla_followup_job), dazu Termin-Erinnerung an beide
(vanilla_termin_job) und sonntags der gemeinsame Ideen-Plan (vanilla_wochenplan_job).

Ersetzt im gleichberechtigten Modus den Tiny-Task-Vorschlag (der geht an die
Dom-Seite zur Freigabe – hier gibt es niemanden, der freigibt). Die neutrale
Stimme schickt jeder Person direkt EINE kleine Idee für heute, aus dem
Vanilla-Katalog, gewichtet nach den eigenen Vorlieben, mit den No-Gos beider
Personen als Gate. Gespeichert in knowledge_base (typ=vanilla_impuls, user_id
= Slot) als „nicht wiederholen“-Liste für Prompt und Chat.
"""
import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from telegram import Bot
from qdrant_client import models as qm
from bot import config
from bot.services import paare, qdrant, embeddings, limits_check, kategorie_logik, zeiten, labels
from bot.services.qdrant import client
from bot.prompts import vanilla as vp
from bot.messages import t
from bot.scheduler import basis

logger = logging.getLogger(__name__)

TYP = "vanilla_impuls"


async def letzte_impulse(rolle: str, limit: int = 6) -> list[str]:
    """Kurzlabels der letzten Impulse an `rolle` (neueste zuerst)."""
    results, _ = await qdrant.run_io(
        client.scroll, collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key(rolle))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value=TYP)),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload.get("kurzlabel") or r.payload.get("inhalt", "")[:120] for r in results]


async def letzte_kategorien(rolle: str, limit: int = 6) -> list[str]:
    results, _ = await qdrant.run_io(
        client.scroll, collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key(rolle))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value=TYP)),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload.get("kategorie", "") for r in results if r.payload.get("kategorie")]


async def _speichere(rolle: str, inhalt: str, kategorien: list[str]) -> None:
    inhalt = inhalt.strip()[:4000]
    try:
        kurzlabel = await labels.kurzlabel(inhalt)
        vector = await embeddings.get_embedding(inhalt)
        await qdrant.run_io(client.upsert, collection_name="knowledge_base", points=[qm.PointStruct(
            id=str(uuid.uuid4()), vector={"text": vector},
            payload={
                "user_id": qdrant.mandanten_key(rolle), "typ": TYP, "status": "gesendet",
                "kategorien": kategorien, "kategorie": kategorien[0] if kategorien else "allgemein",
                "inhalt": inhalt, "kurzlabel": kurzlabel,
                "erstellt_am": datetime.now(timezone.utc).isoformat(),
            })])
    except Exception:
        logger.exception("Vanilla-Impuls nicht speicherbar (%s)", rolle)


async def _impuls_fuer(bot: Bot, rolle: str, profil: dict, partner_profil: dict,
                       dom_profil: dict) -> bool:
    paar = paare.paar_im_kontext()
    chat_id = paar.chat_id(rolle)
    if basis._flow_aktiv(chat_id, f"Vanilla-Impuls ({rolle})"):
        return False
    letzte = await letzte_kategorien(rolle)
    # Katalog-Auswahl mit der gewichteten Logik (Vorlieben als Basis, Nachbarn
    # als Exploration); der Dom-Slot hat seine Vorlieben historisch in „interessen“.
    sicht = dict(profil)
    sicht.setdefault("vorlieben", vp.vorlieben(profil, rolle))
    kategorien = kategorie_logik.gewichtete_auswahl(sicht, letzte_kategorien=letzte, count=2)
    stimmung_entry = await qdrant.get_latest_stimmung(rolle, max_stunden=30)
    stimmung = stimmung_entry.get("zusammenfassung", "") if stimmung_entry else ""
    from bot.services import inventar
    inventar_impuls = await inventar.impuls_wahl()
    system, prompt = vp.impuls(
        rolle, kategorien, profil, partner_profil,
        letzte_impulse=await letzte_impulse(rolle), stimmung=stimmung,
        inventar_impuls=inventar_impuls or "",
    )
    andere = vp.andere_rolle(rolle)
    text = await limits_check.generate_mit_limit_retry(
        prompt, vp.no_gos(profil, rolle), vp.no_gos(partner_profil, andere), system=system)
    if not text:
        logger.error("Vanilla-Impuls (%s) auch nach Re-Generierung grenzverletzend – verworfen.", rolle)
        return False
    if basis._nach_llm_verworfen(chat_id, f"Vanilla-Impuls ({rolle})"):
        return False
    await bot.send_message(chat_id=chat_id, text=t("VANILLA_IMPULS_PREFIX") + text[:3900])
    await _speichere(rolle, text, kategorien)
    # Etappe 3: der Impuls ist eine Aufgabe der Person – Nachfrage am Folgetag.
    await qdrant.erstelle_task(text[:3900], kategorien[0] if kategorien else "allgemein", 1,
                               quelle="vanilla_impuls", user_id=rolle, extra={"erteilt_von": "stimme"})
    from bot import state
    state.add_message(chat_id, "assistant", text)
    logger.info("Vanilla-Impuls gesendet (%s, Kategorien: %s).", rolle, kategorien)
    return True


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def vanilla_impuls_job(bot: Bot) -> None:
    """Täglich (tiny_task_time): je ein Impuls an beide Personen, im
    kinderfreien Fenster des Paares."""
    from bot.services import modi
    if not modi.ist_vanilla():
        return
    dom_profil = await qdrant.get_user_profile(paare.ROLLE_DOM) or {}
    sub_profil = await qdrant.get_user_profile(paare.ROLLE_SUB) or {}
    if not (dom_profil.get("onboarding_abgeschlossen") and sub_profil.get("onboarding_abgeschlossen")):
        logger.info("Vanilla-Impuls übersprungen – Onboarding nicht auf beiden Seiten abgeschlossen.")
        return
    jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
    if not zeiten.ist_im_fenster(jetzt_lokal, dom_profil.get("kinderfreie_zeiten", []) or []):
        logger.info("Vanilla-Impuls übersprungen – außerhalb der kinderfreien Zeiten.")
        return
    for rolle, profil, partner in ((paare.ROLLE_DOM, dom_profil, sub_profil),
                                   (paare.ROLLE_SUB, sub_profil, dom_profil)):
        try:
            await _impuls_fuer(bot, rolle, profil, partner, dom_profil)
        except Exception:
            logger.exception("Vanilla-Impuls für %s fehlgeschlagen", rolle)


# ---------------------------------------------------------------------------
# Etappe 3: Nachfrage, Termin-Erinnerung, Wochenplan
# ---------------------------------------------------------------------------

@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def vanilla_followup_job(bot: Bot) -> None:
    """Täglich (followup_time): je Person höchstens EINE Nachfrage zu einer
    fälligen Aufgabe (✅/❌-Knöpfe, kein Chat-Mode)."""
    from bot.services import modi
    from bot.handlers import vanilla_spiele
    if not modi.ist_vanilla():
        return
    for rolle in (paare.ROLLE_DOM, paare.ROLLE_SUB):
        chat_id = paare.paar_im_kontext().chat_id(rolle)
        if basis._flow_aktiv(chat_id, f"Vanilla-Nachfrage ({rolle})"):
            continue
        tasks = await qdrant.get_open_followup_tasks(user_id=rolle)
        tasks = [x for x in tasks if x.get("quelle") in vanilla_spiele.QUELLEN_AUFGABE and x.get("qdrant_point_id")]
        if not tasks:
            continue
        task = sorted(tasks, key=lambda x: x.get("erteilt_am", ""))[0]
        try:
            await vanilla_spiele.nachfrage_senden(bot, rolle, task)
            logger.info("Vanilla-Nachfrage gesendet (%s, Task %s).", rolle, task.get("qdrant_point_id"))
        except Exception:
            logger.exception("Vanilla-Nachfrage (%s) fehlgeschlagen", rolle)


@basis._job_guard
async def vanilla_termin_job(bot: Bot) -> None:
    """Alle 15 Min: fällige Termine (quelle=vanilla_termin, status=geplant) an
    BEIDE erinnern. Danach status=erinnert (keine Nachfrage – ein Date braucht
    keine Kontrolle)."""
    from bot.services import modi
    if not modi.ist_vanilla():
        return
    jetzt = datetime.now(timezone.utc)
    paar = paare.paar_im_kontext()
    for rolle in (paare.ROLLE_DOM, paare.ROLLE_SUB):
        for task in await qdrant.get_tasks_by_status(["geplant"], user_id=rolle, quelle="vanilla_termin"):
            if basis._parse_datum(task.get("zustellung_ab", "")) > jetzt or not task.get("qdrant_point_id"):
                continue
            pid = task["qdrant_point_id"]
            await qdrant.update_task(pid, {"status": "erinnert"})
            try:
                from bot.services import telegram_helper
                text = t("VANILLA_TERMIN_HEUTE", text=telegram_helper.md_einbett_sicher(task.get("aufgabe", "")[:500]))
                for r in (paare.ROLLE_DOM, paare.ROLLE_SUB):
                    await telegram_helper.send_an(bot, paar, r, text, parse_mode="Markdown")
                logger.info("Vanilla-Termin erinnert (%s).", pid)
            except Exception:
                await qdrant.update_task(pid, {"status": "geplant"})
                raise


async def _letzte_wochenplaene(limit: int = 2) -> list[str]:
    results, _ = await qdrant.run_io(
        client.scroll, collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key(paare.ROLLE_DOM))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="vanilla_wochenplan")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload.get("inhalt", "") for r in results]


async def wochenplan_senden(bot: Bot, thema: str = "") -> bool:
    """Gemeinsamer Ideen-Plan an beide (gleicher Text); True bei Versand."""
    from bot.services import telegram_helper
    dom = await qdrant.get_user_profile(paare.ROLLE_DOM) or {}
    sub = await qdrant.get_user_profile(paare.ROLLE_SUB) or {}
    try:
        letzte = await _letzte_wochenplaene()
    except Exception:
        letzte = []
    system, prompt = vp.wochenplan(dom, sub, thema=thema, letzte_plaene=letzte)
    text = await limits_check.generate_mit_limit_retry(
        prompt, vp.no_gos(dom, paare.ROLLE_DOM), vp.no_gos(sub, paare.ROLLE_SUB), system=system)
    if not text:
        logger.error("Vanilla-Wochenplan auch nach Re-Generierung grenzverletzend – verworfen.")
        return False
    paar = paare.paar_im_kontext()
    for r in (paare.ROLLE_DOM, paare.ROLLE_SUB):
        await telegram_helper.send_an(bot, paar, r, t("VANILLA_WOCHENPLAN_TITEL") + "\n\n" + text[:3800])
    try:
        vector = await embeddings.get_embedding(text[:2000])
        await qdrant.run_io(client.upsert, collection_name="knowledge_base", points=[qm.PointStruct(
            id=str(uuid.uuid4()), vector={"text": vector},
            payload={"user_id": qdrant.mandanten_key(paare.ROLLE_DOM), "typ": "vanilla_wochenplan",
                     "inhalt": text[:4000], "erstellt_am": datetime.now(timezone.utc).isoformat()})])
    except Exception:
        logger.exception("Vanilla-Wochenplan nicht speicherbar")
    logger.info("Vanilla-Wochenplan gesendet (Thema: %s).", thema or "–")
    return True


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def vanilla_wochenplan_job(bot: Bot) -> None:
    """Sonntags (wochenplanung_time): Ideen-Plan an beide."""
    from bot.services import modi
    if not modi.ist_vanilla():
        return
    paar = paare.paar_im_kontext()
    if basis._flow_aktiv(paar.dom_chat_id, "Vanilla-Wochenplan") or basis._flow_aktiv(paar.sub_chat_id, "Vanilla-Wochenplan"):
        return
    dom = await qdrant.get_user_profile(paare.ROLLE_DOM) or {}
    sub = await qdrant.get_user_profile(paare.ROLLE_SUB) or {}
    if not (dom.get("onboarding_abgeschlossen") and sub.get("onboarding_abgeschlossen")):
        return
    await wochenplan_senden(bot)
