"""
Einmal-Migration: Alt-Kategorien (V1 / alte Keyword-Erkennung) auf den aktuellen
config.AUFGABEN_KATEGORIEN-Katalog mappen.

Betrifft:
  - user_profiles.kategorie_reaktionen  (Keys mappen + Counts mergen)
  - user_profiles.persoenlichkeit_tags  (aus gemergten Reaktionen neu berechnen)
  - tasks.kategorie                     (Wert mappen)

Mapping (vom Owner bestätigt 2026-06-06):
  Regeln, gehorsam, service, ritual -> Dienst
  allgemein                          -> in Reaktionen/Tags VERWERFEN (Rausch aus
                                        kaputter Erkennung); an Tasks bleibt es als
                                        gültiger Catch-all-Wert erhalten.
  Anal, Schlucken, ...               -> unverändert (bereits im Katalog)

Stdlib-only (urllib), keine externen Deps. Dry-Run ist Default.
    python3 scripts/migrate_kategorien.py            # zeigt nur, was passieren würde
    python3 scripts/migrate_kategorien.py --apply    # schreibt
Optional: QDRANT_URL als Env (Default http://localhost:6333).
"""
import json
import os
import sys
import urllib.request

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")

# Alt -> Neu. Keys, die hier nicht stehen, bleiben unverändert.
MAPPING = {
    "Regeln": "Dienst",
    "gehorsam": "Dienst",
    "service": "Dienst",
    "ritual": "Dienst",
}
# Nur aus Reaktionen/Tags entfernen (nicht aus Tasks).
DROP_IN_REAKTIONEN = {"allgemein"}

# Buckets, die wir für die Tag-Berechnung als Reaktions-Signale zählen.
_BUCKETS = ("positiv", "neutral", "negativ")


def _req(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{QDRANT_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _scroll_all(collection: str):
    points, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vectors": False}
        if offset is not None:
            body["offset"] = offset
        res = _req("POST", f"/collections/{collection}/points/scroll", body)["result"]
        points.extend(res["points"])
        offset = res.get("next_page_offset")
        if not offset:
            break
    return points


def _set_payload(collection: str, point_id, payload: dict):
    _req("POST", f"/collections/{collection}/points/payload",
         {"payload": payload, "points": [point_id]})


def _merge_counts(ziel: dict, quelle: dict):
    for k, v in quelle.items():
        if isinstance(v, (int, float)):
            ziel[k] = ziel.get(k, 0) + v


def _tags_aus_reaktionen(reaktionen: dict) -> list[str]:
    """Selbe Ratio-Logik wie gefuehl.py: ab 3 Reaktionen, >60% pos -> mag_X,
    >50% neg -> mag_nicht_X."""
    tags = []
    for kat, v in reaktionen.items():
        total = sum(v.get(b, 0) for b in _BUCKETS)
        if total < 3:
            continue
        if v.get("positiv", 0) / total > 0.6:
            tags.append(f"mag_{kat}")
        if v.get("negativ", 0) / total > 0.5:
            tags.append(f"mag_nicht_{kat}")
    return tags


def migrate_profile(p: dict):
    """Gibt (neue_reaktionen, neue_tags) zurück oder None wenn nichts zu tun."""
    pl = p["payload"]
    alt = pl.get("kategorie_reaktionen") or {}
    if not alt:
        return None
    neu: dict = {}
    for kat, counts in alt.items():
        if kat in DROP_IN_REAKTIONEN:
            continue
        ziel = MAPPING.get(kat, kat)
        neu.setdefault(ziel, {})
        _merge_counts(neu[ziel], counts)
    neue_tags = _tags_aus_reaktionen(neu)
    if neu == alt and neue_tags == (pl.get("persoenlichkeit_tags") or []):
        return None
    return neu, neue_tags


def main():
    apply = "--apply" in sys.argv
    # ERLEDIGTE Einmal-Migration (vom Owner am 2026-06-06 angewendet). Schutz gegen
    # versehentliches erneutes --apply, das heutige Daten ein zweites Mal mappen würde.
    if apply and os.getenv("MIGRATION_FORCE") != "1":
        print("ABBRUCH: Diese Migration wurde bereits angewendet. Ein erneutes --apply "
              "würde aktuelle Daten erneut mappen. Falls wirklich gewollt: "
              "MIGRATION_FORCE=1 setzen. (Dry-Run ohne --apply ist weiterhin gefahrlos.)")
        sys.exit(1)
    modus = "APPLY" if apply else "DRY-RUN"
    print(f"== Kategorie-Migration [{modus}] gegen {QDRANT_URL} ==\n")

    # --- user_profiles ---
    print("# user_profiles")
    for p in _scroll_all("user_profiles"):
        uid = p["payload"].get("user_id", "?")
        result = migrate_profile(p)
        if not result:
            print(f"  {uid}: keine Änderung")
            continue
        neu, tags = result
        print(f"  {uid}: kategorie_reaktionen ->")
        for k, v in neu.items():
            print(f"      {k}: {v.get('positiv',0)}+ {v.get('neutral',0)}~ {v.get('negativ',0)}-")
        print(f"      tags -> {tags}")
        if apply:
            _set_payload("user_profiles", p["id"],
                         {"kategorie_reaktionen": neu, "persoenlichkeit_tags": tags})
            print("      ✓ geschrieben")

    # --- tasks ---
    print("\n# tasks")
    geaendert = 0
    for p in _scroll_all("tasks"):
        kat = p["payload"].get("kategorie")
        ziel = MAPPING.get(kat, kat)
        if ziel != kat:
            geaendert += 1
            print(f"  task {p['id']}: {kat} -> {ziel}")
            if apply:
                _set_payload("tasks", p["id"], {"kategorie": ziel})
    print(f"  {geaendert} Task-Kategorien {'geändert' if apply else 'zu ändern'}")

    print(f"\n== Fertig [{modus}] ==")
    if not apply:
        print("Nichts geschrieben. Mit --apply ausführen, um die Änderungen zu übernehmen.")


if __name__ == "__main__":
    main()
