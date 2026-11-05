"""
Werkstatt: Aufgabe, Wette, Inspiration, Serie, Kette.

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""
import logging
from bot.services.miniapp import kern

logger = logging.getLogger(__name__)



async def _vorschau_ogg(text: str) -> bytes | None:
    from bot.services import tts
    return await tts.synthesize(text, rolle=tts.ROLLE_HERRIN)


async def _sende_an_sklaven(paar_id: str, text: str) -> None:
    from bot.services import paare, telegram_helper, tts
    with paare.kontext(paar_id):
        await telegram_helper.send_sklave(kern._BOT, tts.entferne_sprech_tags(text),
                                          voice_text=text)


async def _werkstatt_aufgabe(paar_id: str, text: str, kategorie: str) -> dict:
    """Aufgabe aus der Werkstatt erteilen – Muster luecke._erteile_jetzt:
    Limits-Gate, Herrin-Anweisung, Task-Anlage mit Rollback bei Sendefehler.
    KEIN set_followup_task – das Followup kommt über den Scheduler."""
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        treffer = await limits_check.verletzungen(text)
        if treffer:
            # treffer = Dicts (limit/matched_via) – fürs UI nur die Limit-Namen
            return {"limit": ", ".join(sorted({v["limit"] for v in treffer}))}
        profil = await qdrant.get_user_profile("domina") or {}
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(text), max_tokens=250)
        except Exception:
            logger.exception("Werkstatt: aufgabe_an_sklaven fehlgeschlagen – sende Rohtext")
            anweisung = text
        point_id = await qdrant.erstelle_task(
            text, kategorie or "allgemein", profil.get("aktuelles_level", 1),
            quelle="miniapp")
        try:
            await telegram_helper.send_sklave(kern._BOT, anweisung, voice_text=anweisung)
        except Exception:
            await qdrant.loesche_task(point_id)
            raise
    return {"ok": True}


async def _werkstatt_wette(paar_id: str, thema: str) -> dict:
    """🎲 Wettvorschlag aus dem Cockpit: kurzer Status-Check, dann läuft die
    Generierung im Hintergrund und der Vorschlag kommt im Telegram-Chat mit
    📨/🎲 (derselbe Ablauf wie /wette)."""
    from bot.handlers import coach_quiz, waehrung as waehrung_h
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        sklave = await qdrant.get_user_profile("sklave") or {}
        if waehrung_h.wette_offen(sklave):
            return {"fehler": waehrung_h.lage_text(sklave)}
        waehrung_h.im_hintergrund(coach_quiz.wett_vorschlag_auf_abruf(kern._BOT, thema))
    return {"ok": True}


async def _werkstatt_inspiration(paar_id: str) -> dict:
    from bot.handlers import inspiration as insp
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        dp = await qdrant.get_user_profile("domina") or {}
        sp = await qdrant.get_user_profile("sklave") or {}
        _, vorschlaege = await insp._generate_vorschlaege(dp, sp)
    return {"vorschlaege": vorschlaege[:3]}


async def _werkstatt_loeschen(paar_id: str, task_id: str) -> dict:
    """Soft-Delete wie /loeschen (status='geloescht'); nur wirklich offene
    Tasks des Paares – die id muss in der aktuellen Offen-Liste stehen."""
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        offene = await qdrant.get_tasks_by_status(["offen"])
        if task_id not in {t.get("qdrant_point_id") for t in offene}:
            return {"fehler": "Aufgabe ist nicht (mehr) offen"}
        await qdrant.update_task(task_id, {"status": "geloescht"})
    return {"ok": True}


async def _werkstatt_serie(paar_id: str, text: str, kategorie: str, tage: int) -> dict:
    """Serie erteilen – Kern von serie_handler._save_serie_tasks: Variationen-
    Bogen (Fallback Wiederholung), Limits-Check je Tag mit Rückfall auf den
    Basis-Text, Tag 1 offen + zugestellt, Rest serie_wartend (Scheduler)."""
    from bot.handlers.serie_handler import _parse_variationen
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        treffer = await limits_check.verletzungen(text)
        if treffer:
            return {"limit": ", ".join(sorted({v["limit"] for v in treffer}))}
        profil = await qdrant.get_user_profile("domina") or {}
        level = profil.get("aktuelles_level", 1)
        kategorie = kategorie or "allgemein"

        variationen = None
        try:
            raw = await grok.simple(fp.serie_variationen(text, tage, kategorie), reasoning=True)
            variationen = _parse_variationen(raw, tage)
        except Exception:
            logger.exception("Werkstatt-Serie: Variationen fehlgeschlagen – Wiederholung")
        variationen = variationen or [text] * tage
        for i, v in enumerate(variationen):
            if await limits_check.verletzungen(v):
                variationen[i] = text

        import uuid as _uuid
        serie_id = str(_uuid.uuid4())
        for tag in range(tage):
            await qdrant.erstelle_task(
                variationen[tag], kategorie, level,
                status="offen" if tag == 0 else "serie_wartend",
                followup_in_tagen=tag + 1, quelle="miniapp",
                extra={"serie_id": serie_id, "serie_tag": tag + 1, "serie_gesamt": tage},
            )
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(variationen[0]), max_tokens=250)
        except Exception:
            anweisung = variationen[0]
        await telegram_helper.send_sklave(kern._BOT, anweisung, voice_text=anweisung)
    return {"ok": True, "tage": tage}


async def _werkstatt_kette(paar_id: str, glieder: list, kategorie: str) -> dict:
    """Kette erteilen – Muster des /aufgabe-Ketten-Flows (domina.py): ALLE
    Glieder durchs Limits-Gate (D8/H1), Glied 1 offen + zugestellt, Rest
    kette_wartend; Rollback aller Glieder bei Anlage-/Sendefehler."""
    from datetime import datetime, timezone
    import uuid as _uuid
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        for i, g in enumerate(glieder, 1):
            treffer = await limits_check.verletzungen(g)
            if treffer:
                return {"limit": ", ".join(sorted({v["limit"] for v in treffer})),
                        "glied": i}
        profil = await qdrant.get_user_profile("domina") or {}
        level = profil.get("aktuelles_level", 1)
        kette_id = str(_uuid.uuid4())
        jetzt = datetime.now(timezone.utc).isoformat()
        gespeichert = []
        try:
            for position, g in enumerate(glieder, 1):
                task_id = await qdrant.erstelle_task(
                    g, kategorie or "allgemein", level,
                    status="offen" if position == 1 else "kette_wartend",
                    quelle="miniapp",
                    # Kette prüft zeitnah nach – wie der Chat-Flow (follow_up=erteilt_am)
                    extra={"kette_id": kette_id, "kette_position": position,
                           "kette_gesamt": len(glieder), "follow_up_datum": jetzt},
                )
                gespeichert.append(task_id)
            try:
                anweisung = await grok.simple(fp.aufgabe_an_sklaven(glieder[0]), max_tokens=250)
            except Exception:
                anweisung = glieder[0]
            await telegram_helper.send_sklave(kern._BOT, anweisung, voice_text=anweisung)
        except Exception:
            for tid in gespeichert:
                try:
                    await qdrant.loesche_task(tid)
                except Exception:
                    logger.exception("Ketten-Rollback: Glied %s nicht löschbar", tid)
            raise
    return {"ok": True, "glieder": len(glieder)}
