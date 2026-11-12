#!/usr/bin/env python3
"""
Reaktions-Sticker-Set beim Bot anlegen und das Tag→file_id-Mapping schreiben.

Aufruf (auf dem Host, braucht nur `requests`):
    python3 scripts/sticker_upload.py <ordner-mit-stickern> [--neu]

Der Ordner enthält .png/.webp-Dateien (statisch, 512px, ≤512 KB) oder
.webm (Video: VP9, ≤3 s, ≤256 KiB). Der Tag wird aus dem Dateinamen
abgeleitet: ComfyUI-Namen wie "anal-sitzend_00003_" werden zu "analsitzend3",
"lob" bleibt "lob". MANIFEST-Tags bekommen ihr festes Emoji, alle anderen
eins über die FAMILIEN-Präfixtabelle (Fallback 🎭).
Erstellt das Set "<SET_BASISNAME>_by_<botname>" (Owner = SKLAVE_CHAT_ID aus
der .env, ein Bot-Set braucht einen menschlichen Besitzer) und schreibt
data/reaktions_sticker.json, das der Bot zur Laufzeit liest (mtime-Reload,
kein Neustart nötig).

  --neu       existierendes Set vorher löschen und komplett neu anlegen
  --owner ID  anderen Set-Besitzer erzwingen (Default: SKLAVE_CHAT_ID)

Existiert das Set schon und --neu fehlt, werden Dateien mit Tags, die noch
nicht im Mapping stehen, ans Set angehängt (addStickerToSet). Fehlt die
JSON komplett, wird nur das Mapping per Set-Reihenfolge rekonstruiert.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent

# Tag → Emoji fürs Set. Die Reihenfolge hier ist die Reihenfolge im Set.
MANIFEST = {
    "lob": "👑",          # Aufgabe erledigt, zufriedene Herrin
    "spott": "😏",        # schwache Ausrede, verlorene Wette, verpasster Blitz
    "streng": "🤨",       # Aufgabe nicht erledigt, Ton daneben
    "strafe": "⛓",        # Strafe angeordnet
    "warten": "⏳",       # Erinnerung, ungeduldiges Warten (noch unverdrahtet)
    "befehl": "☝️",       # neue Aufgabe / Blitzaufgabe
    "auge": "👁",         # Kontroll-Frage, "ich sehe alles"
    "gnade": "✨",        # Privileg gewährt, Roulette-Gnade
    "augenrollen": "🙄",  # Nachverhandeln (noch unverdrahtet)
    "schicksal": "🎲",    # Würfel, Roulette, gewonnene Wette
    # Kategorie-Sticker (im Set, ohne Auto-Trigger – manuell/zukünftig nutzbar)
    "creampiecleanup": "🍨",
    "facesitting": "🪑",
    "straponanal": "🍑",
    "straponblowjob": "🍆",
    # Video-Kategorie-Sticker (08/2026, ohne Auto-Trigger)
    "kniefall": "🧎",
    "vonoben": "👠",
    "gerte": "⚡",
    "blowjobknie": "💋",
    "blowjobhaar": "✊",
    "blowjobblick": "👀",
    "analsitzend": "🍑",
}
SET_BASISNAME = "herrin_reaktionen"
SET_TITEL = "Reaktionen der Herrin"

# Motiv-Präfix → Emoji für Tags, die nicht im MANIFEST stehen (längster Präfix gewinnt).
FAMILIEN = {
    "blowjob": "💋", "dildo": "🍆", "strapon": "🍆", "vaginal": "🔥", "anal": "🍑",
    "sppott": "😏",  # Tippfehler-Variante von spott aus der Generierung
    "prost": "🥂", "applaus": "👏", "lachen": "😂", "schock": "😱",
    "nachdenken": "🤔", "daumenhoch": "👍", "psst": "🤫", "fluestern": "🤫",
    "zeit": "⏰", "abschied": "👋",
    "sitzend": "🪑", "stuhl": "🪑", "liegend": "🛏", "knie": "🧎",
    "allevier": "🐾", "rueckwaerts": "🔙", "vonunten": "👆",
    "fixiert": "⛓", "stiefel": "👢",
}


def tag_und_emoji(stem: str) -> tuple[str, str]:
    """ComfyUI-Namen wie 'anal-sitzend_00003_' → ('analsitzend3', '🍑')."""
    m = re.fullmatch(r"(.*?)[-_ ]*(\d+)?[-_ ]*", stem.lower())
    motiv = re.sub(r"[^a-z0-9]", "", m.group(1))
    nummer = int(m.group(2)) if m.group(2) else None
    tag = motiv if nummer is None else f"{motiv}{nummer}"
    if tag in MANIFEST:
        return tag, MANIFEST[tag]
    emoji = MANIFEST.get(motiv)
    if emoji is None:
        for praefix in sorted(FAMILIEN, key=len, reverse=True):
            if motiv.startswith(praefix):
                emoji = FAMILIEN[praefix]
                break
    return tag, emoji or "🎭"


def env_lesen() -> dict:
    """Minimaler .env-Parser – python-dotenv ist auf dem Host nicht installiert."""
    werte = {}
    env = REPO / ".env"
    if env.exists():
        for zeile in env.read_text().splitlines():
            zeile = zeile.strip()
            if zeile and not zeile.startswith("#") and "=" in zeile:
                k, v = zeile.split("=", 1)
                werte[k.strip()] = v.strip().strip('"').strip("'")
    return werte


def api(token: str, methode: str, daten: dict | None = None, dateien: dict | None = None) -> dict:
    for versuch in range(4):
        # Netzfehler abfangen (D9/S10): der ungefangene requests-Traceback druckte
        # sonst die Request-URL inklusive Bot-Token ins Terminal.
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/{methode}",
                              data=daten or {}, files=dateien or None, timeout=60)
        except requests.RequestException as e:
            raise SystemExit(f"{methode}: Netzfehler ({type(e).__name__})") from None
        antwort = r.json()
        if antwort.get("ok"):
            return antwort["result"]
        warte = (antwort.get("parameters") or {}).get("retry_after")
        if warte and versuch < 3:
            print(f"  … Rate-Limit, warte {warte}s")
            time.sleep(warte + 1)
            continue
        raise SystemExit(f"{methode} fehlgeschlagen: {antwort.get('description')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ordner", help="Ordner mit <tag>.webp/<tag>.webm-Dateien")
    parser.add_argument("--neu", action="store_true", help="existierendes Set löschen und neu anlegen")
    parser.add_argument("--owner", help="Set-Besitzer (User-ID), Default SKLAVE_CHAT_ID")
    args = parser.parse_args()

    env = env_lesen()
    token = env.get("TELEGRAM_BOT_TOKEN")
    owner = args.owner or env.get("SKLAVE_CHAT_ID")
    if not token or not owner:
        raise SystemExit("TELEGRAM_BOT_TOKEN/SKLAVE_CHAT_ID fehlen (.env im Repo-Root?)")

    ordner = Path(args.ordner)
    kandidaten = list(ordner.glob("*.png")) + list(ordner.glob("*.webp")) + list(ordner.glob("*.webm"))

    def sortier(p: Path):
        tag, _ = tag_und_emoji(p.stem)
        manifest_pos = list(MANIFEST).index(tag) if tag in MANIFEST else len(MANIFEST)
        return (manifest_pos, p.name)

    dateien: dict[str, Path] = {}
    emojis: dict[str, str] = {}
    for p in sorted(kandidaten, key=sortier):
        tag, emoji = tag_und_emoji(p.stem)
        while tag in dateien:  # zwei Dateien, gleicher abgeleiteter Tag
            tag += "b"
        dateien[tag] = p
        emojis[tag] = emoji

    if not dateien:
        raise SystemExit(f"Keine Sticker-Dateien (*.png/*.webp/*.webm) in {ordner} gefunden")

    for tag in list(dateien):
        p = dateien[tag]
        limit = 262_144 if p.suffix == ".webm" else 512_000
        if p.stat().st_size > limit:
            print(f"⚠ {p.name}: {p.stat().st_size} B über Telegram-Limit {limit} – übersprungen")
            del dateien[tag]

    bot_name = api(token, "getMe")["username"]
    set_name = f"{SET_BASISNAME}_by_{bot_name}"

    try:
        existiert = requests.post(
            f"https://api.telegram.org/bot{token}/getStickerSet",
            data={"name": set_name}, timeout=60,
        ).json().get("ok", False)
    except requests.RequestException as e:
        raise SystemExit(f"getStickerSet: Netzfehler ({type(e).__name__})") from None

    if existiert and args.neu:
        api(token, "deleteStickerSet", {"name": set_name})
        print(f"Altes Set {set_name} gelöscht.")
        existiert = False

    ziel = REPO / "data" / "reaktions_sticker.json"

    if not existiert:
        sticker_json = []
        upload_dateien = {}
        for tag, p in dateien.items():
            sticker_json.append({
                "sticker": f"attach://{tag}",
                "format": "video" if p.suffix == ".webm" else "static",
                "emoji_list": [emojis[tag]],
            })
            upload_dateien[tag] = (p.name, p.read_bytes())
        api(token, "createNewStickerSet", {
            "user_id": owner, "name": set_name, "title": SET_TITEL,
            "stickers": json.dumps(sticker_json),
        }, upload_dateien)
        print(f"Set {set_name} mit {len(dateien)} Stickern angelegt.")
        im_set = api(token, "getStickerSet", {"name": set_name})["stickers"]
        tags = list(dateien)
        if len(im_set) != len(tags):
            print(f"⚠ Set hat {len(im_set)} Sticker, erwartet {len(tags)} – Mapping per Reihenfolge, bitte prüfen!")
        mapping = {tag: s["file_id"] for tag, s in zip(tags, im_set)}
    else:
        bekannt: dict[str, str] = {}
        if ziel.exists():
            alt = json.loads(ziel.read_text())
            if alt.get("set_name") == set_name:
                bekannt = alt.get("sticker", {})

        neue = {tag: p for tag, p in dateien.items() if tag not in bekannt}
        if bekannt and neue:
            # addStickerToSet hängt hinten an – bekannte file_ids bleiben gültig.
            # Einzelfehler (z.B. Set voll) brechen den Batch nicht ab.
            hinzu: list[str] = []
            for tag, p in neue.items():
                try:
                    api(token, "addStickerToSet", {
                        "user_id": owner, "name": set_name,
                        "sticker": json.dumps({
                            "sticker": f"attach://{tag}",
                            "format": "video" if p.suffix == ".webm" else "static",
                            "emoji_list": [emojis[tag]],
                        }),
                    }, {tag: (p.name, p.read_bytes())})
                except SystemExit as e:
                    print(f"  ✗ {tag} ({p.name}): {e}")
                    continue
                hinzu.append(tag)
                print(f"  + {tag} {emojis[tag]} ({p.name})")
            im_set = api(token, "getStickerSet", {"name": set_name})["stickers"]
            if len(im_set) != len(bekannt) + len(hinzu):
                print(f"⚠ Set hat {len(im_set)} Sticker, erwartet {len(bekannt) + len(hinzu)} – bitte prüfen!")
            mapping = {**bekannt,
                       **{tag: s["file_id"] for tag, s in
                          zip(hinzu, im_set[len(im_set) - len(hinzu):] if hinzu else [])}}
            print(f"{len(hinzu)} von {len(neue)} Stickern ans Set angehängt.")
        elif bekannt:
            print("Set existiert, keine neuen Tags im Ordner – Mapping bleibt unverändert.")
            mapping = bekannt
        else:
            # Reparatur-Fall (JSON weg): Reihenfolge im Set = ursprüngliche Upload-Reihenfolge
            print(f"Set {set_name} existiert – schreibe nur das Mapping neu (--neu zum Ersetzen).")
            im_set = api(token, "getStickerSet", {"name": set_name})["stickers"]
            tags = list(dateien)
            if len(im_set) != len(tags):
                print(f"⚠ Set hat {len(im_set)} Sticker, erwartet {len(tags)} – Mapping per Reihenfolge, bitte prüfen!")
            mapping = {tag: s["file_id"] for tag, s in zip(tags, im_set)}
    ziel.write_text(json.dumps(
        {"set_name": set_name, "sticker": mapping}, indent=2, ensure_ascii=False))
    print(f"Mapping geschrieben: {ziel} ({len(mapping)} Tags: {', '.join(mapping)})")
    print("Der Bot lädt die Datei beim nächsten Sticker-Anlass automatisch (mtime-Check).")


if __name__ == "__main__":
    main()
