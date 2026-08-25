# BDSM Coach Bot

> AI-powered Telegram bot for couples in a consensual D/s dynamic — a coach for the dominant partner, a persona chat for the submissive, and a deterministic safety layer in between.

![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB)
![Deploy: docker compose](https://img.shields.io/badge/deploy-docker%20compose-2496ED)
![Self-hosted](https://img.shields.io/badge/data-self--hosted-success)

The bot accompanies the **dominant partner** as a coach (voiced like a close friend who shares the hobby) and talks to the **submissive partner** in the persona of their dominant — across two separate Telegram chats. It recognises tasks written in free text, gets them confirmed, delivers them, follows up daily, tracks feelings and progress, and learns preferences over time.

**Why this instead of a ChatGPT tab?** Because a relationship tool needs memory, initiative and guardrails:

- **Your data stays yours** — profiles, conversations, feelings and progress live in your own local Qdrant instance. The only external call is the LLM API for text generation.
- **It takes initiative** — daily follow-ups, mood check-ins, weekly planning and multi-day task series run on a scheduler instead of you remembering to prompt.
- **Safety is code, not vibes** — hard limits of **both** partners are enforced by a deterministic matcher on every generated text, and a safeword pauses the whole system instantly, checked on every message before anything else.
- **Two chats, two voices** — coach voice for the dominant, the dominant's persona for the submissive. Nothing is ever sent to the submissive without the dominant's confirmation.

## What it looks like

*(staged example)*

> **Dominant:** tomorrow morning he polishes my boots, before 9
>
> **Coach:** Noted 📋 — *"Polish the boots"*, delivery tomorrow 7:00, deadline 9:00. Reads like a 2/5 for him. Send it like that? ✅ ✏️ ❌

> **Bot → submissive** *(persona voice)***:** Good morning. Before you even think about coffee: my boots. Spotless by nine — I *will* check.

Afterwards the sub reports back and shares how it felt, points and streaks update, and the dominant gets a report with a 1–5 ★ rating prompt — which feeds the learning system for the next task.

---

## ⚠️ Intended use

- For **consenting adults** in an existing, negotiated dynamic. The bot is a tool that supports a relationship — it does not replace negotiation, consent or aftercare.
- All data (profiles, conversations, feelings) is stored **locally** in your own Qdrant instance. The only external service is the LLM API (xAI Grok by default); message content is sent there for text generation.
- Logs contain intimate content. The built-in log server is therefore **disabled by default** and refuses to start without authentication.

---

## Features

### Tasks & play

- **Free-text task flow** — the dominant writes naturally; the bot detects tasks, asks for confirmation, delivers them, follows up, collects the sub's feeling, awards points/streaks/badges, reports back and lets the dominant rate (1–5 ★) and comment. Task chains (unlock on completion) and multi-day series (2/3/7/14 days) included.

### Learning & coaching

- **Learning system** — category reactions, personality tags, preference detection from chat, dislike thresholds, difficulty auto-adjustment, trust score, level system, exploration of adjacent categories with a 60/30/10 mix of favourites / mid / fresh topics.
- **Coach side** — task inspiration, weekly planning, psycho training, bi-weekly learning-curve analysis, curated knowledge notes (`/lerne`) that feed generators, a proactive "gap filler" (opt-in, double confirmation) when no task was given for a while.

### Safety

- **Safety** — safeword checked case-insensitively on **every** message before any other logic; hard limits of **both** partners validated deterministically against every generated task (with retry); protected profile fields; child-free time windows.

### Customisation

- **Personas** — the dominant voice and the coach voice are configurable style presets (Markdown files, bring your own), with optional bot name, form of address, and a real-world setup context so generated scenes stay anatomically and logistically consistent.
- **Role constellations** — F/M, M/F, F/F, M/M. Labels, pronouns and the anatomy-consistency rules are generated from the configured constellation.
- **Languages** — UI texts, menus and command aliases in German and English, and the LLM reply language free-form — all **per couple**, switchable at runtime.
- **Voice** — optional voice messages: Piper TTS (fully local) or Grok TTS (cloud; expressive multilingual voices per role with speech tags like `[laugh]` and `<whisper>`), plus Whisper STT (voice input incl. safeword check), language-aware per couple. Speaking to the bot gets you a spoken reply; the coach can relay a whispered voice message to the sub on request.
- **Mini App** — optional in-chat web app (`/app`): a stats cockpit for both partners and a voice-message studio for the dominant (tag buttons, TTS preview, one-tap delivery). Runs LAN-only if you want; see [MINIAPP.md](MINIAPP.md).

### Running it

- **Multi-couple** — one deployment can host several couples (`PAIRING_ENABLED`: `/start` → role choice → invite code). Data, persona, schedules, language, safeword and pause state are fully isolated per couple; operator commands (`ADMIN_CHAT_ID`) list couples and delete one including **all** of its stored data; an optional daily message budget caps LLM costs per couple.
- **Ops** — Docker deployment, daily Qdrant snapshots + JSON exports, restore script, state persistence across restarts, LLM fallback endpoint.

## Tech stack

| Component | Technology |
|-----------|------------|
| Bot framework | python-telegram-bot 21 |
| LLM | xAI Grok (configurable, optional fallback endpoint) |
| Vector DB / memory | Qdrant (semantic + recency hybrid retrieval) |
| Embeddings | Ollama — `jina-embeddings-v2-base-de` (768 dim, German-trained; configurable) |
| Scheduler | APScheduler |
| Deployment | Docker / docker-compose |

---

## Quick start

1. **Create a Telegram bot** via [@BotFather](https://t.me/BotFather) and note the token.
2. **Find the two chat IDs** (e.g. via [@userinfobot](https://t.me/userinfobot)) — one per partner.
3. **Get an xAI API key** (or point `GROK_MODEL`/`FALLBACK_LLM_*` at a compatible endpoint).
4. Configure and start:

```bash
cp .env.example .env      # fill in the four required values
mkdir -p qdrant_data qdrant_snapshots backups data

# Embeddings are required. Either use the bundled profile ...
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull hf.co/MAY-A/jina-embeddings-v2-base-de-Q5_K_M-GGUF:Q5_K_M
# ... or point OLLAMA_URL in .env at an existing Ollama instead.
# (The default model is German-trained — for other languages use a
#  multilingual embedding model and adjust EMBEDDING_DIM, see .env.example.)

docker compose up -d --build
# optional fully local voice messages (TTS/STT): add --profile voice
# and set TTS_WYOMING_URL/STT_WYOMING_URL in .env (see .env.example)
```

Both partners then simply write to the bot — a guided setup wizard (language, role constellation, style preset, experience, limits, goals, child-free times) runs on first contact.

### Tests

```bash
make test        # runs the whole suite locally (works without Docker/deps via stubs)
make deploy      # test → build → up (never deploys with red tests)
```

---

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example) for the full annotated list). The four required values are the bot token, both chat IDs and the LLM API key.

At runtime the dominant configures the rest via `/einstellungen`:
reply language, style preset, role constellation, bot name, form of address, and the real-world setup context.

### Custom personas & rule templates

The three built-in style presets live in [`bot/prompts/presets/`](bot/prompts/presets/) as plain Markdown. Drop your own file into `data/persona_presets/` (or set `PERSONA_PRESETS_DIR`) to add a preset — same key overrides a built-in one, missing sections inherit from `standard`:

```markdown
label: Ice-cold & whispering

## stil_kopf
STYLE of the dominant voice:
- ...

## stil_fuss      (optional – forbidden vocabulary / variation rules)
## coach_stil     (optional – the coach voice)
```

The fixed behaviour rules (lead-don't-mirror, word variety, anatomy grounding) are also overridable: put a `templates/regeln_gespraech.md` or `templates/grundierung_zusatz.md` next to your presets. Placeholders like `{sub_nom}` or `{dom_rolle}` are filled from the configured role constellation. A restart applies changes — no rebuild needed.

### Language notes

`BOT_LOCALE` (de/en) switches UI texts and registers English command aliases; the generated replies follow the runtime language setting instead. **Safety caveat:** the deterministic hard-limit matcher ships with a German synonym list — for non-German deployments only literal limit terms are matched, so phrase limits in the language you play in. The default embedding model is German-trained; for other languages set a multilingual model via `OLLAMA_MODEL`/`EMBEDDING_DIM` **before** first use (changing it later requires re-embedding).

### Log server (off by default)

`LOG_PORT=0` disables the HTTP log server (default). If you enable it, it fail-closes without `LOG_USERS` (Basic-Auth), speaks plain HTTP, and the compose file binds it to `127.0.0.1` — access it via SSH tunnel. Message content is written only to the 0600 log file, never to `docker logs`.

---

## Architecture (short version)

```
incoming message
  → chat-ID auth → safeword check → state machine (active flow?)
  → role handler (dominant: coach + task detection / submissive: persona chat)
```

- **Memory:** every conversation is embedded into Qdrant; prompts combine the semantically closest and the most recent entries, deduplicated.
- **Prompt building:** persona blocks (style preset + identity + grounding + language) are assembled per message in `bot/prompts/`; task generators additionally receive learned context (category weights, ratings, dislikes, curated knowledge, dossier).
- **Safety gate:** `limits_check` validates every generated task against both partners' limits with normalisation, word-boundary/stem matching and a retry loop — deterministic, independent of the LLM.
- **Scheduler:** daily follow-up, tiny-task suggestions, mood tracking, weekly planning, bi-weekly analysis, secret reveals, gap-filler checks — all jobs pause during a safeword pause.

Collections: `tasks`, `conversations`, `knowledge_base`, `progress`, `user_profiles`, `training`, `wuensche`, `geheimnisse`, `strafen`, `skills`, `coach_regeln`.

---

## Project structure

```
bot/
├── main.py              # entry point: handlers, commands, scheduler registration
├── config.py            # env vars + validation
├── state.py             # in-memory state machine (persisted to disk)
├── locales/             # UI texts (de = reference, en overlay) + command aliases
├── handlers/            # one module per flow (task, feeling, rating, wishes, …)
├── services/            # qdrant, grok, embeddings, limits_check, labels, …
├── scheduler/           # APScheduler jobs
└── prompts/             # persona/coach/task prompt builders
    └── presets/         # style presets (Markdown) + behaviour rule templates
scripts/                 # restore, migrations
tests/                   # standalone test scripts (run via make test)
```

---

## Backup & restore

A daily job writes native Qdrant snapshots to `./qdrant_snapshots` and JSON exports to `./backups`. Restore:

```bash
python3 scripts/restore_qdrant.py list
python3 scripts/restore_qdrant.py recover-all <YYYY-MM-DD-HH-MM-SS>
```

Keep an off-site copy of `qdrant_snapshots/` — it lives on the same disk otherwise.

---

## Training data export (fine-tuning)

The bot can export your stored conversations as fine-tuning datasets, e.g. to train a local model on the two voices:

```bash
docker exec bdsm-bot python -m bot.tools.export_training
# optional: session gap in minutes / minimum user-message length
docker exec bdsm-bot python -m bot.tools.export_training --gap 45 --min-chars 12
```

This writes two files to `./data/training/` on the host:

- **`coach.jsonl`** — dominant → coach exchanges (learns the coach voice)
- **`herrin.jsonl`** — submissive → dominant exchanges (learns the dominant persona)

Each line is one session in the OpenAI **messages JSONL format** (`{"messages": [{"role": ...}, ...]}`), directly readable by unsloth, axolotl, llama-factory, OpenAI fine-tuning and Ollama tooling. Exchanges less than `--gap` minutes apart are merged into multi-turn sessions so the model learns conversational context. The system prompt per line is the **stable persona block only** — the dynamic runtime prompt with profile/dossier data is deliberately left out, so the model learns the style without overfitting on personal details. Placeholder replies ("ok", "noted.") and duplicate pairs are filtered.

> **⚠️ Privacy:** the persona block is clean, but the `user`/`assistant` turns are your **real chat messages, verbatim** — intimate conversations of **both** partners, possibly including names, places and everyday details. Treat the exported files like the database itself: keep them local, and review and redact them line by line before they leave your machine in any form.

---

## Support the project

This is a spare-time open-source project. If it's useful to you, you can support
development via [GitHub Sponsors](https://github.com/sponsors/Meisterull) —
one-time or monthly, every bit helps. Sponsorship goes directly into development
time and the API costs of testing new features against real LLMs.

## License

[AGPL-3.0](LICENSE). If you run a modified version as a service, you must offer its source to your users.
