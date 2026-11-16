"""Prompt-Trockenlauf: rendert die Prompts mit den Live-Daten eines Paares,
OHNE etwas zu senden oder zu schreiben – Grundlage für Prompt-Reviews.

Was das Script absichert:
- grok.chat ist abgefangen: kein LLM-Aufruf, jeder Prompt landet als JSON-Zeile
  in der Ausgabedatei (system, messages, Aufruf-Parameter, auslösender Eintritt)
- alle schreibenden Qdrant-Methoden sind No-ops, der State wird nicht persistiert
- Netz ist auf Qdrant und den Embedding-Dienst beschränkt (kein Telegram, kein TTS)
- der Telegram-Bot ist eine Attrappe
- Zufalls-Gates sind offen (random.random → 0.0); Zeit- und Zustands-Gates
  bleiben, was sie sind – nicht jeder Job erzeugt deshalb einen Prompt

Aufruf in einem Wegwerf-Container mit schreibgeschützten Volumes des laufenden
Bots (Container-Name und Netz ggf. anpassen):

    mkdir -p /tmp/trocken && chmod 777 /tmp/trocken
    docker run --rm --network <bot-netz> --volumes-from <bot-container>:ro \\
        --env-file .env -v /tmp/trocken:/aus -w /app -e PYTHONPATH=/app \\
        -e PYTHONDONTWRITEBYTECODE=1 <bot-image> \\
        python -W ignore /app/bot/tools/prompt_trockenlauf.py /aus/prompts.jsonl [filter]

[filter] = kommagetrennte Präfixe der Eintritte: job:, chat:, cmd: (leer = alles).
Die Ausgabe enthält echte Profil-Daten – nicht einchecken, nach dem Review löschen.
"""
import asyncio
import importlib
import json
import logging
import pkgutil
import random
import sys
import traceback
from unittest.mock import AsyncMock, MagicMock

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

# --- Netz: nur Qdrant + Embedding-Dienst erlaubt -----------------------------
import httpx  # noqa: E402
from urllib.parse import urlparse  # noqa: E402
from bot import config  # noqa: E402

ERLAUBT = {urlparse(u).hostname for u in (
    getattr(config, "QDRANT_URL", ""), getattr(config, "OLLAMA_URL", "")) if u}
print("Erlaubte Hosts:", ERLAUBT, file=sys.stderr)
_orig_async_send = httpx.AsyncClient.send
_orig_sync_send = httpx.Client.send


async def _async_send(self, request, *a, **k):
    if request.url.host not in ERLAUBT:
        raise RuntimeError(f"Trockenlauf: Netz gesperrt ({request.url.host})")
    return await _orig_async_send(self, request, *a, **k)


def _sync_send(self, request, *a, **k):
    if request.url.host not in ERLAUBT:
        raise RuntimeError(f"Trockenlauf: Netz gesperrt ({request.url.host})")
    return _orig_sync_send(self, request, *a, **k)

httpx.AsyncClient.send = _async_send
httpx.Client.send = _sync_send

from bot.services import grok  # noqa: E402
from bot.services.db import kern  # noqa: E402

AUS = open(sys.argv[1], "w", encoding="utf-8") if len(sys.argv) > 1 else sys.stdout
NUR = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else None
AKTUELL = {"name": "?"}
ZAEHLER = {"n": 0}


async def fake_chat(system_prompt, messages, **kw):
    ZAEHLER["n"] += 1
    AUS.write(json.dumps({
        "eintritt": AKTUELL["name"], "nr": ZAEHLER["n"], "system": system_prompt,
        "messages": messages, "kw": {k: v for k, v in kw.items() if v not in (None, False)},
    }, ensure_ascii=False) + "\n")
    AUS.flush()
    text = (system_prompt or "") + " ".join(str(m.get("content", "")) for m in messages)
    if "JSON" in text or "json" in text:
        return '{"ok": true}'
    return "Platzhalter-Antwort des Trockenlaufs. Zweiter Satz, damit Längenprüfungen nicht greifen."

grok.chat = fake_chat

