"""
Trainingsdaten-Export aus den gespeicherten Unterhaltungen (Qdrant-Collection 'conversations').

Erzeugt ZWEI getrennte Chat-Datensätze im OpenAI-messages-JSONL-Format
(die De-facto-Standardform fürs Fine-Tuning; von Ollama-Modelfile, llama-factory,
axolotl, unsloth & OpenAI-Tuning direkt lesbar):

  coach.jsonl   – Domina  -> Coach   (lernt den Coach-/beste-Freundin-Stil)
  herrin.jsonl  – Sklave  -> Herrin  (lernt die Herrin-Persona)

Jede Zeile ist EINE Session:
  {"messages": [{"role":"system","content": <Persona>},
                {"role":"user","content": ...},
                {"role":"assistant","content": ...}, ...]}

Mehr-Turn: Austausche, die zeitlich nah beieinander liegen (Standard: <= 30 Min
Abstand), werden zu einer Session mit mehreren user/assistant-Paaren zusammengefasst –
das gibt dem Modell Gesprächskontext statt isolierter Einzelantworten.

Als System-Prompt wird bewusst der STABILE Persona-Block verwendet (nicht der volle,
dynamische Laufzeit-Prompt mit Profil-/Dossier-Daten) – so lernt das Modell den Stil,
ohne auf wechselnde Profildaten zu überfitten und ohne unnötige PII pro Zeile.

Aufruf (im Container, ./data ist auf den Host gemountet -> ./data/training/):
  docker exec bdsm-bot python -m bot.tools.export_training
  docker exec bdsm-bot python -m bot.tools.export_training --gap 45 --min-chars 12
"""
import argparse
import json
import os
from datetime import datetime

from qdrant_client import models as qm

from bot.services import qdrant
from bot.prompts import persona, coach_persona

OUT_DIR = "/app/data/training"

# Antworten, die nur Quittungen/Platzhalter sind -> kein Trainingswert
PLACEHOLDER = {
    "", "ok", "okay", "ja", "nein", "hm.", "notiert.",
    "👍", "🙏", "verstanden.", "gut.",
}


def _clean(s) -> str:
    return (s or "").strip()


def _parse_dt(s: str):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _load(user_id: str) -> list[dict]:
    # Vollständig paginieren – ein einzelner scroll(limit=10000) bricht bei mehr
    # Punkten still ab (Datenverlust im Export).
    rows = []
    offset = None
    while True:
        points, offset = qdrant.client.scroll(
            collection_name="conversations",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=user_id))
            ]),
            limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        rows.extend(p.payload for p in points)
        if offset is None:
            break
    rows.sort(key=lambda r: r.get("datum", ""))
    return rows


def build(user_id: str, user_field: str, assistant_field: str, system: str,
          gap_min: int, min_chars: int):
    rows = _load(user_id)

    # 1. Filtern + in Sessions nach Zeitlücke gruppieren
    sessions: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    last = None
    for r in rows:
        u = _clean(r.get(user_field))
        a = _clean(r.get(assistant_field))
        if len(u) < min_chars or not a or a.lower() in PLACEHOLDER:
            continue
        dt = _parse_dt(r.get("datum", ""))
        if last and dt and (dt - last).total_seconds() > gap_min * 60:
            if cur:
                sessions.append(cur)
                cur = []
        cur.append((u, a))
        last = dt or last
    if cur:
        sessions.append(cur)

    # 2. In messages-Format gießen, global doppelte Paare entfernen
    seen = set()
    samples = []
    n_pairs = 0
    for sess in sessions:
        msgs = [{"role": "system", "content": system}]
        kept = 0
        for u, a in sess:
            key = (u, a)
            if key in seen:
                continue
            seen.add(key)
            msgs.append({"role": "user", "content": u})
            msgs.append({"role": "assistant", "content": a})
            kept += 1
        if kept:
            samples.append({"messages": msgs})
            n_pairs += kept
    return samples, len(rows), n_pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="Trainingsdaten aus Unterhaltungen exportieren")
    ap.add_argument("--gap", type=int, default=30,
                    help="Max. Minuten zwischen Austauschen, die zu einer Session zählen (Default 30)")
    ap.add_argument("--min-chars", type=int, default=15,
                    help="Mindestlänge der user-Nachricht (Default 15)")
    ap.add_argument("--out", default=OUT_DIR, help="Ausgabe-Verzeichnis")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    datasets = [
        ("coach",  "domina", "domina_nachricht", "coach_antwort",  coach_persona.fuer_coach_prompt()),
        ("herrin", "sklave", "sklave_nachricht", "herrin_antwort", persona.fuer_sklaven_prompt()),
    ]

    print(f"Export -> {args.out}  (gap={args.gap}min, min_chars={args.min_chars})\n")
    for name, uid, uf, af, system in datasets:
        samples, n_rows, n_pairs = build(uid, uf, af, system, args.gap, args.min_chars)
        path = os.path.join(args.out, f"{name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"  {name:7s}: {n_rows:4d} Roh-Austausche -> {n_pairs:4d} Paare "
              f"in {len(samples):3d} Sessions  ->  {path}")

    print("\nFertig. Auf dem Host liegen die Dateien unter ./data/training/")


if __name__ == "__main__":
    main()
