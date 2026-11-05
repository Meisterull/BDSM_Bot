"""
coach_regeln: Lern-Speicher des Coachs.

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
# coach_regeln – Lern-Speicher für den Coach
# typ:    "regel" (verbindlich) | "notiz" (lockerer Hinweis) | "profil_update" (Patch-Träger)
# status: "aktiv" (im Prompt) | "pending" (wartet auf User-Bestaetigung) | "verworfen"
# quelle: "manuell" | "chat_praeferenz" (Präferenz-Detektor, live dominierend)
#         | "auto_profil_pflege" (2-Wochen-Job) | "abgeleitet_ablehnung"
#         | "abgeleitet_bewertung" | "abgeleitet_reflexion"
#         (D9/A4: Enum um die live dominierenden Werte ergänzt)
# ---------------------------------------------------------------------------

async def save_coach_regel(
    user_id: str,
    text: str,
    typ: str = "regel",
    status: str = "aktiv",
    quelle: str = "manuell",
    kontext: str = "",
    profile_user: str = "",
    profile_patch: dict = None,
) -> str:
    """Speichert einen Lern-Eintrag fuer den Coach. Gibt point_id zurueck.

    Bei typ='profil_update' werden zusaetzlich profile_user und profile_patch
    abgelegt; bei Bestaetigung wendet der Callback den Patch auf user_profiles an.
    """
    point_id = str(uuid.uuid4())
    vector = await emb.get_embedding(text)
    payload = {
        "user_id": kern.mandanten_key(user_id),
        "typ": typ,
        "status": status,
        "quelle": quelle,
        "text": text,
        "kontext": kontext,
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "qdrant_point_id": point_id,
    }
    # Nur bei direkt-aktiven Einträgen setzen (D9/A4): das frühere Literal-None
    # für pending/verworfen war das A1-Anti-Muster (None-Defaults im Payload).
    if status == "aktiv":
        payload["bestaetigt_am"] = datetime.now(timezone.utc).isoformat()
    if profile_user:
        payload["profile_user"] = profile_user
    if profile_patch:
        payload["profile_patch"] = profile_patch
    await kern._aio(kern.client.upsert,
        collection_name="coach_regeln",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id


async def get_coach_regel(point_id: str) -> Optional[dict]:
    """Holt einen einzelnen Lern-Eintrag (auch pending/verworfen)."""
    try:
        results = await kern._aio(kern.client.retrieve,
            collection_name="coach_regeln",
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None
    except Exception as e:
        logger.error("Fehler beim Laden der coach_regel %s: %s", point_id, e)
        return None


async def get_active_coach_regeln(user_id: str, limit: int = 50) -> list[dict]:
    """Alle aktiven Regeln/Notizen fuer den Coach-Prompt."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="coach_regeln",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="aktiv")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="asc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def get_pending_coach_regeln(user_id: str, limit: int = 50) -> list[dict]:
    """Abgeleitete Regeln, die noch auf Bestaetigung warten."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="coach_regeln",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="pending")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def set_coach_regel_status(point_id: str, status: str) -> None:
    """Setzt den Status einer Regel (aktiv|verworfen|pending)."""
    payload = {"status": status}
    if status == "aktiv":
        payload["bestaetigt_am"] = datetime.now(timezone.utc).isoformat()
    await kern._aio(kern.client.set_payload,
        collection_name="coach_regeln",
        payload=payload,
        points=[point_id],
    )
