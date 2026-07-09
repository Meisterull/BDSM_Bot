"""
Qdrant-Backup: portabler JSON-Export aller Collections + native Snapshots.

Zwei Ebenen:
  1. JSON-Export (gzip) aller Payloads in BACKUP_DIR – portabel, menschenlesbar,
     überlebt Container-Rebuild (gemountetes Volume). Vektoren werden bewusst NICHT
     exportiert (groß und aus `text` jederzeit neu einbettbar).
  2. Native Qdrant-Snapshots als schneller Recovery-Punkt (liegen im qdrant-Volume).

Alte JSON-Backups werden nach BACKUP_KEEP rotiert.

Standalone (im Container) ausführbar:
    python -m bot.services.backup
"""
import asyncio
import gzip
import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot import config
from bot.services.qdrant import client

logger = logging.getLogger(__name__)

_PREFIX = "qdrant_backup_"
_SUFFIX = ".json.gz"


def _collections() -> list[str]:
    return [c.name for c in client.get_collections().collections]


def export_json(backup_dir: str | None = None) -> tuple[str, int]:
    """Exportiert alle Collections (Payloads + IDs, ohne Vektoren) in eine gzip-JSON.
    Gibt (pfad, anzahl_points) zurück. Atomar via temp-Datei + rename."""
    backup_dir = backup_dir or config.BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y%m%d_%H%M%S")

    dump: dict = {
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        "collections": {},
    }
    gesamt = 0
    for col in _collections():
        punkte = []
        offset = None
        while True:
            res, offset = client.scroll(
                collection_name=col, limit=256, offset=offset,
                with_payload=True, with_vectors=False,
            )
            punkte.extend({"id": str(p.id), "payload": p.payload} for p in res)
            if offset is None:
                break
        dump["collections"][col] = punkte
        gesamt += len(punkte)

    pfad = os.path.join(backup_dir, f"{_PREFIX}{stamp}{_SUFFIX}")
    tmp = pfad + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False)
    os.replace(tmp, pfad)
    # Backups enthalten alle intimen Payloads → nur für den Bot-User lesbar.
    try:
        os.chmod(pfad, 0o600)
    except OSError:
        pass
    return pfad, gesamt


def create_snapshots() -> list[str]:
    """Erzeugt pro Collection einen nativen Qdrant-Snapshot (best effort)."""
    namen = []
    for col in _collections():
        try:
            snap = client.create_snapshot(collection_name=col)
            namen.append(getattr(snap, "name", str(snap)))
        except Exception:
            logger.exception("Snapshot fehlgeschlagen für Collection %s", col)
    return namen


def rotate_snapshots(keep: int | None = None) -> int:
    """Behält pro Collection die `keep` neuesten nativen Snapshots, löscht ältere.
    Verhindert unbegrenztes Anwachsen von ./qdrant_snapshots. Gibt Anzahl gelöschter zurück."""
    keep = keep if keep is not None else config.BACKUP_KEEP
    geloescht = 0
    for col in _collections():
        try:
            snaps = list(client.list_snapshots(collection_name=col))
        except Exception:
            logger.exception("list_snapshots fehlgeschlagen für %s", col)
            continue
        # Neueste zuerst – nach creation_time, Fallback Name
        snaps.sort(key=lambda s: getattr(s, "creation_time", None) or getattr(s, "name", ""), reverse=True)
        for s in snaps[keep:]:
            try:
                client.delete_snapshot(collection_name=col, snapshot_name=s.name)
                geloescht += 1
            except Exception:
                logger.exception("delete_snapshot fehlgeschlagen: %s/%s", col, getattr(s, "name", "?"))
    return geloescht


def rotate(backup_dir: str | None = None, keep: int | None = None) -> list[str]:
    """Behält die `keep` neuesten JSON-Backups, löscht ältere. Gibt gelöschte zurück."""
    backup_dir = backup_dir or config.BACKUP_DIR
    keep = keep if keep is not None else config.BACKUP_KEEP
    if not os.path.isdir(backup_dir):
        return []
    files = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith(_PREFIX) and f.endswith(_SUFFIX)
    )
    zu_loeschen = files[:-keep] if keep > 0 and len(files) > keep else []
    for f in zu_loeschen:
        try:
            os.remove(os.path.join(backup_dir, f))
        except OSError:
            logger.warning("Konnte altes Backup nicht löschen: %s", f)
    return zu_loeschen


def run_backup_sync() -> dict:
    """Kompletter Backup-Lauf (synchron). Qdrant-Client-Calls sind synchron."""
    pfad, n = export_json()
    snaps = create_snapshots() if config.BACKUP_SNAPSHOTS else []
    snaps_rotiert = rotate_snapshots() if config.BACKUP_SNAPSHOTS else 0
    geloescht = rotate()
    bericht = {
        "pfad": pfad, "points": n, "snapshots": len(snaps),
        "snapshots_rotiert": snaps_rotiert, "json_rotiert": len(geloescht),
    }
    logger.info(
        "Qdrant-Backup ok: %s (%d Points), %d Snapshots (%d alte entfernt), %d alte JSON entfernt",
        pfad, n, len(snaps), snaps_rotiert, len(geloescht),
    )
    return bericht


async def run_backup() -> dict:
    """Async-Wrapper für den Scheduler-Job. Läuft über qdrant.run_io (den einen
    IO-Worker) statt einem eigenen to_thread – sonst nutzen Backup-Thread und
    qdrant-Worker denselben, nicht nebenläufig gedachten Client gleichzeitig.
    Der Backup-Job läuft zu einer ruhigen Tageszeit, das kurze Serialisieren ist ok."""
    from bot.services import qdrant
    return await qdrant.run_io(run_backup_sync)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_backup_sync())
