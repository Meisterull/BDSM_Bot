"""
Privileg-Effekte – Anwendung und Lebenszyklus aktiver Privilegien.

Liest und schreibt 'aktive_privilegien' aus dem Sklave-Profil. Wird vom Scheduler
und Task-Generatoren aufgerufen, um Wirkung der eingelösten + bestätigten
Privilegien zu materialisieren.
"""
import logging
from datetime import datetime, timezone, timedelta

from bot.services import qdrant

logger = logging.getLogger(__name__)


def _aktive(profil: dict) -> list[dict]:
    return profil.get("aktive_privilegien", []) or []


def _ist_aktiv(eintrag: dict) -> bool:
    """Bestätigt, nicht verbraucht und (falls TTL) noch gültig."""
    if not eintrag.get("domina_bestaetigt"):
        return False
    if eintrag.get("verbraucht"):
        return False
    gueltig_bis = eintrag.get("gueltig_bis")
    if gueltig_bis:
        return datetime.now(timezone.utc).isoformat() <= gueltig_bis
    return True


# ---------------------------------------------------------------------------
# Bestätigung – wenn Domina ein Privileg bestätigt, ggf. TTL setzen
# ---------------------------------------------------------------------------

def setze_ttl_bei_bestaetigung(eintrag: dict) -> dict:
    """Setzt 'gueltig_bis' bei zeitbegrenzten Privilegien. Sofort-Privilegien
    (Lob/Überraschung/Geheimnis) sind mit der Bestätigungs-Nachricht der Herrin
    erfüllt → direkt als verbraucht markieren, sonst stehen sie ewig als
    'aktiv' in /stats."""
    wirkung = eintrag.get("wirkung", "")
    if wirkung == "schwierigkeit_niedrig_3tage":
        bis = datetime.now(timezone.utc) + timedelta(days=3)
        eintrag["gueltig_bis"] = bis.isoformat()
    elif wirkung.startswith("sofort_"):
        eintrag["verbraucht"] = True
    return eintrag


# ---------------------------------------------------------------------------
# Anwendungs-Hooks (vom Scheduler/Task-Generator aufgerufen)
# ---------------------------------------------------------------------------

async def hat_pause_tag() -> bool:
    """True wenn ein 'pause_tag'-Privileg aktiv ist. Verbraucht NICHTS (Detektor).
    Der Verbrauch erfolgt erst nach erfolgreichem Versand via verbrauche_wirkung()."""
    profil = await qdrant.get_user_profile("sklave") or {}
    return any(
        _ist_aktiv(p) and p.get("wirkung") == "skip_next_task" for p in _aktive(profil)
    )


async def verbrauche_wirkung(wirkung: str) -> bool:
    """Markiert das erste aktive Privileg mit dieser Wirkung als verbraucht.
    Erst NACH erfolgreichem Send aufrufen – sonst wird das Privileg bei einem
    Generierungs-/Sendefehler wirkungslos verbrannt. Gibt True wenn verbraucht."""
    profil = await qdrant.get_user_profile("sklave") or {}
    aktive = _aktive(profil)
    eintrag = next(
        (p for p in aktive if _ist_aktiv(p) and p.get("wirkung") == wirkung),
        None,
    )
    if not eintrag:
        return False
    eintrag["verbraucht"] = True
    eintrag["verbraucht_am"] = datetime.now(timezone.utc).isoformat()
    # Bewusst Read→Listen-Patch ohne weiteren Schutz: Qdrant kann keine einzelnen
    # Listen-Elemente patchen, und zwischen Read und set_payload liegt kein
    # LLM-/HTTP-Call mehr – das Restfenster ist minimal (Review D5, akzeptiert).
    await qdrant.patch_profile_fields("sklave", {"aktive_privilegien": aktive})
    logger.info("Privileg mit Wirkung '%s' nach erfolgreichem Versand verbraucht.", wirkung)
    return True


async def aktiver_easy_mode() -> bool:
    """True wenn 'easy_mode_3tage' gerade gültig ist (TTL-basiert, nicht verbraucht)."""
    profil = await qdrant.get_user_profile("sklave") or {}
    aktive = _aktive(profil)
    return any(
        _ist_aktiv(p) and p.get("wirkung") == "schwierigkeit_niedrig_3tage"
        for p in aktive
    )


async def hat_naechste_aus_wunsch() -> bool:
    """True wenn ein 'naechste_aus_wunsch'-Privileg aktiv ist. Verbraucht NICHTS
    (Detektor); Verbrauch via verbrauche_wirkung('naechste_aus_wunsch') nach Send."""
    profil = await qdrant.get_user_profile("sklave") or {}
    return any(
        _ist_aktiv(p) and p.get("wirkung") == "naechste_aus_wunsch" for p in _aktive(profil)
    )


# ---------------------------------------------------------------------------
# Cleanup – verbrauchte und abgelaufene Einträge entfernen
# ---------------------------------------------------------------------------

async def cleanup() -> int:
    """Entfernt verbrauchte und abgelaufene Einträge aus 'aktive_privilegien'.
    Returns Anzahl entfernter Einträge."""
    profil = await qdrant.get_user_profile("sklave") or {}
    aktive = _aktive(profil)
    if not aktive:
        return 0

    jetzt = datetime.now(timezone.utc).isoformat()
    behalten = []
    for p in aktive:
        # Verbraucht? Weg.
        if p.get("verbraucht"):
            continue
        # Abgelaufen? Weg.
        if p.get("gueltig_bis") and p["gueltig_bis"] < jetzt:
            continue
        # Verweigert + älter als 7 Tage? Weg (sonst sammelt sich Müll).
        if not p.get("domina_bestaetigt"):
            eingeloest = p.get("eingeloest_am", "")
            sieben_tage_alt = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            if eingeloest and eingeloest < sieben_tage_alt:
                continue
        behalten.append(p)

    entfernt = len(aktive) - len(behalten)
    if entfernt > 0:
        await qdrant.patch_profile_fields("sklave", {"aktive_privilegien": behalten})
        logger.info("Privileg-Cleanup: %d Einträge entfernt.", entfernt)
    return entfernt
