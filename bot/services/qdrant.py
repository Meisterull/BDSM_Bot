"""
Qdrant Service – alle Datenbankoperationen.

Collections (bereits verifiziert):
  conversations, knowledge_base, progress, tasks, user_profiles
"""
import asyncio
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from bot import config
from bot.services import embeddings as emb
from bot.services import paare

logger = logging.getLogger(__name__)
client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)

# Blockierende Qdrant-I/O aus den async-Pfaden auf Hintergrund-Threads auslagern,
# damit sie den Event-Loop nicht einfrieren. Bis Multiuser Schritt 7 serialisierte
# EIN Worker alle Zugriffe – mit concurrent_updates wäre das der Durchsatz-
# Flaschenhals über alle Paare. Der REST-QdrantClient basiert auf httpx.Client
# und ist thread-safe. Rollback aufs alte serielle Verhalten: QDRANT_IO_WORKERS=1.
_executor = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("QDRANT_IO_WORKERS", "4"))),
    thread_name_prefix="qdrant-io",
)


def mandanten_key(user_id):
    """Mandanten-Normalisierung an der Persistenz-Grenze (Multiuser Schritt 5).

    Nackte Rollen-Literale "domina"/"sklave" (historisch an ~300 Callsites in
    Handlers/Scheduler) werden über den Paar-Kontext qualifiziert: im Kontext
    des Legacy-/Env-Paars bleibt es bei "domina"/"sklave" (Bestandsdaten!),
    im Kontext von Paar 7 wird daraus "7:domina". Bereits qualifizierte Keys
    und None passieren unverändert – idempotent. Damit bedeutet ein
    Rollen-Literal im Code ab jetzt "diese Rolle IM AKTUELLEN PAAR"."""
    if user_id in (paare.ROLLE_DOM, paare.ROLLE_SUB):
        return paare.user_id_fuer(paare.aktueller_kontext(), user_id)
    return user_id


