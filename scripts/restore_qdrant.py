"""
Qdrant-Restore aus nativen Snapshots (vollständig, inkl. Vektoren).

Snapshots liegen auf dem Host unter ./qdrant_snapshots/<collection>/<name>.snapshot
und im qdrant-Container unter /qdrant/snapshots/<collection>/<name>.snapshot.

Nutzung (Stdlib-only, gegen den laufenden qdrant auf :6333):
    python3 scripts/restore_qdrant.py list
    python3 scripts/restore_qdrant.py recover <collection> <snapshot_name>
    python3 scripts/restore_qdrant.py recover-all <YYYY-MM-DD-HH-MM-SS>   # alle Collections eines Stands

ACHTUNG: 'recover' überschreibt die Collection mit dem Snapshot-Stand.
Optional QDRANT_URL als Env (Default http://localhost:6333).
Seit der Härtung 04.07.2026 PFLICHT: QDRANT_API_KEY als Env (Wert aus der .env).
"""
import json
import os
import sys
import urllib.request

Q = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
# Pfad, unter dem der qdrant-SERVER die Snapshots sieht (Container-intern):
SERVER_SNAP_DIR = "/qdrant/snapshots"


def _req(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("QDRANT_API_KEY", "")
    if api_key:
        headers["api-key"] = api_key
    req = urllib.request.Request(f"{Q}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _collections():
    return [c["name"] for c in _req("GET", "/collections")["result"]["collections"]]


def cmd_list():
    for col in _collections():
        snaps = _req("GET", f"/collections/{col}/snapshots")["result"]
        print(f"\n# {col} ({len(snaps)})")
        for s in sorted(snaps, key=lambda x: x.get("creation_time") or x.get("name", ""), reverse=True):
            print(f"  {s['name']}   ({s.get('size', '?')} bytes)")


def recover(col, name):
    location = f"file://{SERVER_SNAP_DIR}/{col}/{name}"
    print(f"Recover {col} <- {location}")
    res = _req("PUT", f"/collections/{col}/snapshots/recover", {"location": location})
    print("  ->", res.get("result"), res.get("status"))


def cmd_recover_all(stamp):
    for col in _collections():
        snaps = _req("GET", f"/collections/{col}/snapshots")["result"]
        treffer = [s["name"] for s in snaps if stamp in s["name"]]
        if treffer:
            recover(col, treffer[0])
        else:
            print(f"  (kein Snapshot mit '{stamp}' für {col})")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] == "recover" and len(args) == 3:
        recover(args[1], args[2])
    elif args[0] == "recover-all" and len(args) == 2:
        cmd_recover_all(args[1])
    else:
        print(__doc__)
        sys.exit(1)
