#!/usr/bin/env python3
"""
Reaktions-Sticker-Set beim Bot anlegen und das Tag→file_id-Mapping schreiben.

Aufruf (auf dem Host, braucht nur `requests`):
    python3 scripts/sticker_upload.py <ordner-mit-stickern> [--neu]

Der Ordner enthält pro Reaktion EINE Datei <tag>.png oder <tag>.webp
(statisch, 512px, ≤512 KB) oder <tag>.webm (Video: VP9, ≤3 s, ≤256 KB) –
Tags siehe MANIFEST.
Erstellt das Set "<SET_BASISNAME>_by_<botname>" (Owner = SKLAVE_CHAT_ID aus
der .env, ein Bot-Set braucht einen menschlichen Besitzer) und schreibt
data/reaktions_sticker.json, das der Bot zur Laufzeit liest (mtime-Reload,
kein Neustart nötig).

  --neu       existierendes Set vorher löschen und komplett neu anlegen
  --owner ID  anderen Set-Besitzer erzwingen (Default: SKLAVE_CHAT_ID)

Existiert das Set schon und --neu fehlt, wird nur das Mapping neu geschrieben
(nützlich nach versehentlich gelöschter JSON).
"""
import argparse
import json
import sys
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
}
SET_BASISNAME = "herrin_reaktionen"
SET_TITEL = "Reaktionen der Herrin"


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
    # Netzfehler abfangen (D9/S10): der ungefangene requests-Traceback druckte
    # sonst die Request-URL inklusive Bot-Token ins Terminal.
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{methode}",
                          data=daten or {}, files=dateien or None, timeout=60)
    except requests.RequestException as e:
        raise SystemExit(f"{methode}: Netzfehler ({type(e).__name__})") from None
    antwort = r.json()
    if not antwort.get("ok"):
        raise SystemExit(f"{methode} fehlgeschlagen: {antwort.get('description')}")
    return antwort["result"]


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
    dateien: dict[str, Path] = {}
    for tag in MANIFEST:
        for endung in (".png", ".webp", ".webm"):
            p = ordner / f"{tag}{endung}"
            if p.exists():
                dateien[tag] = p
                break
    unbekannt = [p.name for p in list(ordner.glob("*.web[pm]")) + list(ordner.glob("*.png"))
                 if p.stem not in MANIFEST]
    if unbekannt:
        print(f"⚠ Ignoriert (kein Manifest-Tag): {', '.join(sorted(unbekannt))}")
    if not dateien:
        raise SystemExit(f"Keine passenden Sticker-Dateien in {ordner} gefunden "
                         f"(erwartet: {', '.join(MANIFEST)} als .png/.webp/.webm)")

    for tag, p in dateien.items():
        limit = 256_000 if p.suffix == ".webm" else 512_000
        if p.stat().st_size > limit:
            raise SystemExit(f"{p.name} ist {p.stat().st_size} Bytes – Telegram-Limit {limit}")

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

    if not existiert:
        sticker_json = []
        upload_dateien = {}
        for tag, p in dateien.items():
            sticker_json.append({
                "sticker": f"attach://{tag}",
                "format": "video" if p.suffix == ".webm" else "static",
                "emoji_list": [MANIFEST[tag]],
            })
            upload_dateien[tag] = (p.name, p.read_bytes())
        api(token, "createNewStickerSet", {
            "user_id": owner, "name": set_name, "title": SET_TITEL,
            "stickers": json.dumps(sticker_json),
        }, upload_dateien)
        print(f"Set {set_name} mit {len(dateien)} Stickern angelegt.")
    else:
        print(f"Set {set_name} existiert – schreibe nur das Mapping neu (--neu zum Ersetzen).")

    # file_ids einsammeln: Reihenfolge im Set = Upload-Reihenfolge = dateien-Reihenfolge
    im_set = api(token, "getStickerSet", {"name": set_name})["stickers"]
    tags = list(dateien)
    if len(im_set) != len(tags):
        print(f"⚠ Set hat {len(im_set)} Sticker, erwartet {len(tags)} – Mapping per Reihenfolge, bitte prüfen!")
    mapping = {tag: s["file_id"] for tag, s in zip(tags, im_set)}

    ziel = REPO / "data" / "reaktions_sticker.json"
    ziel.write_text(json.dumps(
        {"set_name": set_name, "sticker": mapping}, indent=2, ensure_ascii=False))
    print(f"Mapping geschrieben: {ziel} ({len(mapping)} Tags: {', '.join(mapping)})")
    print("Der Bot lädt die Datei beim nächsten Sticker-Anlass automatisch (mtime-Check).")


if __name__ == "__main__":
    main()
