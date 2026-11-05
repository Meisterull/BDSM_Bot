"""
geheimnisse: zeitgesteuerte Geheimnisse.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# geheimnisse
# ---------------------------------------------------------------------------

async def save_geheimnis(data: dict, user_id: str = "domina") -> str:
    """`user_id` = Mandanten-Key der Dom-Seite (Geheimnisse gehören der Domina).
    Bisher hatte die Collection GAR KEIN user_id-Feld – ab jetzt Pflicht
    (Live-Bestand war leer, daher keine Daten-Migration nötig)."""
    point_id = str(uuid.uuid4())
    text = data.get("text", "")
    vector = await emb.get_embedding(text)

    await kern._aio(kern.client.upsert,
        collection_name="geheimnisse",
        points=[qm.PointStruct(
            id=point_id,
            vector={"text": vector},
            # Pflichtfelder NACH **data (D9/A4, Hermes-C4-Muster): ein Caller-data
            # mit user_id-Feld darf den Mandanten-Key nicht überschreiben.
            payload={**data, "user_id": kern.mandanten_key(user_id), "qdrant_point_id": point_id},
        )],
    )
    return point_id


async def get_faellige_geheimnisse(user_id: Optional[str] = "domina") -> list[dict]:
    """Gibt alle Geheimnisse zurück, deren Enthüllungsdatum erreicht oder überschritten ist.
    `user_id`-Semantik wie get_tasks_by_status (None = global, nur Betreiber-Tooling)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    must = [
        qm.FieldCondition(key="status", match=qm.MatchValue(value="wartend")),
        qm.FieldCondition(
            key="enthuellung_datum",
            range=qm.DatetimeRange(lte=now_iso),
        ),
    ]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))))
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="geheimnisse",
        scroll_filter=qm.Filter(must=must),
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]