async def _aio(fn, *args, **kwargs):
    """Führt eine blockierende Client-Operation im Qdrant-I/O-Thread aus."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


async def run_io(fn, *args, **kwargs):
    """Öffentlicher Wrapper für blockierende client.*-Operationen aus anderen Modulen
    (rohe Custom-Scrolls in Handlern/Jobs, die keine eigene qdrant-Funktion haben)."""
    return await _aio(fn, *args, **kwargs)

# ---------------------------------------------------------------------------
# Collection-Setup (idempotent)
# ---------------------------------------------------------------------------

_DENSE = qm.VectorParams(size=config.EMBEDDING_DIM, distance=qm.Distance.COSINE, on_disk=True)
_SPARSE = qm.SparseVectorParams(index=qm.SparseIndexParams(on_disk=True))


def ensure_collections() -> None:
    """Erstellt fehlende Collections. Existierende werden nicht verändert."""
    existing = {c.name for c in client.get_collections().collections}

    def _create(name: str, with_sparse: bool = True):
        if name in existing:
            return
        vectors = {"text": _DENSE}
        sparse = {"sparse": _SPARSE} if with_sparse else {}
        client.create_collection(
            collection_name=name,
            vectors_config=vectors,
            sparse_vectors_config=sparse if with_sparse else None,
            on_disk_payload=True,
        )

    _create("conversations")
    _create("knowledge_base")
    _create("progress")
    _create("tasks")
    _create("user_profiles", with_sparse=False)
    _create("training")
    _create("wuensche")
    _create("geheimnisse", with_sparse=False)
    _create("strafen")
    _create("coach_regeln", with_sparse=False)
    _create("skills", with_sparse=False)
    # Payload Indexes
    _idx = [
        ("conversations",  "user_id",         "keyword",  True),
        ("conversations",  "datum",            "datetime", False),
        ("conversations",  "session_id",       "keyword",  False),
        ("knowledge_base", "user_id",          "keyword",  True),
        ("knowledge_base", "kategorie",        "keyword",  False),
        ("knowledge_base", "level",            "integer",  False),
        ("knowledge_base", "status",           "keyword",  False),
        ("knowledge_base", "typ",              "keyword",  False),
        ("knowledge_base", "erstellt_am",      "datetime", False),
        ("knowledge_base", "feedback_am",      "datetime", False),
        ("progress",       "user_id",          "keyword",  True),
        ("progress",       "datum",            "datetime", False),
        ("progress",       "level",            "integer",  False),
        ("progress",       "thema",            "keyword",  False),
        ("progress",       "typ",              "keyword",  False),
        ("progress",       "arc_id",           "keyword",  False),
        ("progress",       "status",           "keyword",  False),  # Event-Arc-Plaene (event_arc.py)
        ("tasks",          "user_id",          "keyword",  True),
        ("tasks",          "status",           "keyword",  False),
        ("tasks",          "level",            "integer",  False),
        ("tasks",          "erteilt_am",       "datetime", False),
        ("tasks",          "follow_up_datum",  "datetime", False),
        ("tasks",          "kommentar_am",     "datetime", False),
        ("tasks",          "kette_id",         "keyword",  False),
        ("tasks",          "kette_position",   "integer",  False),
        ("tasks",          "kategorie",        "keyword",  False),
        ("tasks",          "quelle",           "keyword",  False),
        ("tasks",          "arc_id",           "keyword",  False),
        ("user_profiles",  "user_id",          "keyword",  True),
        ("user_profiles",  "rolle",            "keyword",  False),
        ("training",     "user_id",          "keyword",  True),
        ("training",     "datum",            "datetime", False),
        ("training",     "typ",              "keyword",  False),
        ("wuensche",     "user_id",          "keyword",  True),
        ("wuensche",     "datum",            "datetime", False),
        ("wuensche",     "status",           "keyword",  False),
        ("geheimnisse",  "user_id",          "keyword",  True),
        ("geheimnisse",  "status",           "keyword",  False),
        ("geheimnisse",  "enthuellung_datum","datetime", False),
        ("strafen",      "user_id",          "keyword",  True),
        ("strafen",      "datum",            "datetime", False),
        ("strafen",      "status",           "keyword",  False),
        ("coach_regeln", "user_id",          "keyword",  True),
        ("coach_regeln", "typ",              "keyword",  False),
        ("coach_regeln", "status",           "keyword",  False),
        ("coach_regeln", "quelle",           "keyword",  False),
        ("coach_regeln", "erstellt_am",      "datetime", False),
        ("skills",       "kategorie",        "keyword",  True),
        ("skills",       "user_id",          "keyword",  False),
        ("skills",       "source",           "keyword",  False),
    ]
    for col, field, ftype, is_tenant in _idx:
        try:
            schema = (
                qm.PayloadSchemaType.KEYWORD  if ftype == "keyword"
                else qm.PayloadSchemaType.DATETIME if ftype == "datetime"
                else qm.PayloadSchemaType.INTEGER
            )
            client.create_payload_index(
                collection_name=col, field_name=field,
                field_schema=qm.KeywordIndexParams(type="keyword", is_tenant=is_tenant)
                if ftype == "keyword"
                else schema,
            )
        except Exception as e:
            # Meist: Index existiert bereits (idempotent). Echte Fehler nicht ganz
            # verschlucken – auf debug loggen, damit sie bei Bedarf sichtbar sind.
            logger.debug("create_payload_index %s.%s übersprungen: %s", col, field, e)

    alle = sorted(c.name for c in client.get_collections().collections)
    logger.info("Qdrant Collections bereit (%d): %s", len(alle), ", ".join(alle))


# ---------------------------------------------------------------------------
# user_profiles
# ---------------------------------------------------------------------------

async def get_user_profile(user_id: str) -> Optional[dict]:
    results, _ = await _aio(client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    return results[0].payload if results else None


async def upsert_user_profile(user_id: str, data: dict) -> str:
    results, _ = await _aio(client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    point_id = str(results[0].id) if results else str(uuid.uuid4())

    # Nur semantisch relevante Felder embedden — verhindert nutzlose Vektoren aus Zahlen/Listen
    embed_data = {k: v for k, v in data.items() if k in _PROFILE_EMBED_FIELDS and v}
    profile_text = " ".join(str(v) for v in embed_data.values()) if embed_data else f"{user_id} profile"
    vector = await emb.get_embedding(profile_text)

    await _aio(client.upsert,
        collection_name="user_profiles",
        points=[qm.PointStruct(
            id=point_id,
            vector={"text": vector},
            payload={**data, "user_id": mandanten_key(user_id)},
        )],
    )
    return point_id


async def _reembed_profile_vector(point_id: str, payload_nach_update: dict) -> None:
    """Profil-Vektor nach Feld-Änderung neu berechnen und GEZIELT schreiben
    (update_vectors). Bewusst kein Full-Upsert: zwischen Payload-Read und Write
    läge sonst der Embedding-HTTP-Call, in dem parallel via set_payload gepatchte
    Felder (punkte/streak) verloren gingen (Lost Update)."""
    embed_data = {k: v for k, v in payload_nach_update.items() if k in _PROFILE_EMBED_FIELDS and v}
    profile_text = (" ".join(str(v) for v in embed_data.values()) if embed_data
                    else f"{payload_nach_update.get('user_id', '?')} profile")
    vector = await emb.get_embedding(profile_text)
    await _aio(client.update_vectors,
        collection_name="user_profiles",
        points=[qm.PointVectors(id=point_id, vector={"text": vector})],
    )


async def patch_profile_fields(user_id: str, fields: dict, erlaube_geschuetzt: bool = False) -> str:
    """Atomares Update einzelner Profil-Felder ohne Full-Overwrite (Race-Condition-safe).
    Nutzt Qdrant set_payload statt upsert mit vollem Profil.

    erlaube_geschuetzt=True NUR für den manuellen /profil-Edit des Owners –
    automatische Schreiber (Detektor, Jobs, Patches) lassen den Default stehen."""
    # Geschützte Felder niemals automatisch patchen
    protected = _PROFILE_PROTECTED_FIELDS & set(fields.keys())
    if protected and not erlaube_geschuetzt:
        logger.warning("patch_profile_fields: Versuch geschützte Felder zu ändern: %s — ignoriert", protected)
        fields = {k: v for k, v in fields.items() if k not in _PROFILE_PROTECTED_FIELDS}
    if not fields:
        return ""
    results, _ = await _aio(client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not results:
        # Kein Profil vorhanden → normal upsert
        return await upsert_user_profile(user_id, {"user_id": mandanten_key(user_id), **fields})

    point_id = str(results[0].id)
    payload = results[0].payload or {}

    # Re-Embed-Trigger = exakt die Embed-Felder – früher stand hier eine eigene
    # (unvollständige) Teilmenge, wodurch Patches von ziele/erfahrungsstand/
    # wunsch_kategorien den Profil-Vektor stale ließen.
    needs_reembed = any(k in _PROFILE_EMBED_FIELDS for k in fields)

    # Felder IMMER gezielt patchen – nie Full-Upsert mit dem stale Read von oben.
    await _aio(client.set_payload,
        collection_name="user_profiles",
        payload=fields,
        points=[point_id],
    )
    if needs_reembed:
        # Re-embed mit aktualisiertem Profil – NUR die semantischen Felder (wie
        # upsert_user_profile), sonst verrauschen Punkte/IDs den Profil-Vektor.
        await _reembed_profile_vector(point_id, {**payload, **fields})
    return point_id


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

async def save_task(task_data: dict) -> str:
    point_id = str(uuid.uuid4())
    task_text = task_data.get("aufgabe", "")
    vector = await emb.get_embedding(task_text)
    indices, values = emb.get_sparse_vector(task_text)

    await _aio(client.upsert,
        collection_name="tasks",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            # user_id kommt aus Caller-Dicts oft als Rollen-Literal → am
            # Persistenz-Eintritt über den Paar-Kontext qualifizieren.
            payload={**task_data, "user_id": mandanten_key(task_data.get("user_id", "sklave")),
                     "qdrant_point_id": point_id},
        )],
    )

    # Auto-Markierung: wenn ein neuer Task explizit aus einem Tiny-Task-Vorschlag
    # erstellt wurde (quelle=tiny_task), gilt der zugehörige Vorschlag als 'übernommen'.
    quelle = task_data.get("quelle", "")
    tiny_task_id = task_data.get("tiny_task_id")  # explizite Verknüpfung
    if quelle == "tiny_task" and tiny_task_id:
        try:
            await mark_tiny_task_status(tiny_task_id, "uebernommen")
        except Exception as e:
            logger.warning("Auto-Markierung Tiny-Task fehlgeschlagen: %s", e)
    elif quelle == "tiny_task":
        # Ohne explizite tiny_task_id NICHT blind den "neuesten" pending Vorschlag
        # markieren – scroll ohne order_by liefert eine willkürliche Teilmenge, das
        # verfälscht den Lern-Loop. Nur warnen.
        logger.warning("save_task: quelle=tiny_task ohne tiny_task_id – keine "
                       "Auto-Markierung (Verknüpfung fehlt).")

    return point_id


def _followup_zeitpunkt_utc(tage: int = 1) -> str:
    """Followup-Zeit DES KONTEXT-PAARES in `tage` Tagen (Bot-Zeitzone), als
    UTC-ISO-String. Lazy-Import: persona_config importiert qdrant (Zyklus)."""
    from zoneinfo import ZoneInfo
    from bot.services import persona_config
    tz = ZoneInfo(config.TIMEZONE)
    hour, minute = map(int, persona_config.zeit("followup_time").split(":"))
    ziel = (datetime.now(tz) + timedelta(days=tage)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return ziel.astimezone(timezone.utc).isoformat()


async def erstelle_task(
    aufgabe: str,
    kategorie: str,
    level: int,
    *,
    status: str = "offen",
    quelle: Optional[str] = None,
    followup_in_tagen: int = 1,
    extra: Optional[dict] = None,
    user_id: str = "sklave",
) -> str:
    """Gemeinsame Task-Factory (Serie/Würfel/Resurface/Wochenplan): einheitliches
    Payload-Schema + FOLLOWUP_TIME-Berechnung an einem Ort. `extra` ergänzt bzw.
    überschreibt Felder (z.B. serie_id/serie_tag/serie_gesamt, resurface_von).
    `user_id` = Mandanten-Key des Sub (paare.Paar.user_id); Default = Env-Paar."""
    payload = {
        "user_id": mandanten_key(user_id),
        "status": status,
        "level": level,
        "aufgabe": aufgabe,
        "kategorie": kategorie,
        "erteilt_am": datetime.now(timezone.utc).isoformat(),
        "follow_up_datum": _followup_zeitpunkt_utc(followup_in_tagen),
        "erteilt_von": "domina",
        "serie_id": None,
        "serie_tag": None,
        "serie_gesamt": None,
        "gefuehl": None,
        "domina_reaktion": None,
    }
    if quelle:
        payload["quelle"] = quelle
    payload.update(extra or {})
    return await save_task(payload)


async def loesche_task(point_id: str) -> None:
    """Task hart löschen – Rollback für 'Task angelegt, aber Zustellung
    fehlgeschlagen' (Blitz/Advent/Lücke): ein nie zugestellter Task darf weder
    ein Followup („hast du … erledigt?" zu einer unbekannten Aufgabe) noch den
    Blitz-Ablauf-Spott auslösen (Trace 06.07., Lücke 5)."""
    await _aio(client.delete,
        collection_name="tasks",
        points_selector=qm.PointIdsList(points=[point_id]),
    )


async def get_task(point_id: str) -> Optional[dict]:
    results = await _aio(client.retrieve,
        collection_name="tasks",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None


async def update_task(point_id: str, updates: dict) -> None:
    await _aio(client.set_payload,
        collection_name="tasks",
        payload=updates,
        points=[point_id],
    )


async def get_tasks_by_status(status_list: list[str], limit: int = 100, sort_by_datum: bool = False,
                              user_id: Optional[str] = "sklave") -> list[dict]:
    """`user_id` = Mandanten-Key (paare.Paar.user_id). Default = Env-Paar ("sklave",
    verifiziert: alle Bestandsdaten tragen diesen Key). None = bewusst global
    über alle Paare (nur für Betreiber-Tooling wie Backup/Migration)."""
    must = [qm.FieldCondition(key="status", match=qm.MatchAny(any=status_list))]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))))
    scroll_kwargs = dict(
        collection_name="tasks",
        scroll_filter=qm.Filter(must=must),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    # Serverseitig nach erteilt_am sortieren – sonst liefert scroll bei limit < total
    # eine willkürliche Teilmenge statt der neuesten Tasks.
    if sort_by_datum:
        scroll_kwargs["order_by"] = qm.OrderBy(key="erteilt_am", direction="desc")
    results, _ = await _aio(client.scroll, **scroll_kwargs)
    return [r.payload for r in results]


async def count_tasks_by_status(status_list: list[str], user_id: Optional[str] = "sklave") -> int:
    """Billiger Existenz-/Mengen-Check ohne Payload-Transfer (für Vorprüfungen).
    `user_id`-Semantik wie get_tasks_by_status."""
    must = [qm.FieldCondition(key="status", match=qm.MatchAny(any=status_list))]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))))
    result = await _aio(client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=must),
    )
    return result.count


async def get_open_followup_tasks(user_id: Optional[str] = "sklave") -> list[dict]:
    """Tasks mit status=offen für heutiges Follow-up. `user_id`-Semantik wie
    get_tasks_by_status."""
    now_iso = datetime.now(timezone.utc).isoformat()
    must = [
        qm.FieldCondition(key="status", match=qm.MatchAny(any=["offen"])),
        qm.FieldCondition(
            key="follow_up_datum",
            range=qm.DatetimeRange(lte=now_iso),
        ),
    ]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))))
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=must),
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------

async def save_conversation(user_id: str, session_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("zusammenfassung", "")
    vector = await emb.get_embedding(text)
    indices, values = emb.get_sparse_vector(text)

    payload = {
        "user_id": mandanten_key(user_id),
        "session_id": session_id,
        "datum": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    await _aio(client.upsert,
        collection_name="conversations",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            payload=payload,
        )],
    )
    return point_id


async def get_conversation_context(user_id: str, query_vector: list[float], limit: int = 5) -> list[dict]:
    # client.search wurde in qdrant-client 1.18 entfernt -> query_points (seit 1.10).
    res = await _aio(client.query_points,
        collection_name="conversations",
        query=query_vector,
        using="text",
        query_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=limit,
        with_payload=True,
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


async def get_hybrid_conversation_context(user_id: str, query_vector: list[float], limit: int = 12) -> list[dict]:
    """Kombiniert neueste + semantisch ähnliche Einträge, ohne Duplikate.

    Default ist großzügig (12), damit der Coach „immer dabei"-Kontext hat.
    Aufteilung: 3 neueste fix (Kontinuität) + Rest thematisch diversifiziert, damit
    nicht ein Dauer-Motiv den gesamten Kontext dominiert.
    """
    semantic = await get_conversation_context(user_id, query_vector, limit=8)

    results, _ = await _aio(client.scroll,
        collection_name="conversations",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=50, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    recent = [r.payload for r in results]

    # Kandidaten-Pool: neueste zuerst, dann semantisch Ähnliche (dedupliziert).
    pool = list(recent)
    seen = {e.get("session_id") for e in recent}
    for e in semantic:
        if e.get("session_id") not in seen:
            pool.append(e)
            seen.add(e.get("session_id"))

    # 3 neueste fix für Kontinuität, der Rest über die Themen diversifiziert.
    kopf = pool[:3]
    rest = _diversify_by_thema(pool[3:], limit - len(kopf))
    return kopf + rest


async def save_lerntagebuch(user_id: str, zeitraum: str, inhalt: str) -> str:
    """Speichert eine verdichtete Wochen-Zusammenfassung in knowledge_base."""
    point_id = str(uuid.uuid4())
    vector = await emb.get_embedding(inhalt)
    payload = {
        "user_id": mandanten_key(user_id),
        "typ": "lerntagebuch",
        "zeitraum": zeitraum,
        "inhalt": inhalt,
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "qdrant_point_id": point_id,
    }
    await _aio(client.upsert,
        collection_name="knowledge_base",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id


async def get_recent_lerntagebuch(user_id: str, limit: int = 3) -> list[dict]:
    """Holt die letzten Lerntagebuch-Einträge (neueste zuerst)."""
    results, _ = await _aio(client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="lerntagebuch")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def get_conversations_in_range(user_id: str, start_iso: str, end_iso: str, limit: int = 200) -> list[dict]:
    """Holt Konversations-Einträge im Zeitfenster (für Verdichtungs-Job).

    order_by desc: greift das `limit` (mehr Gespräche als limit im Fenster), bleiben
    die NEUESTEN erhalten – nicht die ältesten. Rückgabe ist chronologisch (asc)
    sortiert, damit die Verdichtungs-Prompts die Gespräche in Zeitreihenfolge sehen."""
    results, _ = await _aio(client.scroll,
        collection_name="conversations",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="datum", range=qm.DatetimeRange(gte=start_iso, lte=end_iso)),
        ]),
        limit=limit, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return sorted((r.payload for r in results), key=lambda p: p.get("datum", ""))


async def get_lernkurve_daten(user_id: str) -> dict:
    """Auswertung der letzten 2 Wochen für die Lernkurven-Analyse."""
    zwei_wochen_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    erledigt_filter = qm.Filter(must=[
        qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
        qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=zwei_wochen_ago)),
    ])
    tasks, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=erledigt_filter,
        # order_by: bei >50 Treffern die NEUESTEN Details statt willkürlicher Teilmenge
        limit=50, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    # Zählung serverseitig – len(tasks) wäre bei >50 erledigten Tasks zu niedrig
    erledigt_result = await _aio(client.count,
        collection_name="tasks",
        count_filter=erledigt_filter,
    )
    nicht_erledigt_result = await _aio(client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="nicht_erledigt")),
            qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=zwei_wochen_ago)),
        ]),
    )

    task_payloads = [r.payload for r in tasks]
    kategorien: dict = {}
    for t in task_payloads:
        kat = t.get("kategorie", "allgemein")
        kategorien[kat] = kategorien.get(kat, 0) + 1

    bewertungen = [t.get("domina_bewertung", 0) for t in task_payloads if t.get("domina_bewertung")]
    avg_bewertung = sum(bewertungen) / len(bewertungen) if bewertungen else 0

    return {
        "erledigt": erledigt_result.count,
        "nicht_erledigt": nicht_erledigt_result.count,
        "kategorien": kategorien,
        "avg_bewertung": round(avg_bewertung, 1),
        "task_details": [t.get("aufgabe", "")[:80] for t in task_payloads[:5]],
    }


async def get_level_score(user_id: str) -> dict:
    """Berechnet einen Gesamt-Score für das dynamische Level-System."""
    task_count = await get_completed_task_count(user_id)

    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        ]),
        # order_by desc: bei >100 erledigten Tasks deterministisch die neuesten 100
        # (statt arbiträrer Punkt-ID-Teilmenge) für Vielfalt/Bewertungs-Schnitt.
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        limit=100, with_payload=["kategorie", "domina_bewertung"], with_vectors=False,
    )
    payloads = [r.payload for r in results]
    kategorien = set(p.get("kategorie", "allgemein") for p in payloads)
    vielfalt_score = min(len(kategorien), 5)  # gecappt auf 0-5 (fließt *2 ins Level-System)

    # Profil unter demselben Mandanten-Key wie die Tasks (Legacy: "sklave") –
    # vorher hart "sklave", was bei weiteren Paaren das falsche Profil gezogen hätte.
    sklave_profil = await get_user_profile(user_id) or {}
    streak_max = sklave_profil.get("streak_max", 0)
    streak_score = min(streak_max // 5, 5)  # 0-5, alle 5 Streak-Tage 1 Punkt

    bewertungen = [p["domina_bewertung"] for p in payloads if p.get("domina_bewertung")]
    # Nur bewertete Tasks zählen – kein default=3.0 mehr (verzerrt Richtung "normal")
    if bewertungen:
        avg_bewertung = sum(bewertungen) / len(bewertungen)
        bewertungs_score = round(avg_bewertung)  # 1-5
    else:
        bewertungs_score = 3  # Neutral-Fallback nur bei 0 Bewertungen

    gesamt = task_count + (vielfalt_score * 2) + (streak_score * 2) + bewertungs_score

    return {
        "gesamt": gesamt,
        "task_count": task_count,
        "vielfalt": vielfalt_score,
        "streak": streak_score,
        "bewertung": bewertungs_score,
    }


async def get_nicht_erledigt_streak(user_id: str) -> int:
    """Zählt wie viele der letzten Tasks in Folge nicht erledigt wurden."""
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
        ]),
        limit=10, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    streak = 0
    for p in payloads:
        if p.get("status") == "nicht_erledigt":
            streak += 1
        else:
            break
    return streak


async def get_bewertungs_kontext(user_id: str) -> str:
    """Liefert einen Kontext-String über gut/schlecht bewertete Aufgaben-Kategorien."""
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        ]),
        # order_by desc: bei >20 erledigten Tasks die NEUESTEN nehmen, nicht eine
        # arbiträre Teilmenge in Punkt-ID-Reihenfolge.
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        limit=20, with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results if r.payload.get("domina_bewertung")]
    if not payloads:
        return ""

    # "allgemein" ist die Pseudo-Kategorie des Klassifikations-Fallbacks – als
    # Vorlieben-Signal im Prompt nutzlos (Review D7, B4).
    hoch = [k for p in payloads if p.get("domina_bewertung", 0) >= 4
            if (k := p.get("kategorie", "")) and k != "allgemein"]
    niedrig = [k for p in payloads if p.get("domina_bewertung", 0) <= 2
               if (k := p.get("kategorie", "")) and k != "allgemein"]

    kontext = ""
    if hoch:
        kontext += f"Aufgaben die der Domina gut gefielen (4-5★): {', '.join(set(hoch))}\n"
    if niedrig:
        kontext += f"Aufgaben die weniger gefielen (1-2★): {', '.join(set(niedrig))}\n"
    return kontext


async def get_recent_task_kategorien(user_id: str, limit: int = 5) -> list[str]:
    """Kategorien der letzten Tasks (erledigt oder offen) für Ausgleichs-Prüfung."""
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchAny(any=["erledigt", "offen"])),
        ]),
        limit=20,
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return [p.get("kategorie", "allgemein") for p in payloads[:limit]]


async def get_latest_stimmung(user_id: str) -> Optional[dict]:
    """Letzte Stimmungsabfrage aus der training Collection."""
    results, _ = await _aio(client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="stimmung")),
        ]),
        limit=1, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return payloads[0] if payloads else None


async def get_recent_stimmung_fragen(limit: int = 5) -> list[str]:
    """Die zuletzt an den Sub gestellten Stimmungs-Fragen (typ=stimmung_frage) –
    Sperr-Liste gegen die tägliche Wiederholungs-Formulierung (handlers/stimmung)."""
    results, _ = await _aio(client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key("sklave"))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="stimmung_frage")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload.get("zusammenfassung", "") for r in results if r.payload]


def _vorschlag_label(p: dict) -> str:
    """Kurzlabel eines gespeicherten Vorschlags für die "NICHT wiederholen"-Listen
    der Generator-Prompts – Volltexte dort ankern das Modell auf die eigene
    Vorschlags-Formel (Review D7, B1). Bestand ohne `kurzlabel` wird heuristisch
    auf die erste Zeile gekürzt."""
    from bot.services import labels
    return p.get("kurzlabel") or labels.heuristik_label(p.get("inhalt", ""))


async def get_recent_inspirationen(limit: int = 5, user_id: str = "domina") -> list[str]:
    """Letzte vorgeschlagene/abgelehnte Inspirationen aus der knowledge_base –
    als Kurzlabels (nur Kern-Handlung), NICHT als Volltexte.
    `user_id` = Mandanten-Key der Dom-Seite (Default = Env-Paar)."""
    results, _ = await _aio(client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value="inspiration")),
            qm.FieldCondition(key="status", match=qm.MatchAny(any=["vorgeschlagen", "abgelehnt"])),
        ]),
        limit=20, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return [_vorschlag_label(p) for p in payloads[:limit] if p.get("inhalt")]


async def get_pending_tiny_tasks_for_feedback(hours_back: int = 24, user_id: str = "domina") -> list[dict]:
    """Tiny-Tasks die im Zeitraum erstellt wurden und noch Status 'vorgeschlagen' haben."""
    seit = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
    results, _ = await _aio(client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="tiny_task")),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="vorgeschlagen")),
            qm.FieldCondition(key="erstellt_am", range=qm.DatetimeRange(gte=seit)),
        ]),
        limit=20, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def get_tiny_task_by_id(point_id: str) -> Optional[dict]:
    """Holt einen Tiny-Task-Vorschlag aus der knowledge_base."""
    try:
        results = await _aio(client.retrieve,
            collection_name="knowledge_base",
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None
    except Exception as e:
        logger.error("Fehler beim Laden des Tiny-Tasks %s: %s", point_id, e)
        return None


async def mark_tiny_task_status(point_id: str, status: str, grund: str = "") -> None:
    """Setzt Status (uebernommen/abgelehnt) und optional Grund auf einem Tiny-Task."""
    payload_update = {"status": status}
    if grund:
        payload_update["feedback_grund"] = grund
        payload_update["feedback_am"] = datetime.now(timezone.utc).isoformat()
    await _aio(client.set_payload,
        collection_name="knowledge_base",
        payload=payload_update,
        points=[point_id],
    )


async def get_recent_rejected_tiny_tasks(limit: int = 5, user_id: str = "domina") -> list[dict]:
    """Letzte abgelehnte Tiny-Tasks mit Begründung (für Lern-Loop)."""
    results, _ = await _aio(client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="tiny_task")),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="abgelehnt")),
        ]),
        limit=max(limit, 10), order_by=qm.OrderBy(key="feedback_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return [
        {
            "inhalt": p.get("inhalt", ""),
            "kategorien": p.get("kategorien", [p.get("kategorie", "")]),
            "grund": p.get("feedback_grund", ""),
        }
        for p in payloads[:limit]
        if p.get("feedback_grund")
    ]


async def get_recent_tiny_tasks(limit: int = 7, user_id: str = "domina") -> tuple[list[str], list[str], list[str]]:
    """Letzte TinyTask-Vorschläge – gibt (label_liste, kategorie_liste, volltext_liste)
    zurück. Labels statt Volltexte: die Label-Liste geht 1:1 in die Generator-Prompts.
    Die Volltexte sind NUR für deterministische Detektoren im Aufrufer (Opener-/
    Struktur-Wiederholung) – sie dürfen NIE in einen Prompt (Review D7, B1)."""
    results, _ = await _aio(client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="tiny_task")),
        ]),
        limit=max(limit, 10), order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    # Beide Listen aus DEMSELBEN Slice (Review D6: Kategorien kamen vorher aus
    # ALLEN geladenen Payloads → Vielfalt-Liste enthielt Einträge zu nicht
    # zurückgegebenen Inhalten).
    payloads = [r.payload for r in results][:limit]
    inhalte = [_vorschlag_label(p) for p in payloads if p.get("inhalt")]
    volltexte = [p["inhalt"] for p in payloads if p.get("inhalt")]
    kategorien = []
    for p in payloads:
        kats = p.get("kategorien")
        if kats:
            kategorien.extend(kats)
        elif p.get("kategorie"):
            kategorien.append(p["kategorie"])
    return inhalte, kategorien, volltexte


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

async def save_progress(user_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("beschreibung", "")
    vector = await emb.get_embedding(text)
    indices, values = emb.get_sparse_vector(text)

    payload = {
        "user_id": mandanten_key(user_id),
        "datum": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    await _aio(client.upsert,
        collection_name="progress",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            payload=payload,
        )],
    )
    return point_id


async def get_completed_task_count(user_id: str) -> int:
    result = await _aio(client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
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
        results, offset = await _aio(client.scroll,
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
            ]),
            limit=500, offset=offset, with_payload=True, with_vectors=False,
        )
        kategorien.update(
            r.payload.get("kategorie", "allgemein") for r in results if r.payload.get("kategorie")
        )
        if offset is None:
            break
    return kategorien


async def get_completed_count_by_kategorie(user_id: str, kategorie: str) -> int:
    """Anzahl erledigter Tasks in einer Kategorie."""
    result = await _aio(client.count,
        collection_name="tasks",
        count_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value=kategorie)),
        ]),
    )
    return result.count


# ---------------------------------------------------------------------------
# knowledge_base
# ---------------------------------------------------------------------------

async def save_training(user_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("zusammenfassung", "")
    vector = await emb.get_embedding(text)
    indices, values = emb.get_sparse_vector(text)

    payload = {
        "user_id": mandanten_key(user_id),
        "datum": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    await _aio(client.upsert,
        collection_name="training",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            payload=payload,
        )],
    )
    return point_id


async def get_training_entries(user_id: str, limit: int = 10) -> list[dict]:
    results, _ = await _aio(client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


async def get_progress_entries(user_id: str, limit: int = 10) -> list[dict]:
    results, _ = await _aio(client.scroll,
        collection_name="progress",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


# ---------------------------------------------------------------------------
# wuensche
# ---------------------------------------------------------------------------

async def save_wunsch(user_id: str, data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("text", "")
    vector = await emb.get_embedding(text)
    indices, values = emb.get_sparse_vector(text)

    await _aio(client.upsert,
        collection_name="wuensche",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            payload={**data, "user_id": mandanten_key(user_id), "qdrant_point_id": point_id},
        )],
    )
    return point_id


async def update_wunsch(point_id: str, updates: dict) -> None:
    await _aio(client.set_payload,
        collection_name="wuensche",
        payload=updates,
        points=[point_id],
    )


async def get_wunsch(point_id: str) -> Optional[dict]:
    results = await _aio(client.retrieve,
        collection_name="wuensche",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None


async def get_wuensche(user_id: str, status: Optional[str] = None) -> list[dict]:
    must = [qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))]
    if status is not None:
        must.append(qm.FieldCondition(key="status", match=qm.MatchValue(value=status)))
    results, _ = await _aio(client.scroll,
        collection_name="wuensche",
        scroll_filter=qm.Filter(must=must),
        limit=50,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


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

    await _aio(client.upsert,
        collection_name="geheimnisse",
        points=[qm.PointStruct(
            id=point_id,
            vector={"text": vector},
            payload={"user_id": mandanten_key(user_id), **data, "qdrant_point_id": point_id},
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
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))))
    results, _ = await _aio(client.scroll,
        collection_name="geheimnisse",
        scroll_filter=qm.Filter(must=must),
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


# ---------------------------------------------------------------------------
# Aufgaben-Kette
# ---------------------------------------------------------------------------

async def get_kette_wartende(kette_id: str) -> list[dict]:
    """Alle noch wartenden Glieder einer Kette (für Weiter/Abbruch-Entscheidung
    nach einem gescheiterten Glied), sortiert nach Position. kette_id ist eine
    UUID (kollisionssicher) – der Mandanten-Filter ist Defense-in-Depth."""
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="kette_id", match=qm.MatchValue(value=kette_id)),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="kette_wartend")),
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key("sklave"))),
        ]),
        limit=100, with_payload=True, with_vectors=False,
    )
    return sorted([r.payload for r in results], key=lambda x: x.get("kette_position", 0))


async def get_naechster_ketten_task(kette_id: str, aktuelle_position: int) -> Optional[dict]:
    """Gibt den nächsten wartenden Task einer Kette zurück (niedrigste Position > aktuelle)."""
    results, _ = await _aio(client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="kette_id", match=qm.MatchValue(value=kette_id)),
            qm.FieldCondition(
                key="kette_position",
                range=qm.Range(gt=float(aktuelle_position)),
            ),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="kette_wartend")),
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key("sklave"))),
        ]),
        limit=100, with_payload=True, with_vectors=False,
    )
    payloads = sorted(
        [r.payload for r in results],
        key=lambda x: x.get("kette_position", 0),
    )
    return payloads[0] if payloads else None


# ---------------------------------------------------------------------------
# strafen
# ---------------------------------------------------------------------------

async def save_strafe(data: dict) -> str:
    point_id = str(uuid.uuid4())
    text = data.get("bestrafung_text", data.get("aufgabe", ""))
    vector = await emb.get_embedding(text)
    indices, values = emb.get_sparse_vector(text)

    await _aio(client.upsert,
        collection_name="strafen",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
                "sparse": qm.SparseVector(indices=indices, values=values),
            },
            payload={**data, "user_id": mandanten_key(data.get("user_id", "sklave")),
                     "qdrant_point_id": point_id},
        )],
    )
    return point_id


async def get_strafen(user_id: str, limit: int = 20) -> list[dict]:
    results, _ = await _aio(client.scroll,
        collection_name="strafen",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=limit,
        order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


async def get_strafe(point_id: str) -> Optional[dict]:
    results = await _aio(client.retrieve,
        collection_name="strafen",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None


async def update_strafe(point_id: str, updates: dict) -> None:
    await _aio(client.set_payload,
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
        result = await _aio(client.count,
            collection_name="tasks",
            count_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value=status)),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=dreissig_tage)),
            ]),
        )
        return result.count

    erledigt = await _count("erledigt")
    nicht_erledigt = await _count("nicht_erledigt")

    gesamt = erledigt + nicht_erledigt
    if gesamt == 0:
        return {"score": 50, "stufe": "unbekannt", "erledigt": 0, "nicht_erledigt": 0, "quote": 0}

    quote = erledigt / gesamt
    profil = await get_user_profile(user_id) or {}
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


# ---------------------------------------------------------------------------
# coach_regeln – Lern-Speicher für den Coach
# typ:    "regel" (verbindlich) | "notiz" (lockerer Hinweis)
# status: "aktiv" (im Prompt) | "pending" (wartet auf User-Bestaetigung) | "verworfen"
# quelle: "manuell" | "abgeleitet_ablehnung" | "abgeleitet_bewertung" | "abgeleitet_reflexion"
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
        "user_id": mandanten_key(user_id),
        "typ": typ,
        "status": status,
        "quelle": quelle,
        "text": text,
        "kontext": kontext,
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "bestaetigt_am": datetime.now(timezone.utc).isoformat() if status == "aktiv" else None,
        "qdrant_point_id": point_id,
    }
    if profile_user:
        payload["profile_user"] = profile_user
    if profile_patch:
        payload["profile_patch"] = profile_patch
    await _aio(client.upsert,
        collection_name="coach_regeln",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id


async def get_coach_regel(point_id: str) -> Optional[dict]:
    """Holt einen einzelnen Lern-Eintrag (auch pending/verworfen)."""
    try:
        results = await _aio(client.retrieve,
            collection_name="coach_regeln",
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None
    except Exception as e:
        logger.error("Fehler beim Laden der coach_regel %s: %s", point_id, e)
        return None


# Felder, die der Auto-Updater anfassen darf – Hard Limits & kinderfreie Zeiten sind tabu.
_PROFILE_ALLOWED_FIELDS = {
    "domina": {
        "list_add":    {"interessen"},
        "text_replace": {"ziele", "erfahrungsstand"},
    },
    "sklave": {
        "list_add":    {"vorlieben", "wunsch_kategorien", "persoenlichkeit_tags"},
        "text_replace": {"erfahrungsstand"},
    },
}

# Sicherheits-Grenzen-Feld pro Rolle. Diese Felder dürfen NUR additiv (mehr Grenzen =
# strenger = sicher) und NUR über einen explizit bestätigten User-Flow geschrieben
# werden (append_profile_limits / apply_profile_patch-Operation "limit_add").
# Der stille Auto-Updater fasst sie nie an – hard_limits ist zusätzlich in
# _PROFILE_PROTECTED_FIELDS gegen patch_profile_fields gesperrt.
_PROFILE_LIMIT_FIELDS = {
    "sklave": "hard_limits",
    "domina": "grenzen",
}


async def append_profile_limits(user_id: str, feld: str, werte: list[str]) -> list[str]:
    """Fügt Sicherheits-Grenzen ADD-ONLY hinzu – niemals entfernen.

    Bewusst der EINZIGE Pfad, der das geschützte Feld hard_limits schreiben darf, und
    auch nur additiv. Nur aus einem expliziten, vom User bestätigten Flow aufrufen
    (nie aus dem stillen Auto-Updater). Gibt die NEU hinzugefügten Werte zurück."""
    if feld not in _PROFILE_LIMIT_FIELDS.values():
        logger.warning("append_profile_limits: unerlaubtes Feld %s – ignoriert", feld)
        return []
    if isinstance(werte, str):
        werte = [werte]
    werte = [w.strip() for w in (werte or []) if isinstance(w, str) and w.strip()]
    if not werte:
        return []

    results, _ = await _aio(client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=1, with_payload=True, with_vectors=False,
    )
    if results:
        point_id = str(results[0].id)
        payload = results[0].payload or {}
    else:
        point_id = str(uuid.uuid4())
        payload = {"user_id": mandanten_key(user_id)}

    bestand = payload.get(feld) or []
    if not isinstance(bestand, list):
        bestand = []
    neue = [w for w in werte if w not in bestand]
    if not neue:
        return []

    updated = {**payload, "user_id": mandanten_key(user_id), feld: bestand + neue}
    if results:
        # Gezielt patchen + Vektor separat – kein Full-Upsert (Lost Update, s. _reembed_profile_vector).
        await _aio(client.set_payload,
            collection_name="user_profiles",
            payload={feld: bestand + neue},
            points=[point_id],
        )
        await _reembed_profile_vector(point_id, updated)
    else:
        # Noch kein Profil-Punkt vorhanden → Neuanlage per Upsert.
        embed_data = {k: v for k, v in updated.items() if k in _PROFILE_EMBED_FIELDS and v}
        profile_text = " ".join(str(v) for v in embed_data.values()) if embed_data else f"{user_id} profile"
        vector = await emb.get_embedding(profile_text)
        await _aio(client.upsert,
            collection_name="user_profiles",
            points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=updated)],
        )
    logger.info("Grenzen ergänzt (%s.%s): +%s", user_id, feld, ", ".join(neue))
    return neue


def _limit_basis(limit: str) -> str:
    """Limit-Begriff ohne '(Ausnahme: ...)'-Annotation."""
    idx = (limit or "").lower().find("(ausnahme")
    return limit[:idx].strip() if idx > 0 else (limit or "").strip()


async def refine_profile_limit(user_id: str, feld: str, alt: str, neu: str) -> bool:
    """Präzisiert eine BESTEHENDE Sicherheits-Grenze (Ausnahme-Annotation) – niemals
    entfernen oder ersetzen: `neu` muss den alten Basis-Begriff weiterhin enthalten
    (nur Annotation, z.B. 'Öffentlichkeit' → 'Öffentlichkeit (Ausnahme: Plug tragen)').
    Wie append_profile_limits nur aus einem explizit vom User bestätigten Flow
    aufrufen. Gibt True zurück, wenn der Eintrag ersetzt wurde."""
    if feld not in _PROFILE_LIMIT_FIELDS.values():
        logger.warning("refine_profile_limit: unerlaubtes Feld %s – ignoriert", feld)
        return False
    alt = (alt or "").strip()
    neu = (neu or "").strip()
    basis = _limit_basis(alt)
    if not alt or not neu or not basis:
        return False
    # Nur-Annotation-Garantie: der alte Basis-Begriff muss in `neu` erhalten bleiben.
    if basis.lower() not in neu.lower():
        logger.warning("refine_profile_limit: '%s' enthält Basis '%s' nicht – ignoriert", neu, basis)
        return False

    results, _ = await _aio(client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id)))
        ]),
        limit=1, with_payload=True, with_vectors=False,
    )
    if not results:
        return False
    point_id = str(results[0].id)
    payload = results[0].payload or {}
    bestand = payload.get(feld) or []
    if not isinstance(bestand, list):
        return False

    def _gleich(a: str, b: str) -> bool:
        return " ".join(a.lower().split()) == " ".join(b.lower().split())

    idx = next((i for i, b in enumerate(bestand) if isinstance(b, str) and _gleich(b, alt)), None)
    if idx is None:
        logger.info("refine_profile_limit: '%s' nicht im Bestand (%s.%s) – ignoriert", alt, user_id, feld)
        return False
    if any(isinstance(b, str) and _gleich(b, neu) for b in bestand):
        return False  # Ziel-Form steht schon drin

    neu_liste = list(bestand)
    neu_liste[idx] = neu
    # Gezielt patchen + Vektor separat – kein Full-Upsert (Lost Update, s. _reembed_profile_vector).
    await _aio(client.set_payload,
        collection_name="user_profiles",
        payload={feld: neu_liste},
        points=[point_id],
    )
    await _reembed_profile_vector(point_id, {**payload, "user_id": mandanten_key(user_id), feld: neu_liste})
    logger.info("Grenze präzisiert (%s.%s): %s → %s", user_id, feld, alt, neu)
    return True

# Niemals durch Auto-Updater veränderbare Felder. Hinweis: Einstellungs-Felder
# (bot_name, sklave_anrede, setup_kontext, persona_stil, sprache) sind durch die
# _PROFILE_ALLOWED_FIELDS-Allowlist bereits vor dem Auto-Updater geschützt – sie
# gehören NICHT hierher, weil patch_profile_fields (der legitime Setter-Pfad aus
# persona_config/einstellungen) PROTECTED-Felder grundsätzlich verweigert.
_PROFILE_PROTECTED_FIELDS = {"hard_limits", "kinderfreie_zeiten", "safe_word"}
# Felder, die in den Profil-Vektor einfließen (semantisch relevant). Gilt für
# upsert_user_profile UND patch_profile_fields – sonst je nach Pfad inkonsistente Vektoren.
# Einstellungs-Felder (sprache, persona_stil, bot_name, …) bewusst NICHT aufnehmen –
# sie würden den Vektor nur verrauschen.
_PROFILE_EMBED_FIELDS = {"interessen", "vorlieben", "hard_limits", "ziele", "erfahrungsstand",
                         "kategorie_reaktionen", "persoenlichkeit_tags", "wunsch_kategorien",
                         # grenzen = Domina-Pendant zu hard_limits (semantisch relevant) –
                         # ohne den Eintrag wäre das Re-Embed in append/refine_profile_limits
                         # fürs Domina-Profil ein No-Op.
                         "grenzen"}


async def apply_profile_patch(profile_user: str, patch: dict) -> dict:
    """Wendet einen Profil-Patch sicher an. Gibt Bericht zurueck (was wurde geaendert).

    Erlaubte Operationen: "list_add" (Liste erweitern, dedupliziert),
    "text_replace" (Text-Feld ersetzen), "limit_add" (Sicherheits-Grenze ADD-ONLY
    ergänzen – nur für das Grenzen-Feld der Rolle, niemals entfernen), "limit_refine"
    (bestehende Grenze um eine Ausnahme-Annotation präzisieren; der Basis-Begriff
    bleibt zwingend erhalten, wert = {"alt": ..., "neu": ...} oder Liste davon).
    Alles andere wird ignoriert. Hard Limits können ausschließlich additiv wachsen
    oder annotiert werden – nie schrumpfen.
    """
    bericht = {"angewandt": [], "ignoriert": []}
    # Whitelists sind pro ROLLE definiert; profile_user ist der Mandanten-Key
    # ("sklave" bzw. "7:sklave") – Reads/Writes nutzen den vollen Key.
    rolle = paare.rolle_von_user_id(profile_user)
    if rolle not in _PROFILE_ALLOWED_FIELDS:
        bericht["ignoriert"].append(f"unbekannter user '{profile_user}'")
        return bericht

    erlaubt = _PROFILE_ALLOWED_FIELDS[rolle]
    profil = await get_user_profile(profile_user) or {"user_id": profile_user}

    aenderungen = patch.get("changes") if isinstance(patch, dict) else None
    if not isinstance(aenderungen, list):
        bericht["ignoriert"].append("kein 'changes'-Array im Patch")
        return bericht

    # Nur die wirklich geänderten Felder sammeln und gezielt patchen (statt das
    # ganze Profil neu zu upserten) – sonst überschreibt der stale Read parallel
    # geschriebene Felder wie punkte/streak (Lost Update).
    geaendert: dict = {}
    limit_zusatz: dict = {}  # add-only Sicherheits-Grenzen, separat geschrieben
    limit_feld = _PROFILE_LIMIT_FIELDS.get(rolle)
    for ch in aenderungen:
        if not isinstance(ch, dict):
            continue
        feld = ch.get("feld")
        op = ch.get("operation")
        wert = ch.get("wert")

        if op == "limit_add" and feld and feld == limit_feld:
            if isinstance(wert, str):
                wert = [wert]
            if not isinstance(wert, list):
                bericht["ignoriert"].append(f"{feld}: kein Listen-Wert")
                continue
            limit_zusatz.setdefault(feld, []).extend([v for v in wert if v])
        elif op == "limit_refine" and feld and feld == limit_feld:
            alt_neu_liste = wert if isinstance(wert, list) else [wert]
            for alt_neu in alt_neu_liste:
                if not (isinstance(alt_neu, dict) and alt_neu.get("alt") and alt_neu.get("neu")):
                    bericht["ignoriert"].append(f"{feld}: limit_refine braucht alt/neu")
                    continue
                if await refine_profile_limit(profile_user, feld, alt_neu["alt"], alt_neu["neu"]):
                    bericht["angewandt"].append(f"✏️ {feld}: {alt_neu['alt']} → {alt_neu['neu']}")
                else:
                    bericht["ignoriert"].append(f"{feld}: '{alt_neu['alt']}' nicht präzisierbar")
        elif op == "list_add" and feld in erlaubt["list_add"]:
            bestand = profil.get(feld) or []
            if not isinstance(bestand, list):
                bestand = []
            if isinstance(wert, str):
                wert = [wert]
            if not isinstance(wert, list):
                bericht["ignoriert"].append(f"{feld}: kein Listen-Wert")
                continue
            neue = [v for v in wert if v and v not in bestand]
            if neue:
                geaendert[feld] = bestand + neue
                bericht["angewandt"].append(f"+ {feld}: {', '.join(neue)}")
        elif op == "text_replace" and feld in erlaubt["text_replace"]:
            if isinstance(wert, str) and wert.strip():
                geaendert[feld] = wert.strip()
                bericht["angewandt"].append(f"~ {feld}: {wert.strip()[:80]}")
            else:
                bericht["ignoriert"].append(f"{feld}: leerer Text")
        else:
            bericht["ignoriert"].append(f"{feld}/{op} nicht erlaubt")

    if geaendert:
        await patch_profile_fields(profile_user, geaendert)
    for feld, werte in limit_zusatz.items():
        neue = await append_profile_limits(profile_user, feld, werte)
        if neue:
            bericht["angewandt"].append(f"🚫 {feld}: {', '.join(neue)}")
    return bericht


async def get_active_coach_regeln(user_id: str, limit: int = 50) -> list[dict]:
    """Alle aktiven Regeln/Notizen fuer den Coach-Prompt."""
    results, _ = await _aio(client.scroll,
        collection_name="coach_regeln",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="aktiv")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="erstellt_am", direction="asc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results]


async def get_pending_coach_regeln(user_id: str, limit: int = 50) -> list[dict]:
    """Abgeleitete Regeln, die noch auf Bestaetigung warten."""
    results, _ = await _aio(client.scroll,
        collection_name="coach_regeln",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key(user_id))),
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
    await _aio(client.set_payload,
        collection_name="coach_regeln",
        payload=payload,
        points=[point_id],
    )


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
            anzahl = (await _aio(client.count, collection_name=col,
                                 count_filter=mandanten_filter)).count
            if anzahl:
                await _aio(client.delete, collection_name=col,
                           points_selector=qm.FilterSelector(filter=mandanten_filter))
            bericht[col] = anzahl
        except Exception:
            logger.exception("Paar-Daten-Löschung: Collection %s fehlgeschlagen", col)
            bericht[col] = -1
    logger.info("Paar-Daten gelöscht (Paar %s): %s", paar_id, bericht)
    return bericht


# ---------------------------------------------------------------------------
# skills – kuratiertes Wissen pro Kategorie
# ---------------------------------------------------------------------------

def _skills_filter(kategorie: Optional[str] = None) -> qm.Filter:
    """Skills sind PRO PAAR (Mandanten-Key der Dom-Seite): /lerne-Inhalte eines
    Paares dürfen weder in den Prompts anderer Paare landen (Injection-Kanal)
    noch für sie sichtbar sein. Vor dem Scoping war die Collection global –
    Live-Bestand war leer, daher keine Migration nötig."""
    must = [qm.FieldCondition(key="user_id", match=qm.MatchValue(value=mandanten_key("domina")))]
    if kategorie is not None:
        must.append(qm.FieldCondition(key="kategorie", match=qm.MatchValue(value=kategorie)))
    return qm.Filter(must=must)


async def get_skill(kategorie: str) -> Optional[dict]:
    """Holt den Skill-Eintrag des Paares fuer eine Kategorie (oder None)."""
    results, _ = await _aio(client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=True, with_vectors=False,
    )
    return results[0].payload if results else None


async def list_skills() -> list[dict]:
    """Liste aller Skill-Eintraege des Paares."""
    results, _ = await _aio(client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(),
        limit=200, with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    payloads.sort(key=lambda x: x.get("kategorie", ""))
    return payloads


async def update_skill_fields(kategorie: str, fields: dict) -> bool:
    """Gezieltes Feld-Update am Skill-Eintrag (set_payload, kein Voll-Upsert) –
    z.B. um eine fehlende `kurzfassung` nachzutragen. True, wenn der Eintrag existiert."""
    existing, _ = await _aio(client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=False, with_vectors=False,
    )
    if not existing:
        return False
    await _aio(client.set_payload,
        collection_name="skills",
        payload=fields,
        points=[str(existing[0].id)],
    )
    return True


async def save_skill(kategorie: str, inhalt: str, source: str = "grok", kurzfassung: str = "") -> str:
    """Speichert/aktualisiert einen Skill-Eintrag fuer eine Kategorie.

    `kurzfassung` ist die einmalig beim Speichern kondensierte Fassung (v.a.
    Sicherheit & Progression) fuer die Generator-Prompts (skill_kontext_block) –
    so faellt pro Generierung kein Kuerzungs-Call an."""
    existing, _ = await _aio(client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=False, with_vectors=False,
    )
    point_id = str(existing[0].id) if existing else str(uuid.uuid4())
    vector = await emb.get_embedding(f"{kategorie}\n{inhalt[:2000]}")
    payload = {
        "user_id": mandanten_key("domina"),
        "kategorie": kategorie,
        "inhalt": inhalt,
        "source": source,
        "kurzfassung": (kurzfassung or "").strip(),
        "aktualisiert_am": datetime.now(timezone.utc).isoformat(),
    }
    if not existing:
        payload["erstellt_am"] = payload["aktualisiert_am"]
    else:
        # Bestehende erstellt_am erhalten
        try:
            alt = await _aio(client.retrieve, collection_name="skills", ids=[point_id], with_payload=True)
            if alt and alt[0].payload.get("erstellt_am"):
                payload["erstellt_am"] = alt[0].payload["erstellt_am"]
        except Exception:
            payload["erstellt_am"] = payload["aktualisiert_am"]

    await _aio(client.upsert,
        collection_name="skills",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id

