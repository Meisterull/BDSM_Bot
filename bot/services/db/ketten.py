"""
Aufgaben-Ketten und Gruppen-Glieder (tasks-Collection).

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
from typing import Optional
from qdrant_client import models as qm
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Aufgaben-Kette
# ---------------------------------------------------------------------------

async def get_kette_wartende(kette_id: str) -> list[dict]:
    """Alle noch wartenden Glieder einer Kette (für Weiter/Abbruch-Entscheidung
    nach einem gescheiterten Glied), sortiert nach Position. kette_id ist eine
    UUID (kollisionssicher) – der Mandanten-Filter ist Defense-in-Depth."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="kette_id", match=qm.MatchValue(value=kette_id)),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="kette_wartend")),
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key("sklave"))),
        ]),
        limit=100, with_payload=True, with_vectors=False,
    )
    return sorted([r.payload for r in results], key=lambda x: x.get("kette_position", 0))


async def get_gruppen_glieder(feld: str, wert: str, status: list[str] | None = None) -> list[dict]:
    """Alle Glieder einer Serie (feld="serie_id") oder Kette (feld="kette_id"),
    optional auf Status eingeschränkt – für /loeschen `s` (ganze Serie/Kette
    stoppen) und den kette_wartend-Sweep. Sortiert nach Position bzw. Datum."""
    bedingungen = [
        qm.FieldCondition(key=feld, match=qm.MatchValue(value=wert)),
        qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key("sklave"))),
    ]
    if status is not None:
        bedingungen.append(qm.FieldCondition(key="status", match=qm.MatchAny(any=status)))
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=bedingungen),
        limit=100, with_payload=True, with_vectors=False,
    )
    return sorted(
        [r.payload for r in results],
        key=lambda x: (x.get("kette_position", 0), x.get("erteilt_am", "")),
    )


async def get_naechster_ketten_task(kette_id: str, aktuelle_position: int) -> Optional[dict]:
    """Gibt den nächsten wartenden Task einer Kette zurück (niedrigste Position > aktuelle)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="kette_id", match=qm.MatchValue(value=kette_id)),
            qm.FieldCondition(
                key="kette_position",
                range=qm.Range(gt=float(aktuelle_position)),
            ),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="kette_wartend")),
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key("sklave"))),
        ]),
        limit=100, with_payload=True, with_vectors=False,
    )
    payloads = sorted(
        [r.payload for r in results],
        key=lambda x: x.get("kette_position", 0),
    )
    return payloads[0] if payloads else None
