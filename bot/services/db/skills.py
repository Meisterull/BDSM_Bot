"""
skills: kuratiertes Wissen je Kategorie.

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
# skills – kuratiertes Wissen pro Kategorie
# ---------------------------------------------------------------------------

def _skills_filter(kategorie: Optional[str] = None) -> qm.Filter:
    """Skills sind PRO PAAR (Mandanten-Key der Dom-Seite): /lerne-Inhalte eines
    Paares dürfen weder in den Prompts anderer Paare landen (Injection-Kanal)
    noch für sie sichtbar sein. Vor dem Scoping war die Collection global –
    Live-Bestand war leer, daher keine Migration nötig."""
    must = [qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key("domina")))]
    if kategorie is not None:
        must.append(qm.FieldCondition(key="kategorie", match=qm.MatchValue(value=kategorie)))
    return qm.Filter(must=must)


async def get_skill(kategorie: str) -> Optional[dict]:
    """Holt den Skill-Eintrag des Paares fuer eine Kategorie (oder None)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=True, with_vectors=False,
    )
    return results[0].payload if results else None


async def list_skills() -> list[dict]:
    """Liste aller Skill-Eintraege des Paares."""
    results, _ = await kern._aio(kern.client.scroll,
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
    existing, _ = await kern._aio(kern.client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=False, with_vectors=False,
    )
    if not existing:
        return False
    await kern._aio(kern.client.set_payload,
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
    existing, _ = await kern._aio(kern.client.scroll,
        collection_name="skills",
        scroll_filter=_skills_filter(kategorie),
        limit=1, with_payload=False, with_vectors=False,
    )
    point_id = str(existing[0].id) if existing else str(uuid.uuid4())
    vector = await emb.get_embedding(f"{kategorie}\n{inhalt[:2000]}")
    payload = {
        "user_id": kern.mandanten_key("domina"),
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
            alt = await kern._aio(kern.client.retrieve, collection_name="skills", ids=[point_id], with_payload=True)
            if alt and alt[0].payload.get("erstellt_am"):
                payload["erstellt_am"] = alt[0].payload["erstellt_am"]
        except Exception:
            payload["erstellt_am"] = payload["aktualisiert_am"]

    await kern._aio(kern.client.upsert,
        collection_name="skills",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=payload)],
    )
    return point_id
