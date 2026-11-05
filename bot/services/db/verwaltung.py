"""
Betreiber-Funktionen: Paar-Daten löschen.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
from qdrant_client import models as qm
from bot.services import paare
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Paar-Daten-Löschung (Betreiber, handlers/admin.py)
# ---------------------------------------------------------------------------

# Alle Collections mit Mandanten-Feld user_id. "skills" ist seit dem
# Paar-Scoping ebenfalls mandanten-gebunden.
_MANDANTEN_COLLECTIONS = (
    "conversations", "knowledge_base", "progress", "tasks", "user_profiles",
    "training", "wuensche", "geheimnisse", "strafen", "coach_regeln", "skills",
)


async def loesche_paar_daten(paar_id: str) -> dict[str, int]:
    """Löscht ALLE Qdrant-Daten eines Paares (beide Mandanten-Keys, alle
    Collections) – unwiderruflich; Backups rotieren erst nach BACKUP_KEEP
    Tagen heraus. Das Env-Paar ("1") ist bewusst ausgeschlossen – seine
    Legacy-Keys "domina"/"sklave" wären zu leicht versehentlich getroffen.
    Rückgabe: gelöschte Punkte pro Collection (-1 = Fehler in der Collection)."""
    if str(paar_id) == paare.LEGACY_PAAR_ID:
        raise ValueError("Env-Paar-Daten werden nicht per Kommando gelöscht")
    keys = [paare.user_id_fuer(paar_id, paare.ROLLE_DOM),
            paare.user_id_fuer(paar_id, paare.ROLLE_SUB)]
    mandanten_filter = qm.Filter(must=[
        qm.FieldCondition(key="user_id", match=qm.MatchAny(any=keys))
    ])
    bericht: dict[str, int] = {}
    for col in _MANDANTEN_COLLECTIONS:
        try:
            anzahl = (await kern._aio(kern.client.count, collection_name=col,
                                 count_filter=mandanten_filter)).count
            if anzahl:
                await kern._aio(kern.client.delete, collection_name=col,
                           points_selector=qm.FilterSelector(filter=mandanten_filter))
            bericht[col] = anzahl
        except Exception:
            logger.exception("Paar-Daten-Löschung: Collection %s fehlgeschlagen", col)
            bericht[col] = -1
    logger.info("Paar-Daten gelöscht (Paar %s): %s", paar_id, bericht)
    return bericht
