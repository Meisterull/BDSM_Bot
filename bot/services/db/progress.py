"""
progress: Fortschritt, erledigte Kategorien, Training.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from datetime import datetime, timezone
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

async def save_progress(user_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("beschreibung", "")
    vector = await emb.get_embedding(text)

    payload = {
        **data,
        "user_id": kern.mandanten_key(user_id),
        "datum": datetime.now(timezone.utc).isoformat(),
    }
    await kern._aio(kern.client.upsert,
        collection_name="progress",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            payload=payload,
        )],
    )
    return point_id


async def get_completed_task_count(user_id: str) -> int:
    result = await kern._aio(kern.client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        ]),
    )
    return result.count


async def get_completed_kategorien_set(user_id: str) -> set[str]:
    """Set aller Kategorien, in denen der User mind. 1 Task erledigt hat.
    Paginiert vollständig – ein fixes Limit würde ab >500 erledigten Tasks
    eine willkürliche Teilmenge liefern (Vielfalt-Zähler/Abzeichen falsch)."""
    kategorien: set[str] = set()
    offset = None
    while True:
        results, offset = await kern._aio(kern.client.scroll,
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
            ]),
            # Nur das eine gebrauchte Feld transferieren (D9/A3): der Pfad läuft
            # bei jeder Task-Erledigung – Voll-Payloads (komplette Aufgabentexte)
            # waren reiner Ballast.
            limit=500, offset=offset, with_payload=["kategorie"], with_vectors=False,
        )
        kategorien.update(
            r.payload.get("kategorie", "allgemein") for r in results if r.payload.get("kategorie")
        )
        if offset is None:
            break
    return kategorien


async def get_completed_count_by_kategorie(user_id: str, kategorie: str) -> int:
    """Anzahl erledigter Tasks in einer Kategorie."""
    result = await kern._aio(kern.client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value=kategorie)),
        ]),
    )
    return result.count


# ---------------------------------------------------------------------------
# knowledge_base
# ---------------------------------------------------------------------------

async def save_training(user_id: str, data: dict, point_id: str | None = None) -> str:
    """Legt einen Trainings-Eintrag ab. `point_id` vorgeben, wenn die ID schon
    vor dem Speichern gebraucht wird (Knopf unter der Übung trägt sie)."""
    point_id = point_id or str(uuid.uuid4())
    text = data.get("zusammenfassung", "")
    vector = await emb.get_embedding(text)

    payload = {
        **data,
        "user_id": kern.mandanten_key(user_id),
        "datum": datetime.now(timezone.utc).isoformat(),
    }
    await kern._aio(kern.client.upsert,
        collection_name="training",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            payload=payload,
        )],
    )
    return point_id


async def get_training_by_id(point_id: str, user_id: str = "domina") -> dict | None:
    """Ein Trainings-Eintrag per ID. None, wenn es ihn nicht gibt oder er einem
    anderen Paar gehört (die ID kommt aus callback_data, also von außen)."""
    try:
        results = await kern._aio(kern.client.retrieve,
            collection_name="training",
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        logger.error("Fehler beim Laden des Trainings %s: %s", point_id, e)
        return None
    payload = (results[0].payload or {}) if results else {}
    if not payload or payload.get("user_id") != kern.mandanten_key(user_id):
        return None
    return payload


async def update_training(point_id: str, updates: dict) -> None:
    """Ergänzt einen Trainings-Eintrag (Antwort + Rückmeldung kommen später als die Übung)."""
    await kern._aio(kern.client.set_payload,
        collection_name="training",
        payload=updates,
        points=[point_id],
    )


async def get_training_entries(user_id: str, limit: int = 10) -> list[dict]:
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


async def get_progress_entries(user_id: str, limit: int = 10) -> list[dict]:
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="progress",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]
