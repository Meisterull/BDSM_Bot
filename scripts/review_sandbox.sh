#!/usr/bin/env bash
# Review-Szenario gegen eine Wegwerf-Kopie der Datenbank fahren.
#
#   scripts/review_sandbox.sh <ausgabe-verzeichnis> [phasen-filter]
#
# Ablauf: frische Qdrant-Snapshots des laufenden Bots → eigene Qdrant-Instanz in
# einem eigenen Docker-Netz (der Lauf kann die echte Datenbank nicht erreichen)
# → tests/review_szenario.py im Bot-Image → Instanz und Netz wieder weg.
# Das Sprachmodell wird nicht aufgerufen (feste Antworten), Telegram ist eine
# Attrappe. Die Ausgabe enthält echte Profil-Daten: nicht einchecken.
#
# Phasen-Filter: kommagetrennte Präfixe der Phasen-Namen, z. B. "05,06,30".
# Anpassbar über Umgebungsvariablen: BOT_CONTAINER, QDRANT_CONTAINER.
set -euo pipefail

AUS="${1:?Ausgabe-Verzeichnis angeben}"
FILTER="${2:-}"
BOT_CONTAINER="${BOT_CONTAINER:-bdsm-bot}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-qdrant}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NETZ="bot-review-net"
QNAME="qdrant-review"

BOT_IMAGE="$(docker inspect "$BOT_CONTAINER" --format '{{.Config.Image}}')"
QDRANT_IMAGE="$(docker inspect "$QDRANT_CONTAINER" --format '{{.Config.Image}}')"
SNAP_DIR="$(docker inspect "$QDRANT_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/qdrant/snapshots"}}{{.Source}}{{end}}{{end}}')"
[ -n "$SNAP_DIR" ] || { echo "Snapshot-Verzeichnis des Qdrant-Containers nicht gefunden" >&2; exit 1; }

mkdir -p "$AUS"; chmod 777 "$AUS"
ENVFILE="$(mktemp)"; chmod 600 "$ENVFILE"
aufraeumen() {
    rm -f "$ENVFILE"
    docker rm -f "$QNAME" >/dev/null 2>&1 || true
    docker network rm "$NETZ" >/dev/null 2>&1 || true
}
trap aufraeumen EXIT

# Umgebung des Bots übernehmen – ohne die Pfade/Adressen, die der Lauf selbst setzt
docker inspect "$BOT_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -v '^$' | grep -v -E '^(QDRANT_URL|STATE_FILE|PATH|HOME|HOSTNAME)=' > "$ENVFILE"

echo "→ Snapshots erzeugen"
SNAPS="$(docker exec "$BOT_CONTAINER" python -W ignore -c \
    "from bot.services import backup; print('\n'.join(backup.create_snapshots()))" 2>/dev/null | grep '\.snapshot$')"

echo "→ Wegwerf-Datenbank starten"
docker network inspect "$NETZ" >/dev/null 2>&1 || docker network create "$NETZ" >/dev/null
docker rm -f "$QNAME" >/dev/null 2>&1 || true
MOUNTS=()
for verz in "$SNAP_DIR"/*/; do
    c="$(basename "$verz")"; [ "$c" = "tmp" ] && continue
    MOUNTS+=(-v "$SNAP_DIR/$c:/qdrant/snapshots/$c:ro")
done
docker run -d --name "$QNAME" --network "$NETZ" --memory 600m "${MOUNTS[@]}" "$QDRANT_IMAGE" >/dev/null
sleep 5

echo "→ Snapshots einspielen"
docker run --rm --network "$NETZ" -e SNAPS="$SNAPS" "$BOT_IMAGE" python -W ignore -c "
import os, re
from qdrant_client import QdrantClient
c = QdrantClient(url='http://$QNAME:6333', timeout=120)
for name in os.environ['SNAPS'].split():
    col = re.sub(r'-\d+-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.snapshot$', '', name)
    c.recover_snapshot(col, location=f'file:///qdrant/snapshots/{col}/{name}')
    print('  ', col, c.count(col).count)
"

# Chat-Verlauf als beschreibbare Kopie
docker cp "$BOT_CONTAINER:/app/data/state.json" "$AUS/state.json" 2>/dev/null || echo '{}' > "$AUS/state.json"
chmod 666 "$AUS/state.json"
rm -rf "$AUS/protokolle" "$AUS/prompts.jsonl"

echo "→ Szenario"
docker run --rm --network "$NETZ" --volumes-from "$BOT_CONTAINER:ro" --env-file "$ENVFILE" \
    -e QDRANT_URL="http://$QNAME:6333" -e REVIEW_QDRANT_HOST="$QNAME" -e STATE_FILE=/aus/state.json \
    -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$REPO/tests:/app/tests:ro" -v "$(cd "$AUS" && pwd):/aus" -w /app --memory 700m \
    "$BOT_IMAGE" python -W ignore -m tests.review_szenario /aus "$FILTER" 2>&1 \
    | grep -E '^\[|^   |FERTIG|FEHLER|ABBRUCH|Traceback' || true

echo "→ fertig: $AUS/prompts.jsonl, $AUS/protokolle/"
