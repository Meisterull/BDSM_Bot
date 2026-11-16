"""Erfasste Prompts eines Review-Laufs gruppieren und lesen.

    python3 tests/review_lesen.py <prompts.jsonl>            # Gruppen auflisten
    python3 tests/review_lesen.py <prompts.jsonl> 3,7,12     # Gruppen ausgeben
    python3 tests/review_lesen.py <prompts.jsonl> 3 --voll   # ohne Ausblenden

Gruppiert wird nach dem EIGENEN Text des System-Prompts: Zeilen, die in sehr
vielen Prompts vorkommen (Stil-Blöcke, Inventar, Regeln), gelten als bekannt
und werden beim Ausgeben ausgeblendet – so bleibt pro Gruppe das zu lesen, was
diesen Prompt ausmacht.
"""
import collections
import hashlib
import json
import re
import sys

pfad = sys.argv[1]
P = [json.loads(z) for z in open(pfad, encoding="utf-8")]
zaehl = collections.Counter()
for d in P:
    for zeile in {x.strip() for x in (d["system"] or "").splitlines() if len(x.strip()) > 40}:
        zaehl[zeile] += 1
# „bekannt" = steht in mindestens einem Fünftel aller Prompts
BEKANNT = {z for z, n in zaehl.items() if n >= max(5, len(P) // 5)}


def eigen(d):
    return [x.strip() for x in (d["system"] or "").splitlines() if len(x.strip()) > 25 and x.strip() not in BEKANNT]


G = {}
for d in P:
    e = eigen(d)
    key = hashlib.md5(re.sub(r"\d+", "#", "\n".join(e[:6])).encode()).hexdigest()[:8]
    g = G.setdefault(key, {"n": 0, "phasen": [], "bsp": d, "eigen": e})
    g["n"] += 1
    if d["eintritt"][:2] not in g["phasen"]:
        g["phasen"].append(d["eintritt"][:2])
keys = list(G)
if len(sys.argv) < 3:
    for i, k in enumerate(keys):
        g = G[k]
        print(f"{i:3d} n={g['n']:2d} eigene Zeilen={len(g['eigen']):3d} | Phasen {','.join(g['phasen'])[:22]:22s} | "
              f"{(g['eigen'][0] if g['eigen'] else '(nur bekannte Blöcke)')[:100]!r}")
    print(len(P), "Prompts,", len(G), "Gruppen")
else:
    voll = "--voll" in sys.argv
    for i in [int(x) for x in sys.argv[2].split(",")]:
        g = G[keys[i]]
        d = g["bsp"]
        print(f"\n################ [{i}] n={g['n']} Phasen {','.join(g['phasen'])} kw={d['kw']}")
        for rolle, text in [("SYSTEM", d["system"] or "")] + [(m["role"].upper(), str(m.get("content", ""))) for m in d["messages"][-1:]]:
            print(f"##### {rolle}")
            aus = 0
            for zeile in text.splitlines():
                if not voll and len(zeile.strip()) > 40 and zeile.strip() in BEKANNT:
                    aus += 1
                    continue
                if aus:
                    print(f"   [… {aus} bekannte Zeile(n) …]")
                    aus = 0
                print(zeile)
            if aus:
                print(f"   [… {aus} bekannte Zeile(n) …]")
