# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A two-process system that connects a WhatsApp number to a local coding CLI (`codex` or `claude`). Each WhatsApp chat gets its own persistent session state so different chats can target different repos or models independently.

## Architecture

Two processes run together:

**`bridge/bridge.js`** — Node.js process. Connects to WhatsApp via Baileys and exposes a local HTTP API on `127.0.0.1:<WHATSAPP_PORT>`. Handles pairing, media download, message deduplication (echo-loop prevention), and LID↔phone identity mapping for allowlists.

**`server/gateway.py`** — Python async process. Spawns the bridge as a subprocess, polls `/messages`, dispatches each chat message to the CLI backend, and sends replies back via `/send`. Owns all session state (`~/.agent-whatsapp/state.json`): per-chat `thread_id`, `root`, `model`, `summary`, and saved sessions. Chat commands (`/new`, `/root`, `/compact`, etc.) are intercepted here before reaching the CLI.

The gateway starts the bridge itself — you don't need to run both manually.

## Running locally

The gateway is the only entry point:

```bash
# activate the venv first
source ~/.agent-whatsapp/.venv/bin/activate

# then run from the repo root (reads ~/.agent-whatsapp/.env automatically)
python server/gateway.py
```

To run just the bridge standalone (e.g. for debugging):

```bash
cd bridge
node bridge.js --port 3010 --session ~/.agent-whatsapp/whatsapp/session
```

Pair a new WhatsApp account (prints QR, exits on scan):

```bash
bash scripts/pair.sh
# or directly:
node bridge/bridge.js --port 3010 --session ~/.agent-whatsapp/whatsapp/session --pair-only
```

## Install / service setup

```bash
bash scripts/install.sh        # guided installer; writes .env, installs systemd user service
systemctl --user start agent-whatsapp.service
systemctl --user status agent-whatsapp.service --no-pager
journalctl --user -u agent-whatsapp.service -n 100 --no-pager
```

## Dependencies

- Python: `aiohttp` only (`requirements.txt`). Install with `uv pip install -r requirements.txt`.
- Node: `@whiskeysockets/baileys`, `express`, `pino`, `qrcode-terminal`. Install with `npm install` inside `bridge/`.

## Key env vars (`~/.agent-whatsapp/.env`)

| Var | Purpose |
|-----|---------|
| `AGENT_BACKEND` | `codex` or `claude` |
| `AGENT_COMMAND` | Path to the CLI binary |
| `AGENT_ROOT` | Default working directory passed to the CLI |
| `AGENT_MODEL` | Optional default model |
| `WHATSAPP_MODE` | `bot` (dedicated number) or `self-chat` (your own number) |
| `WHATSAPP_ALLOWED_USERS` | Comma-separated phone numbers in E.164 format |
| `WHATSAPP_PORT` | Bridge HTTP port (default `3010`) |
| `CW_LOG_LEVEL` | Python log level (default `INFO`) |
| `WHATSAPP_DEBUG` | Set to `1` on the bridge side for raw message event logging |

## Session state internals

`StateStore` in `gateway.py` persists to `~/.agent-whatsapp/state.json`. Each chat entry stores:
- `thread_id` — CLI session ID for continuing the conversation
- `root` — working directory for this chat
- `model` — per-chat model override
- `summary` — carry-forward compacted summary text
- `saved_sessions` — list of archived snapshots (max 30)

The gateway uses per-chat `asyncio.Lock` to serialize concurrent messages from the same chat.

## Backend invocation details

**Codex** (`run_codex`): calls `codex exec [resume --json | --json --skip-git-repo-check] --dangerously-bypass-approvals-and-sandbox -o <tmpfile>`. Reply is read from the tmpfile; `thread_id` is extracted from `thread.started` JSON events on stdout.

**Claude** (`run_claude`): calls `claude -p --output-format json --permission-mode bypassPermissions --add-dir <root> [--resume | --session-id]`. Reply and `session_id` come from the JSON payload on stdout.

Both backends auto-retry once with a cleared `thread_id` if the backend reports the session was not found.

## Allowlist / identity matching

`bridge/allowlist.js` handles LID↔phone resolution. The bridge writes `lid-mapping-<phone>.json` files into the session directory when it sees a new LID. `matchesAllowedUser` expands a sender ID through those mappings before checking against `WHATSAPP_ALLOWED_USERS`, so operators can use either phone numbers or LIDs in the allowlist.
