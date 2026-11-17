"""Review-Szenario: spielt die Abläufe des Bots gegen eine WEGWERF-KOPIE der
Datenbank durch und schreibt jeden Prompt mit, ohne ein Sprachmodell aufzurufen.
Grundlage für Prompt-Reviews jenseits des Trockenlaufs (bot/tools/
prompt_trockenlauf.py): hier darf geschrieben werden, deshalb laufen auch die
mehrstufigen Abläufe (Aufgabe → Nachfrage → Gefühl → Bewertung, Strafe, Wetten,
Blitz, Rollenspiel …) wie im Betrieb.

Start über scripts/review_sandbox.sh – das Skript legt die Kopie in einem
eigenen Docker-Netz an, startet diesen Lauf und räumt danach wieder auf.

Sicherheiten:
- bricht ab, wenn QDRANT_URL nicht auf die Wegwerf-Instanz zeigt
  (Hostname aus REVIEW_QDRANT_HOST, Standard „qdrant-review")
- Netz nur zur Wegwerf-Datenbank und zum Embedding-Dienst (kein Telegram,
  kein LLM-Anbieter, kein TTS)
- Telegram ist die Attrappe aus tests/harness.py

Das Sprachmodell ist durch feste Antworten ersetzt (JSON-Vorlagen aus dem Prompt
gefüllt, JA/NEIN- und Urteils-Wörter nach Regel, sonst Platzhalter). Geprüft
werden damit die PROMPTS, nicht die Antworten. Über OVERRIDES können Phasen
einzelne Antworten vorgeben, um Wiederholungs-Zweige auszulösen.

Ausgabe: <aus>/prompts.jsonl (ein Prompt je Zeile) und <aus>/protokolle/*.md.
Beides enthält echte Profil-Daten – nicht einchecken, nach dem Review löschen.
Lesen: python3 tests/review_lesen.py <aus>/prompts.jsonl
"""
import asyncio
import json
import os
import random
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from bot import config

QHOST = urlparse(config.QDRANT_URL).hostname
ERWARTET = os.environ.get("REVIEW_QDRANT_HOST", "qdrant-review")
if QHOST != ERWARTET:
    sys.exit(f"ABBRUCH: QDRANT_URL zeigt auf {QHOST!r}, nicht auf die Wegwerf-Kopie {ERWARTET!r}")
ERLAUBT = {QHOST, urlparse(getattr(config, "OLLAMA_URL", "")).hostname}
_as, _ss = httpx.AsyncClient.send, httpx.Client.send


async def _async_send(self, request, *a, **k):
    if request.url.host not in ERLAUBT:
        raise RuntimeError(f"Netz gesperrt ({request.url.host})")
    return await _as(self, request, *a, **k)


def _sync_send(self, request, *a, **k):
    if request.url.host not in ERLAUBT:
        raise RuntimeError(f"Netz gesperrt ({request.url.host})")
    return _ss(self, request, *a, **k)

httpx.AsyncClient.send = _async_send
httpx.Client.send = _sync_send

from bot.services import grok, tts  # noqa: E402

AUS_DIR = sys.argv[1]
NUR = [x for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else []) if x]
AUS = open(os.path.join(AUS_DIR, "prompts.jsonl"), "a", encoding="utf-8")
PHASE = {"name": "start"}
ZAEHLER = {"n": 0}
OVERRIDES: list[tuple[str, str]] = []   # (Regex auf System+User, Antwort) – einmalig


def json_aus_vorlage(text: str) -> str:
    """Füllt die im Prompt genannte JSON-Vorlage mit Platzhaltern."""
    m = re.search(r"JSON[^{\[]{0,80}(\{.*?\})(?=[^}]*$|\s|\.|,)", text, re.S)
    roh = m.group(1) if m else ""
    try:
        d = json.loads(roh)
    except Exception:
        keys = re.findall(r'"([A-Za-z_äöü]+)"\s*:', roh or text[text.find("JSON"):][:600])
        d = {k: None for k in keys} or {"ok": True}
    def fuell(k, v):
        if isinstance(v, bool) or isinstance(v, (int, float)):
            return v
        if isinstance(v, list):
            return [f"Platzhalter {k}"]
        if isinstance(v, dict):
            return {kk: fuell(kk, vv) for kk, vv in v.items()}
        return f"Platzhalter {k} des Szenarios"
    return json.dumps({k: fuell(k, v) for k, v in d.items()}, ensure_ascii=False)


