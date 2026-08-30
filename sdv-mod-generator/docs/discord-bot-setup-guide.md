# Discord Bot Setup Guide — SDV Mod Generator

How to create, configure, and invite this project's Discord bot into a server (guild), with the exact permissions, intents, scopes, and env vars it needs. Grounded in Discord's official developer docs (scraped 2026-08-30) plus a permissions calculator cross-check.

The project has two delivery paths (see `app/discord/`):
- **Gateway bot** (`bot.py`) — discord.py WebSocket connection. Slash commands `/generate`, `/status`, `/cancel`, `/history`, plus free-form chat intake. Runs the pipeline in-process and DMs the zip when done.
- **HTTP webhook** (`webhook.py`) — pure-HTTP interactions endpoint at `POST /webhooks/discord`, Ed25519 signature verification. Fallback when the gateway isn't viable.

This guide covers both.

---

## 1. What you need before starting

- A Discord account.
- A Discord server (guild) you can add bots to. If you don't have one: Discord app → "+" → create server → "For me and my friends". Bots can only be added to servers where you hold the **Manage Server** permission (you have it in a server you own).
- Python 3.11+ (already used by this repo).

---

## 2. Create the application and fetch credentials

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**, name it, **Create**.
2. On the **General Information** page, copy three values (you'll need all of them):
   - **Application ID** → `DISCORD_APP_ID`
   - **Public Key** → `DISCORD_PUBLIC_KEY` (only needed for the webhook path / Ed25519 signature check)
3. Go to the **Bot** page (left sidebar) → **Reset Token** → copy the token → `DISCORD_BOT_TOKEN`.

> Token is highly sensitive. Discord shows it only once — store it in a password manager, never in git. This repo keeps it in `sdv-mod-generator/config/.env` (gitignored).

---

## 3. Enable the privileged intents (REQUIRED for free-form chat)

This bot reads the **content** of ordinary chat messages in `on_message` (`_extract_prompt_from_message`), so it needs the **MESSAGE CONTENT** privileged intent — without it the bot sees message events but the `content` field is empty for anything except DMs, its own messages, and mentions.

1. Developer Portal → your app → **Bot** page → **Privileged Gateway Intents**.
2. Toggle **MESSAGE CONTENT INTENT** to ON.

Discord's official docs: `MESSAGE_CONTENT (1 << 15)` is a privileged intent — it does not gate individual events; it controls whether the `content` / `embeds` / `attachments` fields are populated on message objects. Apps without it receive empty content except for (a) messages the app sent, (b) DMs with the app, (c) messages mentioning the app, (d) messages a message-context command is used on.

The code already requests it (`app/discord/bot.py`):

```python
_intents = discord.Intents.default()
_intents.messages = True
_intents.message_content = True
```

Note the two-layer requirement Discord enforces: enabling the intent in the portal is *not enough by itself*, and requesting it in code is *not enough by itself* — you must do both. If you pass a privileged intent in `IDENTIFY` without enabling it in the portal, the gateway closes the connection with close code `4014` ("Disallowed intents").

Apps under 10,000 users can self-enable privileged intents. Above that threshold Discord requires a review (system DM/email to the owner). This bot is well under it.

**Also recommended on the Bot page:**
- **Public Bot** = ON (so other users can add it if you want).
- **Server Members Intent** and **Presence Intent** = leave OFF (the bot doesn't read member lists or presence).

---

## 4. Scopes and permissions (the invite setup)

### Scopes (OAuth2)

On the **Installation** page (or the **OAuth2 → URL Generator** tab):

- **`bot`** — grants the bot user membership in the guild.
- **`applications.commands`** — lets the bot register and respond to slash commands.

Those two scopes are all this bot needs. (If you request any scope *beyond* `bot` + `applications.commands`, Discord forces a full authorization-code flow with `response_type` and `redirect_uri` — don't, unless you're building an OAuth2 login.)

### Bot permissions (least-privilege)

Select exactly these in the **Bot Permissions** section. Don't grant **Administrator** — server owners distrust bots that ask for it, and this bot needs nothing close to it.

| Permission | Value | Why this bot needs it |
|---|---|---|
| `SEND_MESSAGES` | `1 << 11` (2048) | Reply "Started generation…" to channel |
| `EMBED_LINKS` | `1 << 14` (16384) | `/history` sends a `discord.Embed` |
| `ATTACH_FILES` | `1 << 15` (32768) | DM the finished zip as a `discord.File` |
| `READ_MESSAGE_HISTORY` | `1 << 16` (65536) | Read message content in the channel |
| `USE_APPLICATION_COMMANDS` | `1 << 31` (2147483648) | Members can invoke the slash commands |

**Permissions integer = `2147600384`** (verified: `2048 | 16384 | 32768 | 65536 | 2147483648`).

This is the `permissions` value for the invite URL and the default permissions of the bot's auto-created role on join.

### Install link

Build the OAuth2 invite URL (Discord's format):

```
https://discord.com/oauth2/authorize?client_id=<DISCORD_APP_ID>&scope=bot+applications.commands&permissions=2147600384
```

- Replace `<DISCORD_APP_ID>` with the Application ID.
- `scope=bot+applications.commands` (space or `+` both work).
- `permissions=2147600384` is the computed integer above.

Discord's official docs note the bot-auth URL needs no `response_type` or `redirect_uri` (there's no user access token to return), and you may append `&guild_id=<ID>` to pre-select a guild.

You can also use the Developer Portal **Installation → Install Link → Discord Provided Link** to generate the same URL, or a third-party calculator like [XGamingServer's permissions calculator](https://xgamingserver.com/tools/discord-bot/permissions) to build/verify the integer.

---

## 5. Invite the bot to your server

1. Paste the install link into a browser.
2. In the prompt, choose **Add to Server**, pick your test server, **Authorize** (you must have Manage Server there).
3. The bot appears in the member list with an `APP` tag.

Then install to your **user account** too (optional but useful for testing DMs): paste the link again → **Add to my apps**. The completion notifier DMs the zip to the requesting user, so a DM path must exist.

---

## 6. Map credentials into this project

All values go into `sdv-mod-generator/config/.env` (already gitignored; `.env.example` documents the keys):

```
DISCORD_BOT_TOKEN=<bot token from Bot page>
DISCORD_APP_ID=<application id from General Information>
DISCORD_PUBLIC_KEY=<public key from General Information>   # webhook path only
DISCORD_WEBHOOK_URL=<a server webhook URL>                 # optional push notifications
```

Env-var → code mapping:

| Env var | Consumed by |
|---|---|
| `DISCORD_BOT_TOKEN` | `app/config.py` (`discord_bot_token`), `app/discord/bot.py` (`start_bot`) |
| `DISCORD_APP_ID` | `app/config.py` (`discord_app_id`) |
| `DISCORD_PUBLIC_KEY` | `app/discord/webhook.py` (`verify_signature`, Ed25519) |
| `DISCORD_WEBHOOK_URL` | `app/discord/webhook.py` (`send_completion_webhook`) |
| `ALL_PROXY` / `all_proxy` | `app/discord/bot.py` (`_patch_http_for_proxy`) — SOCKS5 proxy for the gateway + LLM calls |

`app/main.py` starts the gateway bot at app startup whenever `DISCORD_BOT_TOKEN` is set (see `lifespan`), and `/health` reports `discord_bot_ready`.

---

## 7. Slash-command registration (important gotcha)

Slash commands are defined with `@_bot.tree.command(...)` in `bot.py`, but they only appear in the server's command picker **after the command tree is synced to Discord**. Discord's docs describe this as `PUT /applications/{id}/commands` (bulk-overwrite global) or per-guild sync.

Current state of this repo: `bot.py` defines the commands but has **no `tree.sync()` call**. Consequences and the standard fix:

- **Global sync** (`await bot.tree.sync()`) propagates to all guilds but can take **up to an hour** to show up. Use only for a stable, released command set.
- **Guild sync** (`await bot.tree.sync(guild=discord.Object(id=GUILD_ID))`) is instant and is the right choice during development/testing.

Recommended: in `on_ready`, sync to a single test guild during dev; switch to global on release. `discord.py` 2.7.1 (this repo's pinned version) supports both.

---

## 8. The HTTP webhook path (alternative to the gateway)

Used when the gateway isn't viable (serverless, blocked WebSocket egress). Discord sends interaction POSTs to the **Interactions Endpoint URL** you set on the app's **General Information** page.

1. **Public Key** from General Information → `DISCORD_PUBLIC_KEY`.
2. Set the **Interactions Endpoint URL** to your deployed `/webhooks/discord` route (must be HTTPS; use ngrok or a tunnel for local dev: `ngrok http 8000`, then set the URL to `https://<ngrok>.ngrok.io/webhooks/discord`).
3. Discord signs every request (`x-signature-ed25519` header) with Ed25519 over `timestamp + body`. This repo's `webhook.py::verify_signature` already implements the verification with PyNaCl — it validates the hex signature against the public key and rejects mismatches with 401. When `DISCORD_PUBLIC_KEY` is unset the endpoint returns 503.

The webhook path also requires the `applications.commands` scope but **not** `bot` (no gateway membership needed). Slack the webhook path's completion push (`send_completion_webhook`) sends a text message; attaching the zip on the webhook path is a separate enhancement not yet wired.

---

## 9. Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| Slash commands don't appear | Command tree not synced | `await bot.tree.sync(guild=discord.Object(id=GUILD_ID))` in `on_ready` |
| `on_message` fires but `message.content` is empty | MESSAGE CONTENT intent not enabled in portal | Enable **Privileged Gateway Intents → Message Content** |
| Gateway closes with code `4014` | Privileged intent passed in code but not enabled in portal | Enable the intent in the portal, restart |
| Gateway closes with code `4013` | Invalid intents value | Fix the `intents` bitmask |
| `50013 Missing Permissions` on send | Bot's role lacks the permission, or a channel overwrite denies it | Grant the permission in the role, check channel overwrites; decode the role integer |
| `403 Forbidden` on DM | User has DMs from server members disabled, or blocked the bot | Ask user to allow DMs; bot must share a server with the user |
| Token reset / "Improper token" | Token leaked or expired | Reset token in portal, update `.env` |

---

## 10. Sources

- Discord Docs — [Building your first Discord Bot](https://docs.discord.com/developers/quick-start/getting-started) (app creation, credentials, scopes, install link)
- Discord Docs — [Permissions](https://docs.discord.com/developers/topics/permissions) (bitwise flag table, hierarchy, overwrites)
- Discord Docs — [Gateway](https://docs.discord.com/developers/events/gateway) (intents list, privileged intents, `MESSAGE_CONTENT (1<<15)`, close codes 4013/4014)
- Discord Docs — [OAuth2](https://docs.discord.com/developers/topics/oauth2) (bot auth URL format, scopes)
- [XGamingServer Discord Permissions Calculator](https://xgamingserver.com/tools/discord-bot/permissions) (integer cross-check, least-privilege guidance)
- [Python Discord — Message Content Intent](https://www.pythondiscord.com/pages/tags/message-content-intent) (two-layer portal+code requirement)
- [discord.py guide (pyguides.dev)](https://pyguides.dev/guides/discord-bot-basics) (intents + slash command basics)
