"""
conversations: Speichern, Kontext-Suche (hybrid), Zeitraum-Abfragen.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------

async def save_conversation(user_id: str, session_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("zusammenfassung", "")
    vector = await emb.get_embedding(text)

    # Pflichtfelder NACH **data (Hermes-Review C4, Muster save_strafe): ein
    # Caller-data mit user_id-Feld darf den Mandanten-Key nie überschreiben.
    payload = {
        **data,
        "user_id": kern.mandanten_key(user_id),
        "session_id": session_id,
        "datum": datetime.now(timezone.utc).isoformat(),
    }
    await kern._aio(kern.client.upsert,
        collection_name="conversations",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            payload=payload,
        )],
    )
    return point_id


# Felder, die die Chat-Kontext-Konsumenten (format_context, _erinnerung,
# _diversify_by_thema) tatsächlich lesen. Die Volltextfelder (domina_nachricht/
# coach_antwort/sklave_nachricht/herrin_antwort, mehrere KB je Punkt) braucht
# nur das Dossier – Chat-Pfade übergeben diese Liste (Review D8/N3).
KONTEXT_FELDER = ["datum", "zusammenfassung", "wichtige_punkte", "themen", "thema", "session_id"]


async def get_conversation_context(user_id: str, query_vector: list[float], limit: int = 5,
                                   felder: list[str] | None = None) -> list[dict]:
    # client.search wurde in qdrant-client 1.18 entfernt -> query_points (seit 1.10).
    res = await kern._aio(kern.client.query_points,
        collection_name="conversations",
        query=query_vector,
        using="text",
        query_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=limit,
        with_payload=felder if felder else True,
    )
    return [p.payload for p in res.points]


def _diversify_by_thema(entries: list[dict], k: int) -> list[dict]:
    """Round-Robin über das `thema`-Feld, damit kein einzelnes Motiv den Kontext
    flutet. Ohne das retrievt sich eine dominante Themenlage selbst immer wieder
    nach (Echokammer) und die Herrin bleibt an zwei Dauer-Motiven hängen. Die
    Reihenfolge innerhalb eines Themas (Recency) bleibt erhalten."""
    if k <= 0:
        return []
    buckets: dict = {}
    reihenfolge: list = []
    for e in entries:
        th = e.get("thema") or "allgemein"
        if th not in buckets:
            buckets[th] = []
            reihenfolge.append(th)
        buckets[th].append(e)
    out: list = []
    while len(out) < k and any(buckets[th] for th in reihenfolge):
        for th in reihenfolge:
            if buckets[th]:
                out.append(buckets[th].pop(0))
                if len(out) >= k:
                    break
    return out


async def get_hybrid_conversation_context(user_id: str, query_vector: list[float] | None, limit: int = 12,
                                           felder: list[str] | None = None) -> list[dict]:
    """Kombiniert neueste + semantisch ähnliche Einträge, ohne Duplikate.

    Default ist großzügig (12), damit der Coach „immer dabei"-Kontext hat.
    Aufteilung: 3 neueste fix (Kontinuität) + bis zu 2 GARANTIERTE Plätze für
    rein semantische Treffer + Rest thematisch diversifiziert.

    Review D8/N3: vorher startete der Pool mit 50 Recency-Voll-Payloads und die
    semantischen Treffer hingen hinten dran – durch kopf=3 + Bucket-Diversify
    schafften sie es praktisch nie in den Kontext (der Embedding-Call zahlte
    für nichts). Jetzt: Recency 24, beide Arme parallel, Semantik-Slots fix.
    `felder` beschränkt den Payload-Transfer (Chat-Pfade: KONTEXT_FELDER);
    None = voller Payload (Dossier braucht die Volltextfelder)."""
    import asyncio

    async def _recent() -> list[dict]:
        results, _ = await kern._aio(kern.client.scroll,
            collection_name="conversations",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
            ]),
            limit=24, order_by=qm.OrderBy(key="datum", direction="desc"),
            with_payload=felder if felder else True, with_vectors=False,
        )
        return [r.payload for r in results]

    if query_vector is None or limit <= 3:
        # query_vector None: Embedding-Ausfall (D9/M11) – Recency-Arm allein
        # statt gar kein Kontext. limit<=3 (D9/A3): kopf=3 füllt das Budget
        # komplett, der semantische Arm würde eh verworfen – Query sparen.
        semantic, recent = [], await _recent()
    else:
        semantic, recent = await asyncio.gather(
            get_conversation_context(user_id, query_vector, limit=8, felder=felder),
            _recent(),
        )
    recent_ids = {e.get("session_id") for e in recent}

    # 3 neueste fix für Kontinuität.
    kopf = recent[:3]

    # Bis zu 2 garantierte Plätze für Treffer, die Recency allein nie zeigen
    # würde (nur bei ausreichendem Budget – bei limit<=4 gewinnt Kontinuität).
    sem_neu = [e for e in semantic if e.get("session_id") not in recent_ids]
    garantiert = sem_neu[:2] if limit > 4 else sem_neu[:1] if limit == 4 else []
    garantiert_ids = {e.get("session_id") for e in garantiert}

    # Rest über die Themen diversifiziert (Recency-Tail + übrige Semantik-Treffer).
    rest_pool = recent[3:] + [
        e for e in sem_neu if e.get("session_id") not in garantiert_ids
    ]
    rest = _diversify_by_thema(rest_pool, limit - len(kopf) - len(garantiert))
    return kopf + garantiert + rest


async def get_conversations_in_range(user_id: str, start_iso: str, end_iso: str, limit: int = 200) -> list[dict]:
    """Holt Konversations-Einträge im Zeitfenster (für Verdichtungs-Job).

    order_by desc: greift das `limit` (mehr Gespräche als limit im Fenster), bleiben
    die NEUESTEN erhalten – nicht die ältesten. Rückgabe ist chronologisch (asc)
    sortiert, damit die Verdichtungs-Prompts die Gespräche in Zeitreihenfolge sehen."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="conversations",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="datum", range=qm.DatetimeRange(gte=start_iso, lte=end_iso)),
        ]),
        limit=limit, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return sorted((r.payload for r in results), key=lambda p: p.get("datum", ""))
