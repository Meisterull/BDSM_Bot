"""
Qdrant-Client, I/O-Executor, Mandanten-Key und Collection-Setup.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from bot import config
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


def ensure_collections() -> None:
    """Erstellt fehlende Collections. Existierende werden nicht verändert.

    Review D8/N4: Sparse-Vektoren komplett gestrichen – sie wurden von allen
    Writern berechnet und geschrieben, aber von keinem einzigen Query-Pfad je
    gelesen (nur `using="text"`-Dense-Queries). Bestehende Collections behalten
    ihre Sparse-Config (harmlos, wird ignoriert); Punkte OHNE Sparse-Vektor sind
    in Qdrant auch bei vorhandener Sparse-Config gültig."""
    existing = {c.name for c in client.get_collections().collections}

    def _create(name: str):
        if name in existing:
            return
        client.create_collection(
            collection_name=name,
            vectors_config={"text": _DENSE},
            on_disk_payload=True,
        )

    _create("conversations")
    _create("knowledge_base")
    _create("progress")
    _create("tasks")
    _create("user_profiles")
    _create("training")
    _create("wuensche")
    _create("geheimnisse")
    _create("strafen")
    _create("coach_regeln")
    _create("skills")
    # Payload Indexes
    _idx = [
        ("conversations",  "user_id",         "keyword",  True),
        ("conversations",  "datum",            "datetime", False),
        ("conversations",  "session_id",       "keyword",  False),
        ("knowledge_base", "user_id",          "keyword",  True),
        ("knowledge_base", "kategorie",        "keyword",  False),
        # level-Index entfernt (Review D8/A2): hatte weder Writer noch Reader,
        # und kein Live-Punkt trug das Feld.
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
        ("tasks",          "serie_id",         "keyword",  False),  # /loeschen s (Serie stoppen)
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
