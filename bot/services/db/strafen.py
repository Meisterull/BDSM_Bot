"""
strafen + Vertrauens-Score.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern, profile

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# strafen
# ---------------------------------------------------------------------------

async def save_strafe(data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("bestrafung_text", data.get("aufgabe", ""))
    vector = await emb.get_embedding(text)

    await kern._aio(kern.client.upsert,
        collection_name="strafen",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            payload={**data, "user_id": kern.mandanten_key(data.get("user_id", "sklave")),
                     "qdrant_point_id": point_id},
        )],
    )
    return point_id


async def get_strafen(user_id: str, limit: int = 20) -> list[dict]:
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="strafen",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


async def get_strafe(point_id: str) -> Optional[dict]:
    results = await kern._aio(kern.client.retrieve,
        collection_name="strafen",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None


async def update_strafe(point_id: str, updates: dict) -> None:
    await kern._aio(kern.client.set_payload,
        collection_name="strafen",
        payload=updates,
        points=[point_id],
    )


# ---------------------------------------------------------------------------
# Vertrauens-Score
# ---------------------------------------------------------------------------

async def get_vertrauens_score(user_id: str) -> dict:
    dreissig_tage = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    async def _count(status: str) -> int:
        result = await kern._aio(kern.client.count,
            collection_name="tasks",
            count_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value=status)),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=dreissig_tage)),
            ]),
        )
        return result.count

    import asyncio
    erledigt, nicht_erledigt = await asyncio.gather(  # parallel (D9/A2)
        _count("erledigt"), _count("nicht_erledigt"))

    gesamt = erledigt + nicht_erledigt
    if gesamt == 0:
        return {"score": 50, "stufe": "unbekannt", "erledigt": 0, "nicht_erledigt": 0, "quote": 0}

    quote = erledigt / gesamt
    profil = await profile.get_user_profile(user_id) or {}
    streak = profil.get("streak", 0)
    streak_bonus = min(streak * 2, 20)
    score = min(100, int(quote * 80) + streak_bonus)

    if score >= 80:
        stufe = "sehr hoch 🌟"
    elif score >= 60:
        stufe = "hoch ✅"
    elif score >= 40:
        stufe = "mittel ⚠️"
    else:
        stufe = "niedrig ❌"

    return {
        "score": score,
        "stufe": stufe,
        "erledigt": erledigt,
        "nicht_erledigt": nicht_erledigt,
        "quote": round(quote * 100),
    }
