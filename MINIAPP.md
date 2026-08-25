# Telegram Mini App (cockpit + voice-message studio)

The bot ships an optional [Telegram Mini App](https://core.telegram.org/bots/webapps):
`/app` (both roles) answers with a button that opens a small web page **inside the
chat** — no separate login, Telegram passes a cryptographically signed identity.

What it shows:

- **Cockpit** (both partners): points, streak, best streak, trust score, completed
  tasks, badge count, open tasks, recent results, latest mood. Badges are shown as a
  *count only* — secret badge goals are never revealed.
- **Voice-message studio** (dominant side only): write a message, insert speech tags
  with one tap (`[pause]`, `[laugh]`, `<whisper>…</whisper>`, …), listen to a TTS
  preview, then deliver it to the submissive as text (tags stripped) + voice bubble
  (tags spoken). Requires Grok TTS (`GROK_TTS=1`, see `.env.example`); with Piper the
  studio still works but tags are ignored.

The server is part of the bot process (`bot/services/miniapp.py`, stdlib only): it
serves the page over HTTPS and validates Telegram's `initData` signature
(HMAC-SHA256 with the bot token) on every API call. The user id inside is resolved
against the configured pair — strangers get a 403 even if they know the URL. Without
`MINIAPP_PORT` or a readable certificate the server simply does not start.

## Requirements

1. **A publicly trusted HTTPS certificate.** This is the hard-won lesson of this
   feature: Telegram's Android WebView does **not** trust user-installed private CAs
   (Chrome does, which makes the failure maddeningly confusing — the page works in
   the browser and stays blank in Telegram). A self-signed or home-CA certificate
   will not work on Android. Use Let's Encrypt.
2. **A hostname your phones can resolve** to the machine running the bot. This works
   fine LAN-only: the DNS name may point at a private IP, no port forwarding needed.
3. The Telegram WebApp bootstrap script, served locally (see below).

## Setup

### 1. Vendor the Telegram script

The page loads `telegram-web-app.js` from the bot itself (an external
`telegram.org` script tag in the head would render the page completely blank
whenever it cannot load). The file is not committed; fetch it once:

```bash
curl -o bot/webapp/telegram-web-app.js https://telegram.org/js/telegram-web-app.js
```

### 2. Get a certificate (LAN-only friendly)

The DNS-01 challenge needs no open ports. Example with a free
[DuckDNS](https://www.duckdns.org) subdomain and [acme.sh](https://acme.sh):

```bash
# point yourname.duckdns.org at your LAN IP (private IPs are fine)
curl "https://www.duckdns.org/update?domains=yourname&token=YOURTOKEN&ip=192.0.2.10"

curl https://get.acme.sh | sh -s email=you@example.com
export DuckDNS_Token=YOURTOKEN
~/.acme.sh/acme.sh --issue --dns dns_duckdns -d yourname.duckdns.org --server letsencrypt
~/.acme.sh/acme.sh --install-cert -d yourname.duckdns.org --ecc \
    --fullchain-file /path/to/repo/ssl-miniapp/fullchain.pem \
    --key-file      /path/to/repo/ssl-miniapp/privkey.pem \
    --reloadcmd     "docker restart bdsm-bot"
```

acme.sh installs a cron job; renewals restart the bot automatically. The
`ssl-miniapp/` directory is git-ignored; the key must be readable by uid 1000 (the
container user).

If your router has DNS rebind protection (FritzBox does), add a local DNS override
for the name (e.g. Pi-hole: `pihole-FTL --config dns.hosts`, then
`pihole reloaddns`) so LAN clients resolve it without asking upstream.

### 3. Configure and expose

`.env`:

```bash
MINIAPP_PORT=8444
MINIAPP_URL=https://yourname.duckdns.org:8444/
```

`docker-compose.override.yml` (LAN-only binding + certificate mount):

```yaml
services:
  bdsm-bot:
    ports:
      - "192.0.2.10:8444:8444"
    volumes:
      - ./ssl-miniapp:/app/ssl-miniapp:ro
```

Then `docker compose up -d`. The startup log should show
`Mini-App-Server läuft auf Port 8444`.

### 4. Open it

Send `/app` to the bot and tap the button. Done.

## Troubleshooting

- **Blank page in Telegram, works in Chrome** → certificate not publicly trusted.
  The bot log shows the proof: `Mini-App: TLS-Handshake abgelehnt: … certificate
  unknown`. (Python's `ThreadingHTTPServer` swallows TLS handshake errors silently;
  the server subclasses it to log them — don't remove that.)
- **Reopening shows the same broken page** → Telegram brings the old WebView back
  instead of reloading. Change the URL (`MINIAPP_URL=…/?v=2`) and send a fresh
  `/app`; old buttons keep their old URL.
- **"Bitte in Telegram über /app öffnen"** → you opened the page in a normal
  browser. There is no Telegram identity there; that message is expected.
- **Every access is logged** (`Mini-App-Zugriff …` with client platform and
  initData length), and the page posts a diagnostic beacon to `/api/log` on load
  (JS errors, platform, initData presence). Grep the bot log for `Mini-App` — the
  whole commissioning story of this feature was solved with exactly those lines.

## Security notes

- Auth is Telegram's signed `initData` (24 h freshness window), checked on every
  API request; the user id must belong to a configured pair, and write actions
  (preview/send) additionally require the dominant role.
- Fail-closed: no port, no cert, or no auth data → no server / 401 / 403.
- Keep the port bound to the LAN interface. The signature check would hold up on
  the open internet, but a private couple's dashboard has no business being there.
