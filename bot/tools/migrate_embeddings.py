"""
Re-Embedding-Migration: ersetzt die Dense-Vektoren ('text') ALLER Punkte in ALLEN
Collections durch neue Vektoren des aktuell konfigurierten Modells (config.OLLAMA_MODEL).

Nur der Dense-Vektor wird via update_vectors ersetzt – Payload und ggf. Sparse-Vektor
bleiben unangetastet. Die Embed-Quelle pro Collection spiegelt exakt die jeweilige
save_*-Funktion in qdrant.py.

VORHER Qdrant-Snapshot machen! Reihenfolge:
  1) Snapshot
  2) config.OLLAMA_MODEL auf das Zielmodell stellen + Bot neu starten
  3) docker exec bdsm-bot python -m bot.tools.migrate_embeddings           (Dry-Run, zeigt nur an)
  4) docker exec bdsm-bot python -m bot.tools.migrate_embeddings --apply   (führt aus)
"""
import argparse
import asyncio

from qdrant_client import models as qm

from bot import config
from bot.services import qdrant, embeddings

COLLECTIONS = [
    "conversations", "knowledge_base", "progress", "tasks", "user_profiles",
    "training", "wuensche", "geheimnisse", "strafen", "coach_regeln", "skills",
]

_PROFILE_EMBED_FIELDS = ["interessen", "vorlieben", "hard_limits", "ziele", "erfahrungsstand",
                         "kategorie_reaktionen", "persoenlichkeit_tags", "wunsch_kategorien"]


def embed_text(collection: str, p: dict) -> str:
    """Rekonstruiert exakt den Text, den die jeweilige save_*-Funktion embeddet."""
    if collection == "user_profiles":
        parts = [str(p[k]) for k in _PROFILE_EMBED_FIELDS if p.get(k)]
        return " ".join(parts) if parts else f"{p.get('user_id', '')} profile"
    if collection == "tasks":
        return p.get("aufgabe", "") or ""
    if collection == "conversations":
        return p.get("zusammenfassung", "") or ""
    if collection == "knowledge_base":
        return p.get("inhalt", "") or ""
    if collection == "progress":
        return p.get("beschreibung", "") or ""
    if collection == "training":
        return p.get("zusammenfassung", "") or ""
    if collection in ("wuensche", "geheimnisse", "coach_regeln"):
        return p.get("text", "") or ""
    if collection == "strafen":
        return p.get("bestrafung_text") or p.get("aufgabe", "") or ""
    if collection == "skills":
        return f"{p.get('kategorie', '')}\n{(p.get('inhalt', '') or '')[:2000]}"
    return ""


async def migrate_collection(coll: str, apply: bool, batch: int = 64) -> dict:
    client = qdrant.client
    total = skipped = updated = 0
    offset = None
    pending = []

    while True:
        points, offset = client.scroll(
            collection_name=coll, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        if not points:
            break
        for pt in points:
            total += 1
            text = (embed_text(coll, pt.payload or {}) or "").strip()
            if not text:
                skipped += 1
                continue
            if apply:
                vec = await embeddings.get_embedding(text)
                if not vec:
                    skipped += 1
                    continue
                pending.append(qm.PointVectors(id=pt.id, vector={"text": vec}))
                if len(pending) >= batch:
                    client.update_vectors(collection_name=coll, points=pending)
                    updated += len(pending)
                    pending = []
            else:
                updated += 1  # Dry-Run: würde aktualisiert
        if offset is None:
            break

    if apply and pending:
        client.update_vectors(collection_name=coll, points=pending)
        updated += len(pending)

    return {"total": total, "updated": updated, "skipped_empty": skipped}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Tatsächlich schreiben (sonst Dry-Run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN (nichts wird geschrieben)"
    print(f"Re-Embedding mit Modell: {config.OLLAMA_MODEL}")
    print(f"Modus: {mode}\n")
    print(f"{'Collection':16} | {'Punkte':>7} | {'Re-embed':>9} | {'leer/skip':>9}")
    print("-" * 52)
    grand = {"total": 0, "updated": 0, "skipped_empty": 0}
    for coll in COLLECTIONS:
        try:
            r = await migrate_collection(coll, args.apply)
        except Exception as e:
            print(f"{coll:16} | FEHLER: {e}")
            continue
        for k in grand:
            grand[k] += r[k]
        print(f"{coll:16} | {r['total']:7} | {r['updated']:9} | {r['skipped_empty']:9}")
    print("-" * 52)
    print(f"{'GESAMT':16} | {grand['total']:7} | {grand['updated']:9} | {grand['skipped_empty']:9}")
    if not args.apply:
        print("\nDry-Run. Mit --apply ausführen, um die Vektoren wirklich zu ersetzen.")


if __name__ == "__main__":
    asyncio.run(main())
