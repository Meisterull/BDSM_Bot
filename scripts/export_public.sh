#!/usr/bin/env bash
# Baut/aktualisiert das veröffentlichbare Repo aus dem aktuellen HEAD – ohne
# Historie des Privat-Repos und ohne projektintime Dateien (TODO.md, deutsches
# readme.md, .claude/).
#
#   scripts/export_public.sh [zielverzeichnis]   # Default: ../bdsm-bot-public
#
# Erster Lauf:  frisches Repo + Squash-Commit "Initial public release".
# Weitere Läufe: bestehendes Repo (samt .git/Remote) bleibt erhalten, der neue
# Stand wird als Sync-Commit obendrauf gesetzt – kein Force-Push nötig.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$(dirname "$SRC")/bdsm-bot-public}"
EXCLUDES=(TODO.md readme.md .claude)

# Privater GitHub-Account des Owners (bewusst NICHT die Arbeits-Identität aus
# der lokalen Repo-Config). Die noreply-Konvention ordnet die Commits dem
# Account zu, ohne eine echte Mail-Adresse zu veröffentlichen.
GIT_NAME="Meisterull"
GIT_EMAIL="Meisterull@users.noreply.github.com"

if [ -n "$(git -C "$SRC" status --porcelain)" ]; then
    echo "⚠️  Arbeitsverzeichnis nicht sauber – exportiert wird der letzte COMMIT (HEAD)." >&2
fi

# --- HEAD in Staging-Verzeichnis entpacken + Interna entfernen --------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$SRC" archive HEAD | tar -x -C "$TMP"
for e in "${EXCLUDES[@]}"; do rm -rf "${TMP:?}/$e"; done

# --- Sanity-Gate 1: bekannte private Muster (LAN-IPs, Namen, Anreden) -------
# Persönliche Begriffe stehen BEWUSST nicht hier im Script (es wird selbst mit
# exportiert!), sondern in scripts/.export_blockliste – untracked, gitignored,
# eine Regex pro Zeile. Ohne die Datei bricht der Export ab, damit das Gate
# nie stillschweigend wegfällt.
BLOCKLISTE="$SRC/scripts/.export_blockliste"
if [ ! -s "$BLOCKLISTE" ]; then
    echo "❌ $BLOCKLISTE fehlt oder ist leer (private Muster, eine Regex pro Zeile) – Abbruch." >&2
    exit 1
fi
# RC explizit auswerten (D9/S7): Treffer (0) UND grep-Fehler (2, z.B. kaputte
# Regex in der Blockliste) brechen ab – nur "kein Treffer" (1) lässt durch.
# Vorher war RC 2 in der if-Bedingung "false" und der Export lief ohne
# wirksames Muster-Gate weiter (set -e greift in Bedingungen nicht).
pruefe_gate() {
    local rc=0
    grep -rniE "$@" "$TMP" || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "❌ Private Spuren im Export gefunden (siehe oben) – Abbruch." >&2
        exit 1
    elif [ "$rc" -ne 1 ]; then
        echo "❌ grep-Fehler (RC $rc) im Muster-Gate – Abbruch (.export_blockliste prüfen)." >&2
        exit 1
    fi
}
pruefe_gate '192\.168\.'
pruefe_gate -f "$BLOCKLISTE"

# --- Sanity-Gate 2: ECHTE Secret-Werte aus der .env dürfen nirgends stehen --
# (Token, API-Keys, Chat-IDs, Log-Zugänge – als Fixed-Strings gegrept)
if [ -f "$SRC/.env" ]; then
    PATTERNS="$TMP.patterns"
    grep -E '^(TELEGRAM_BOT_TOKEN|XAI_API_KEY|DOMINA_CHAT_ID|SKLAVE_CHAT_ID|ADMIN_CHAT_ID|QDRANT_API_KEY|LOG_USERS|LOG_TOKEN|FALLBACK_LLM_KEY)=' "$SRC/.env" \
        | sed -e 's/^[^=]*=//' -e 's/[[:space:]]*#.*$//' -e 's/^"//' -e 's/"$//' \
        | awk 'length($0) > 3' > "$PATTERNS" || true
    if [ -s "$PATTERNS" ] && grep -rF -f "$PATTERNS" "$TMP" -l; then
        rm -f "$PATTERNS"
        echo "❌ Secret-Wert aus der .env im Export gefunden (Dateien oben) – Abbruch." >&2
        exit 1
    fi
    rm -f "$PATTERNS"
fi

# --- Sanity-Gate 3: Struktur ------------------------------------------------
[ ! -e "$TMP/.env" ]        || { echo "❌ .env im Export – Abbruch." >&2; exit 1; }
[ -f "$TMP/LICENSE" ]        || { echo "❌ LICENSE fehlt im Export – Abbruch." >&2; exit 1; }
[ -f "$TMP/README.md" ]      || { echo "❌ README.md fehlt im Export – Abbruch." >&2; exit 1; }
[ -f "$TMP/.env.example" ]   || { echo "❌ .env.example fehlt im Export – Abbruch." >&2; exit 1; }

# --- Ziel-Repo anlegen oder aktualisieren -----------------------------------
if [ -d "$DEST/.git" ]; then
    MODUS="update"
else
    MODUS="init"
    rm -rf "$DEST"
    mkdir -p "$DEST"
    git -C "$DEST" init -q -b main
fi
git -C "$DEST" config user.name  "$GIT_NAME"
git -C "$DEST" config user.email "$GIT_EMAIL"

rsync -a --delete --exclude=.git "$TMP"/ "$DEST"/

git -C "$DEST" add -A
if git -C "$DEST" diff --cached --quiet; then
    echo "✅ Public-Repo unverändert: $DEST (nichts zu committen)"
else
    if [ "$MODUS" = "init" ]; then
        git -C "$DEST" commit -q -m "Initial public release"
    else
        git -C "$DEST" commit -q -m "Sync from private repo ($(date +%F))"
    fi
    echo "✅ Public-Repo aktualisiert ($MODUS): $DEST ($(git -C "$DEST" ls-files | wc -l) Dateien)"
fi
echo "   Push z.B. mit: gh repo create <name> --private --source \"$DEST\" --push"
