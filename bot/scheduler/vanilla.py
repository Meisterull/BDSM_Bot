"""
Vanilla-Modus: täglicher Impuls an BEIDE Partner (Etappe 2, 05.11.2026).

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
