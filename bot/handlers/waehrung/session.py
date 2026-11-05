"""
Session-Privileg.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
from bot.services import qdrant
from bot.messages import t

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Session-Privileg
# ---------------------------------------------------------------------------

async def session_wunsch_task() -> str | None:
    """Nach Bestätigung des Privilegs „Session nach Wunsch": offene Aufgabe mit
    7-Tage-Nachfrage – Kategorien aus den Wunsch-Kategorien des Subs."""
    try:
        profil = await qdrant.get_user_profile("sklave") or {}
        dom = await qdrant.get_user_profile("domina") or {}
        kats = list(profil.get("wunsch_kategorien", []) or [])
        from bot.services import kategorie_logik
        anzeige = kategorie_logik.anzeige_liste(kats) if kats else t("SESSION_WUNSCH_OHNE_KATEGORIE")
        text = t("SESSION_WUNSCH_AUFGABE", kategorien=anzeige)
        task_id = await qdrant.erstelle_task(
            text, kats[0] if kats else "allgemein", int(dom.get("aktuelles_level", 1) or 1),
            status="offen", quelle="privileg", followup_in_tagen=7,
            extra={"privileg_id": "session_wunsch"})
        logger.info("Session-nach-Wunsch-Aufgabe angelegt (Task %s).", task_id)
        return task_id
    except Exception:
        logger.exception("Session-nach-Wunsch-Aufgabe konnte nicht angelegt werden")
        return None
