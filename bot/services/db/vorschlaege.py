"""
Stimmung, Inspirationen und Tiny-Task-Vorschläge (progress/tasks-Typen).

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from qdrant_client import models as qm
from bot.services.db import kern

logger = logging.getLogger(__name__)



async def get_latest_stimmung(user_id: str, max_stunden: int | None = None) -> Optional[dict]:
    """Letzte Stimmungsabfrage aus der training Collection.

    max_stunden (D9/N13): die Chat-Pfade rendern den Eintrag als „Aktuelle
    Stimmung" – eine tagealte Antwort (z. B. nach Abwesenheit) ist keine
    aktuelle mehr und lenkte Coach/Herrin auf verjährte Gefühlslagen."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="stimmung")),
        ]),
        limit=1, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    if not payloads:
        return None
    eintrag = payloads[0]
    if max_stunden is not None:
        try:
            d = datetime.fromisoformat(str(eintrag.get("datum", "")).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - d > timedelta(hours=max_stunden):
                return None
        except ValueError:
            return None
    return eintrag


async def get_recent_stimmung_eintraege(limit: int = 5, user_id: str = "sklave") -> list[dict]:
    """Die zuletzt an den Sub gestellten Stimmungs-Fragen als volle Payloads –
    Sperr-Liste (`zusammenfassung`) UND zuletzt benutzte Richtungs-Stichworte
    (`richtung`, seit 27.08.2026; Alt-Bestand hat das Feld nicht)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="training",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="stimmung_frage")),
        ]),
        limit=limit, order_by=qm.OrderBy(key="datum", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    return [r.payload for r in results if r.payload]


async def get_recent_stimmung_fragen(limit: int = 5) -> list[str]:
    """Nur die Fragetexte (handlers/stimmung)."""
    return [p.get("zusammenfassung", "") for p in await get_recent_stimmung_eintraege(limit)]


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
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value="inspiration")),
            # nicht_gewaehlt mitzählen (Review D8/N7): auch die wurden der
            # Domina schon gezeigt (live 8 von 30 Punkten) und fehlten in der
            # „NICHT wiederholen"-Liste. `gemerkt` bleibt bewusst draußen –
            # gemerkte Ideen DÜRFEN wieder auftauchen.
            qm.FieldCondition(key="status", match=qm.MatchAny(
                any=["vorgeschlagen", "abgelehnt", "nicht_gewaehlt"])),
        ]),
        limit=20, order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return [_vorschlag_label(p) for p in payloads[:limit] if p.get("inhalt")]


async def get_pending_tiny_tasks_for_feedback(hours_back: int = 24, user_id: str = "domina") -> list[dict]:
    """Tiny-Tasks die im Zeitraum erstellt wurden und noch Status 'vorgeschlagen' haben."""
    seit = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
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
        results = await kern._aio(kern.client.retrieve,
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
    await kern._aio(kern.client.set_payload,
        collection_name="knowledge_base",
        payload=payload_update,
        points=[point_id],
    )


async def get_recent_rejected_tiny_tasks(limit: int = 5, user_id: str = "domina") -> list[dict]:
    """Letzte abgelehnte Tiny-Tasks mit Begründung (für Lern-Loop)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
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
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
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
