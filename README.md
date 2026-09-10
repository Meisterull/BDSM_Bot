# BDSM Coach Bot

> AI-powered Telegram bot for couples in a consensual D/s dynamic — a coach for the dominant partner, a persona chat for the submissive, and a deterministic safety layer in between.

🇩🇪 [Deutsche Version](README.de.md)

![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB)
![Deploy: docker compose](https://img.shields.io/badge/deploy-docker%20compose-2496ED)
![Self-hosted](https://img.shields.io/badge/data-self--hosted-success)

The bot accompanies the **dominant partner** as a coach (voiced like a close friend who shares the hobby) and talks to the **submissive partner** in the persona of their dominant — across two separate Telegram chats. It recognises tasks written in free text, gets them confirmed, delivers them, follows up daily, tracks feelings and progress, learns preferences over time — and brings its own games: dice, roulette, bets, storylines, quizzes, secrets and more.

**Why this instead of a ChatGPT tab?** Because a relationship tool needs memory, initiative and guardrails:

- **Your data stays yours** — profiles, conversations, feelings and progress live in your own local Qdrant instance. The only external call is the LLM API for text generation.
- **It takes initiative** — daily follow-ups, mood check-ins, weekly planning, multi-day task series and spontaneous game impulses run on a scheduler instead of you remembering to prompt.
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

### Tasks

- **Free-text task flow** — the dominant writes naturally; the bot detects the task, asks for confirmation with a difficulty estimate and an optional delivery time, delivers it in the persona voice, follows up, collects the sub's feeling, awards points and streaks, reports back and lets the dominant rate (1–5 ★) and comment.
- **Chains, series, templates** — task chains unlock step by step, and when the last step went badly the coach proposes an adapted alternative for the next one; multi-day series get automatic daily follow-ups; templates keep favourite tasks one command away.
- **Endurance orders** — `/dauer <hours> <text>`: an instruction that runs for 1–48 hours with unannounced interim checks, then the usual "did you hold out?" follow-up.
- **Tiny tasks & inspiration** — a daily short-task suggestion the dominant forwards with one tap, three ideas on demand matching the sub's level, and an evening question why a suggestion wasn't used — the answer can become a coach rule.
- **Resurface** — once a week the bot digs up a well-rated task from about three months ago and offers to re-issue it.

### Play

- **Dice & roulette** — `/wuerfel` rolls a surprise task outside the usual rotation, with Telegram's dice animation; `/roulette` spins a slot machine over a punishment: jackpot means mercy, everything below sets the severity. Both reach the dominant as a preview first.
- **Bets & privileges** — the sub can bet points on the next task (double or nothing, with a taunt from the persona) and spend points in a privilege shop: a pause day, easy mode, a wish category, a free task, a secret of the dominant, and more. Every redemption needs the dominant's confirmation.
- **Flash tasks** — opt-in: unannounced mini tasks with a 30-minute countdown, only inside child-free windows, never two at once.
- **Storylines** — `/arc_starten <topic>` turns a theme into a 3–7 day storyline with one task per day; `/event <date> <topic>` plans a storyline whose finale lands exactly on a birthday or anniversary; `/adventskalender` opens one door every morning from 1 to 24 December.
- **Roleplay** — a scenario library with adjustable intensity; the persona stays in character over days, and the bot suggests a matching scenario on Friday and Saturday evenings.
- **Quizzes** — the sub is quizzed on how well they know their dominant (every answer must be provable from stored data; points for correct answers, a counter from the persona for wrong ones); the dominant gets a coach quiz mixing kink knowledge with resolution and questions about the sub.
- **Secrets** — the dominant stores a secret with a reveal date; the bot reveals it to the sub at the right moment.
- **Wishes** — the sub submits wishes for the dominant to decide on and picks up to three favourite categories; when the sub tells the persona that the dominant "should know something", the coach relays it in its own words rather than verbatim.
- **Reaction stickers** — the persona answers with stickers for praise, mockery, commands and more; bring your own set, the upload script is included.
- **Game impulses** — optional: the persona spontaneously starts a quiz or a bet with the sub, and the coach spontaneously drops a quiz question or a ready-made bet idea for the dominant — throttled, inside time windows, never both at once.
- **Points, streaks, badges** — 15 badges plus a handful of secret ones, a trust score, a level system that scales task complexity, weekend multipliers.

### Learning & coaching

- **Learning system** — category reactions, personality tags, preference detection from chat (always a proposal, never applied silently), dislike thresholds, difficulty auto-adjustment, trust score, level system, exploration of adjacent categories with a 60/30/10 mix of favourites / mid / fresh topics, and weekly aging of stale reactions.
- **Coach conversations** — the dominant chats with a coach voiced like a close friend who shares the hobby: task ideas, weekly planning on Sundays, psycho training twice a week (mindset questions and challenges), a review of the last weeks on demand, goal tracking with Monday reminders.
- **Knowledge** — curated knowledge briefs per category (`/lerne`: anatomy, safety, progression, tools, common mistakes) that feed every generator; rules and notes the dominant confirms with one tap; a fortnightly coach self-reflection that proposes new rules from recent conversations.
- **Memory that condenses itself** — weekly dossiers of both partners, a weekly learning log of the coach conversations, open threads the persona picks up on its own, and a fortnightly learning-curve analysis.
- **Gap filler** — opt-in: when no task was given for a while, the bot proposes one and the dominant sends it now, tonight, asks for another or declines.
- **Silence check-in** — after a week without any input from the dominant side the coach asks what is going on (one-tap answers: no time / the suggestions don't fit / running without the bot / something bugs me) and adapts: two weeks of coach quiet, a spectator mode that only reports, or a rule learned from the answer. The answer stays with the coach.

### Safety

- **Safeword** checked case-insensitively on **every** message and voice note before any other logic; it pauses all jobs of the couple instantly, a second word resumes.
- **Hard limits of both partners** validated deterministically against every generated task — normalisation, word-boundary and stem matching, retry loop — independent of the LLM.
- **Guardrails around the edges** — protected profile fields, child-free time windows for anything time-sensitive, absences (`/abwesend`) that flow into every generator, and a punishment log the dominant can review.

### Customisation

- **Personas** — the dominant voice and the coach voice are configurable style presets (Markdown files, bring your own), with optional bot name, form of address, and a real-world setup context so generated scenes stay anatomically and logistically consistent.
- **Role constellations** — F/M, M/F, F/F, M/M. Labels, pronouns and the anatomy-consistency rules are generated from the configured constellation.
- **Languages** — UI texts, menus and command aliases in German and English, and the LLM reply language free-form — all **per couple**, switchable at runtime.
- **Voice** — optional voice messages: Piper TTS (fully local) or Grok TTS (cloud; expressive multilingual voices per role with speech tags like `[laugh]` and `<whisper>`), plus Whisper STT for voice input including the safeword check. Speaking to the bot gets you a spoken reply; the coach can relay a whispered voice message to the sub on request.
- **Mini App** — optional in-chat web app (`/app`): a stats cockpit for both partners and a voice-message studio for the dominant (tag buttons, TTS preview, one-tap delivery). Runs LAN-only if you want; see [MINIAPP.md](MINIAPP.md).

### Running it

- **Multi-couple** — one deployment can host several couples (`PAIRING_ENABLED`: `/start` → role choice → invite code). Data, persona, schedules, language, safeword and pause state are fully isolated per couple; operator commands (`ADMIN_CHAT_ID`) list couples and delete one including **all** of its stored data; an optional daily message budget caps LLM costs per couple.
- **Ops** — Docker deployment, daily Qdrant snapshots + JSON exports, restore script, state persistence across restarts, an OpenAI-compatible fallback endpoint plus an optional local Ollama model that keeps the sub chat alive when the main LLM is down.

## Commands at a glance

<!-- commands:start -->

<details>
<summary><b>Dominant partner</b> – 48 commands</summary>

**📋 Tasks & Templates**

| Command | What it does |
|---|---|
| `/tasks` | Show completed and open tasks |
| `/tasks_all` | All tasks (all categories) |
| `/delete` | Pause or delete an open task |
| `/templates` | Manage task templates |
| `/inspiration` | 3 task ideas matching the level |
| `/tinytask` | Request a tiny task suggestion |
| `/dice` | 🎲 Roll a surprise task |
| `/roulette` | Punishment roulette: slot machine picks mercy or severity |
| `/endure` | Order lasting hours with unannounced check-ins |

**📖 Storylines**

| Command | What it does |
|---|---|
| `/arc` | Show active storyline |
| `/arc_start` | New storyline: /arc_starten <topic> |
| `/arc_end` | End active storyline |
| `/event` | Plan an event: /event <DD.MM.> [days] <topic> – storyline ends on the event day |
| `/event_delete` | Discard a planned event |
| `/adventcalendar` | Plan an advent calendar – one door every morning Dec 1-24 |

**🎭 Roleplay & Weekly Planning**

| Command | What it does |
|---|---|
| `/roleplay` | Start a roleplay scenario |
| `/roleplay_end` | End roleplay |
| `/weekplan` | Create a weekly plan |
| `/training` | Start psycho training |

**📊 Statistics & Reflection**

| Command | What it does |
|---|---|
| `/profile` | Show and edit profile |
| `/goals` | Show goals and progress |
| `/review` | Review of recent weeks |
| `/punishments` | Show punishment log |
| `/secret` | Store a secret for a later moment |

**🧠 Coach & Knowledge**

| Command | What it does |
|---|---|
| `/quiz` | Quiz question about your Mistress – right answers earn points |
| `/learning_log` | 📓 Condense recent coach conversations |
| `/dossier` | 🗒 Profile of the slave (what the bot knows about him) |
| `/botname` | 🏷 Set the bot Mistress's name |
| `/slavename` | 🏷 Set how the slave is addressed |
| `/setup` | 🧩 Set setup/context (roles, anatomy, equipment) |
| `/rule` | ⚡ Set a binding rule for the coach |
| `/note` | 📝 Remember a note/preference |
| `/rules` | 📋 Show learned rules & suggestions |
| `/forget` | 🗑 Deactivate a rule (number from /regeln) |
| `/profile_check` | 🧬 Check profile updates manually |
| `/learn` | 📚 Knowledge brief on a category |
| `/skills` | 📚 Existing knowledge entries |
| `/learn_new` | 📚 Regenerate a knowledge brief |
| `/skill_edit` | ✏️ Overwrite a knowledge entry |

**⚙️ System**

| Command | What it does |
|---|---|
| `/settings` | Set language, personality style, names and setup |
| `/inventory` | Maintain the inventory: which toys/tools are really at home (feeds into tasks) + wish list |
| `/away` | Record an absence: /away 20.07.-02.08. reason – tasks & suggestions take the period into account; /away end clears it |
| `/gap_filler` | Automatically get a task suggestion after a longer task lull (you approve it) |
| `/flash` | Unannounced mini tasks with a countdown for the slave (sent directly) |
| `/skip` | Skip the optional comment |
| `/app` | Mini-App im Chat öffnen: Statistik-Cockpit + Sprachnachrichten-Studio (LAN) |
| `/cancel` | Cancel current action |
| `/help` | This overview |

</details>

<details>
<summary><b>Submissive partner</b> – 15 commands</summary>

**📊 Status & Statistics**

| Command | What it does |
|---|---|
| `/profile` | Show and edit profile |
| `/stats` | Points, streak, badges, privileges |

**💬 Share**

| Command | What it does |
|---|---|
| `/mood` | Share your mood |
| `/wish` | Submit a wish or suggestion |
| `/mywishes` | View/clean up collected wishes |
| `/wish_categories` | Pick favorite categories (max 3) |
| `/inventory` | Maintain the inventory: which toys/tools are really at home (feeds into tasks) + wish list |

**🎁 Rewards**

| Command | What it does |
|---|---|
| `/privilege` | Redeem privileges (costs points) |
| `/bet` | Bet points on your next task (double or nothing) |
| `/quiz` | Quiz question about your Mistress – right answers earn points |

**📋 Tasks**

| Command | What it does |
|---|---|
| `/mytasks` | View & complete open tasks |

**⚙️ System**

| Command | What it does |
|---|---|
| `/away` | Record an absence: /away 20.07.-02.08. reason – tasks & suggestions take the period into account; /away end clears it |
| `/app` | Mini-App im Chat öffnen: Punkte, Streak, Aufgaben als Übersicht (LAN) |
| `/cancel` | Cancel current action |
| `/help` | This overview |

</details>

German command names are the originals; the English aliases shown here are registered in every deployment, and so are the German ones – `/tasks` and `/aufgaben` both work.

<!-- commands:end -->

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
reply language, style preset, role constellation, bot name, form of address, the real-world setup context, the daily schedule, the safeword pair, and coach quiet / spectator mode.

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
- **Scheduler:** daily follow-up, tiny-task suggestions, mood tracking, weekly planning, bi-weekly analyses, secret reveals, storyline days, flash tasks, game and coach impulses, gap-filler checks and the silence check-in — all jobs pause during a safeword pause.

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
scripts/                 # restore, migrations, README command tables
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
