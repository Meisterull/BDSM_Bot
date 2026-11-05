"""
wuensche: Wunschliste des Subs.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from typing import Optional
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# wuensche
# ---------------------------------------------------------------------------

async def save_wunsch(user_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("text", "")
    vector = await emb.get_embedding(text)

    await kern._aio(kern.client.upsert,
        collection_name="wuensche",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            payload={**data, "user_id": kern.mandanten_key(user_id), "qdrant_point_id": point_id},
        )],
    )
    return point_id


async def update_wunsch(point_id: str, updates: dict) -> None:
    await kern._aio(kern.client.set_payload,
        collection_name="wuensche",
        payload=updates,
        points=[point_id],
    )


async def get_wunsch(point_id: str) -> Optional[dict]:
    results = await kern._aio(kern.client.retrieve,
        collection_name="wuensche",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None