def antwort_fuer(system: str, messages: list) -> str:
    user = str(messages[-1].get("content", "")) if messages else ""
    voll = system + "\n" + user
    # Den Automatik-Vorspann des Coachs (prompts/domina_coach.automatik_hinweis)
    # nicht als ihre Worte lesen: Die Muster unten gelten nur ihrer Nachricht.
    user = re.sub(r"\A\[Zur Einordnung.*?\]\n\n", "", user, flags=re.S)
    for i, (rx, ant) in enumerate(OVERRIDES):
        if re.search(rx, voll, re.S):
            OVERRIDES.pop(i)
            return ant
    if "[AUFGABE: ...] Tag-Regeln" in system:
        m = re.search(r"(?:Aufgabe|aufgabe)[^:\n]*:\s*(.+)", user, re.S)
        if m:
            return f"Mach ich, geht gleich raus.\n\n[AUFGABE: {' '.join(m.group(1).split())[:220]}]"
        m = re.search(r"(?:sag|schick|richte|schreib) ihm[^:\n]*:\s*(.+)", user, re.S | re.I)
        if m:
            return f"Geht raus.\n\n[SPRACHNACHRICHT: {' '.join(m.group(1).split())[:220]}]"
        return "Coach-Antwort des Szenarios, zwei Sätze lang. Mehr gibt es hier nicht zu sagen."
    if re.search(r"\bJSON\b", system):
        return json_aus_vorlage(system)
    if re.search(r"JA oder NEIN|mit JA oder|NUR mit JA", system):
        return "JA"
    if re.search(r"RICHTIG.{0,40}TEILWEISE.{0,40}FALSCH", system, re.S):
        return "TEILWEISE"
    if re.search(r"ABLEHNUNG.{0,60}ANDERES", system, re.S):
        return "ABLEHNUNG"
    if re.search(r"(NUR|AUSSCHLIESSLICH|ausschließlich) mit (dem Wort )?:? ?KEINE|sonst NUR mit KEINE|antworte NUR mit: KEINE", system):
        return "KEINE"
    return "Antwort des Szenarios, erster Satz. Zweiter Satz, damit Längenprüfungen nicht greifen."


async def fake_chat(system_prompt, messages, **kw):
    ZAEHLER["n"] += 1
    antwort = antwort_fuer(system_prompt or "", messages)
    AUS.write(json.dumps({
        "eintritt": PHASE["name"], "nr": ZAEHLER["n"], "system": system_prompt,
        "messages": messages, "kw": {k: v for k, v in kw.items() if v not in (None, False)},
        "antwort": antwort,
    }, ensure_ascii=False) + "\n")
    AUS.flush()
    return antwort

grok.chat = fake_chat


async def _kein_ton(*a, **k):
    return None

tts.synthesize = _kein_ton

import tests.harness as th  # noqa: E402
th.PROTOKOLL_DIR = os.path.join(AUS_DIR, "protokolle")
from tests.harness import Harness  # noqa: E402
from tests.szenario import aufgaben_dialog, zeitraffer_followups  # noqa: E402
from bot import state as st  # noqa: E402
from bot.services import qdrant, inventar  # noqa: E402
from bot.services.db import tasks as db_tasks  # noqa: E402


def knoepfe(h: Harness, rolle: str) -> list[str]:
    gruppen = h._buttons.get(str(h._chat_id(rolle)), [])
    return [d for g in gruppen[-3:] for _, d, _ in g]


async def phase(h: Harness, name: str, fn) -> None:
    if NUR and not any(name.startswith(n) for n in NUR):
        return
    PHASE["name"] = name
    vorher = ZAEHLER["n"]
    h.note(f"### PHASE: {name}")
    try:
        await fn()   # KEIN wait_for: _settle des Testgerüsts wartet sonst auf die eigene Haupt-Task
        status = "ok"
    except Exception as e:  # noqa: BLE001
        status = f"{type(e).__name__}: {str(e)[:160]}"
        traceback.print_exc()
    print(f"[{name}] prompts={ZAEHLER['n'] - vorher} {status} | D={st.get_mode(config.DOMINA_CHAT_ID)} "
          f"S={st.get_mode(config.SKLAVE_CHAT_ID)}", flush=True)


def kn(h, rolle: str) -> str:
    """Kompakte Knopf-Übersicht: die Aktionen der letzten Knopfreihen."""
    gruppen = h._buttons.get(str(h._chat_id(rolle)), [])
    return ",".join(dict.fromkeys(":".join(d.split(":")[:2]) for g in gruppen[-2:] for _, d, _ in g))


async def main() -> None:
    h = Harness(neu=True)
    await h.start()
    await inventar.load()
    with h.bot._unfrozen():
        async def fake_true(*a, **k):
            return True
        for n in ("send_voice", "send_sticker", "send_dice", "send_photo", "send_audio",
                  "send_document", "send_animation", "edit_message_caption", "pin_chat_message"):
            setattr(h.bot, n, fake_true)
    random.seed(7)
    import importlib
    phasen = importlib.import_module("tests.review_phasen")
    await phasen.alle(h, phase, sys.modules[__name__])
    print(f"FERTIG: {ZAEHLER['n']} Prompts, {len(h.fehler_liste)} Handler-Fehler", flush=True)
    for f in h.fehler_liste[:12]:
        print("FEHLER:", f.strip().splitlines()[-1][:200], flush=True)

if __name__ == "__main__":
    asyncio.run(main())
