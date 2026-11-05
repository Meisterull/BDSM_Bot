"""
tasks: Anlegen, Lesen, Status, Follow-up-Zeitpunkte.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from qdrant_client import models as qm
from bot import config
from bot.services import embeddings as emb
from bot.services import paare
from bot.services.db import kern, vorschlaege

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

async def save_task(task_data: dict) -> str:
    point_id = str(uuid.uuid4())
    task_text = task_data.get("aufgabe", "")
    vector = await emb.get_embedding(task_text)

    await kern._aio(kern.client.upsert,
        collection_name="tasks",
        points=[qm.PointStruct(
            id=point_id,
            vector={
                "text": vector,
            },
            # user_id kommt aus Caller-Dicts oft als Rollen-Literal → am
            # Persistenz-Eintritt über den Paar-Kontext qualifizieren.
            payload={**task_data, "user_id": kern.mandanten_key(task_data.get("user_id", "sklave")),
                     "qdrant_point_id": point_id},
        )],
    )

    # Auto-Markierung: wenn ein neuer Task explizit aus einem Tiny-Task-Vorschlag
    # erstellt wurde (quelle=tiny_task), gilt der zugehörige Vorschlag als 'übernommen'.
    quelle = task_data.get("quelle", "")
    tiny_task_id = task_data.get("tiny_task_id")  # explizite Verknüpfung
    if quelle == "tiny_task" and tiny_task_id:
        try:
            await vorschlaege.mark_tiny_task_status(tiny_task_id, "uebernommen")
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


def followup_zeitpunkt_utc(tage: int = 1) -> str:
    """Öffentlicher Name (herrin_versaeumnis: Nachfrage in N Tagen neu setzen)."""
    return _followup_zeitpunkt_utc(tage)


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
    # Keine None-Defaults für serie_*/gefuehl/domina_reaktion mehr (Review D8/A1):
    # sie verschmutzten JEDEN Nicht-Serien-Task mit toten Feldern (Live-Muster
    # „serie_* immer None"). Serien setzen ihre Felder über `extra`; alle Leser
    # nutzen ohnehin .get().
    payload = {
        "user_id": kern.mandanten_key(user_id),
        "status": status,
        "level": level,
        "aufgabe": aufgabe,
        "kategorie": kategorie,
        "erteilt_am": datetime.now(timezone.utc).isoformat(),
        "follow_up_datum": _followup_zeitpunkt_utc(followup_in_tagen),
        "erteilt_von": "domina",
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
    await kern._aio(kern.client.delete,
        collection_name="tasks",
        points_selector=qm.PointIdsList(points=[point_id]),
    )


async def get_task(point_id: str) -> Optional[dict]:
    results = await kern._aio(kern.client.retrieve,
        collection_name="tasks",
        ids=[point_id],
        with_payload=True,
    )
    return results[0].payload if results else None


async def update_task(point_id: str, updates: dict) -> None:
    await kern._aio(kern.client.set_payload,
        collection_name="tasks",
        payload=updates,
        points=[point_id],
    )


async def get_tasks_by_status(status_list: list[str], limit: int = 100, sort_by_datum: bool = False,
                              user_id: Optional[str] = "sklave",
                              quelle: Optional[str] = None) -> list[dict]:
    """`user_id` = Mandanten-Key (paare.Paar.user_id). Default = Env-Paar ("sklave",
    verifiziert: alle Bestandsdaten tragen diesen Key). None = bewusst global
    über alle Paare (nur für Betreiber-Tooling wie Backup/Migration).
    `quelle` filtert serverseitig im Index (Review D8/N5, z.B. Blitz-Jobs)."""
    must = [qm.FieldCondition(key="status", match=qm.MatchAny(any=status_list))]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))))
    if quelle is not None:
        must.append(qm.FieldCondition(key="quelle", match=qm.MatchValue(value=quelle)))
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
    results, _ = await kern._aio(kern.client.scroll, **scroll_kwargs)
    return [r.payload for r in results]


async def count_tasks_by_status(status_list: list[str], user_id: Optional[str] = "sklave",
                                quelle: Optional[str] = None) -> int:
    """Billiger Existenz-/Mengen-Check ohne Payload-Transfer (für Vorprüfungen).
    `user_id`/`quelle`-Semantik wie get_tasks_by_status."""
    must = [qm.FieldCondition(key="status", match=qm.MatchAny(any=status_list))]
    if user_id is not None:
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))))
    if quelle is not None:
        must.append(qm.FieldCondition(key="quelle", match=qm.MatchValue(value=quelle)))
    result = await kern._aio(kern.client.count,
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
        must.append(qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))))
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=must),
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]