# --- Qdrant: nur lesen -------------------------------------------------------
for _name in ("upsert", "set_payload", "overwrite_payload", "delete", "delete_payload",
              "clear_payload", "update_vectors", "delete_vectors", "batch_update_points",
              "create_collection", "delete_collection", "recreate_collection",
              "create_payload_index", "delete_payload_index", "update_collection",
              "upload_points", "upload_collection"):
    if hasattr(kern.client, _name):
        setattr(kern.client, _name, lambda *a, **k: None)

from bot import state  # noqa: E402
state._persist = lambda *a, **k: None

from bot.services import paare, persona_config, inventar  # noqa: E402

# Zufalls-Gates öffnen (Chance-Würfe gewinnen), Auswahl bleibt zufällig
random.random = lambda: 0.0


def attrappe_bot():
    return AsyncMock()


def fake_update(chat_id: str, text: str):
    u = MagicMock()
    u.effective_chat.id = int(chat_id)
    u.effective_user.id = int(chat_id)
    u.message.text = text
    u.message.voice = None
    u.message.reply_text = AsyncMock()
    u.message.reply_voice = AsyncMock()
    u.effective_message = u.message
    u.callback_query = None
    c = MagicMock()
    c.bot = attrappe_bot()
    c.args = []
    c.user_data = {}
    c.chat_data = {}
    return u, c


async def lauf(name, coro_fn):
    if NUR and name not in NUR and not any(name.startswith(n) for n in NUR):
        return
    AKTUELL["name"] = name
    vorher = ZAEHLER["n"]
    try:
        await asyncio.wait_for(coro_fn(), timeout=120)
        status = "ok"
    except Exception as e:  # noqa: BLE001
        status = f"{type(e).__name__}: {e}"[:200]
        traceback.print_exc(file=sys.stderr)
    print(f"[{name}] prompts={ZAEHLER['n'] - vorher} {status}", file=sys.stderr)


async def main():
    await persona_config.load()
    await inventar.load()
    paar = paare.alle_paare()[0]
    with paare.kontext(paar.paar_id):
        # 1) alle Scheduler-Jobs
        import bot.scheduler as sched_pkg
        jobs = {}
        for m in pkgutil.iter_modules(sched_pkg.__path__):
            mod = importlib.import_module(f"bot.scheduler.{m.name}")
            for n in dir(mod):
                if n.endswith("_job") and asyncio.iscoroutinefunction(getattr(mod, n)):
                    jobs.setdefault(n, getattr(mod, n))
        for n in sorted(jobs):
            if n in ("backup_job", "nachhol_tick_job"):
                continue
            await lauf(f"job:{n}", lambda fn=jobs[n]: fn(attrappe_bot()))

        # 2) Chat-Nachrichten beider Rollen
        from bot.app import routing
        for rolle, cid, text in (
            ("domina", paare.dom_chat_id(), "Was könnte ich heute Abend mit ihm machen? Ich habe eine halbe Stunde."),
            ("sklave", paare.sub_chat_id(), "Guten Abend Herrin, ich bin zuhause und habe meine Aufgabe erledigt."),
        ):
            async def chat(cid=cid, text=text):
                u, c = fake_update(cid, text)
                await routing.handle_message(u, c)
            await lauf(f"chat:{rolle}", chat)

        # 3) Kommandos (Handler direkt)
        from bot.app import registrierung
        from bot import commands_katalog as ck
        erfasst = []

        app = MagicMock()
        app.add_handler = lambda h, group=0: erfasst.append(h)
        try:
            registrierung.register_handlers(app)
        except Exception:
            traceback.print_exc(file=sys.stderr)
        for h in erfasst:
            cmds = getattr(h, "commands", None)
            if not cmds:
                continue
            cmd = sorted(cmds)[0]
            for rolle, cid in (("domina", paare.dom_chat_id()), ("sklave", paare.sub_chat_id())):
                async def kommando(h=h, cid=cid, cmd=cmd):
                    u, c = fake_update(cid, f"/{cmd}")
                    await h.callback(u, c)
                await lauf(f"cmd:{cmd}:{rolle}", kommando)

asyncio.run(main())
AUS.close()
