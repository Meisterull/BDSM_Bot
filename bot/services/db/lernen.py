"""
Lerntagebuch und Quiz-Wissen (conversations-Collection, eigene Typen).

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern

logger = logging.getLogger(__name__)



async def save_lerntagebuch(user_id: str, zeitraum: str, inhalt: str) -> str:
    """Speichert eine verdichtete Wochen-Zusammenfassung in knowledge_base."""
    point_id = str(uuid.uuid4())
    vector = await emb.get_embedding(inhalt)
    payload = {
        "user_id": kern.mandanten_key(user_id),
        "typ": "lerntagebuch",
        "zeitraum": zeitraum,
        "inhalt": inhalt,
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "qdrant_point_id": point_id,
    }
    await kern._aio(kern.client.upsert,
        collection_name="knowledge_base",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id


async def get_recent_lerntagebuch(user_id: str, limit: int = 3) -> list[dict]:
    """Holt die letzten Lerntagebuch-Einträge (neueste zuerst)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="lerntagebuch")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def save_quiz_wissen(user_id: str, daten: dict) -> str:
    """Speichert eine Coach-Quiz-Auflösung als Langzeit-Wissen (typ=quiz_wissen).

    daten: thema, frage, inhalt (Auflösung), urteil (RICHTIG/TEILWEISE/FALSCH),
    status ("gelernt" bzw. "offen" bei FALSCH – offene Themen dürfen später
    wiederkommen)."""
    point_id = str(uuid.uuid4())
    vector = await emb.get_embedding(f"{daten.get('thema', '')}: {daten.get('inhalt', '')}")
    payload = {
        **daten,
        "user_id": kern.mandanten_key(user_id),
        "typ": "quiz_wissen",
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "qdrant_point_id": point_id,
    }
    await kern._aio(kern.client.upsert,
        collection_name="knowledge_base",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id


async def get_recent_quiz_wissen(user_id: str, limit: int = 15) -> list[dict]:
    """Letzte Coach-Quiz-Einträge (neueste zuerst) – für Prompt-Injektion (kleine
    limits) und sessionfeste Anti-Wiederholung (größere limits)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="quiz_wissen")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def get_offene_quiz_themen(user_id: str, aelter_als_tage: int = 7) -> list[str]:
    """Themen, die FALSCH beantwortet wurden (status=offen) und deren Eintrag alt
    genug ist – Kandidaten fürs Wieder-Abfragen."""
    schwelle = (datetime.now(timezone.utc) - timedelta(days=aelter_als_tage)).isoformat()
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="quiz_wissen")),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="offen")),
            qm.FieldCondition(key="erstellt_am", range=qm.DatetimeRange(lte=schwelle)),
        ]),
        limit=20, with_payload=True, with_vectors=False,
    )
    return [r.payload.get("thema", "") for r in results if r.payload.get("thema")]
