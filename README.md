# Codex WhatsApp Gateway

Run Codex behind a dedicated WhatsApp number.

## What it does

- Runs a Baileys-based WhatsApp bridge on the server
- Maps each WhatsApp chat to a persistent Codex thread
- Sends replies back through WhatsApp
- Supports gateway commands:
- `/status`
- `/new`, `/clear`, `/reset`
- `/resume [name]`
- `/title <name>`
- `/root /path`
- `/model <name>`
- `/compact`
- `/help`

## How it works

The bridge receives WhatsApp messages over Baileys and exposes a small local HTTP API.
The Python gateway polls that bridge, maps each chat to a Codex thread, and runs Codex
non-interactively on the server. Replies go back through the same WhatsApp bridge.

## Layout

- `bridge/` - Node WhatsApp bridge
- `server/gateway.py` - long-running Codex gateway
- `systemd/codex-whatsapp.service` - user service unit

## Requirements

- Node.js 18+
- Python 3.11+ or `uv`
- `codex` installed and authenticated on the server
- A WhatsApp account/number to pair with the bridge

## Quick start

```bash
git clone https://github.com/<you>/codex-whatsapp.git
cd codex-whatsapp

cp .env.example .env
# edit .env

uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

cd bridge
npm install
cd ..
```

Pair WhatsApp:

```bash
cd bridge
WHATSAPP_MODE=bot WHATSAPP_ALLOWED_USERS=15551234567 WHATSAPP_PORT=3010 node bridge.js --pair-only --port 3010 --session ~/.codex-whatsapp/whatsapp/session --mode bot
```

Run the gateway:

```bash
CW_ENV_FILE=$PWD/.env .venv/bin/python server/gateway.py
```

If port `3000` is already taken on your box, set `WHATSAPP_PORT` in `.env` to something else like `3010`.

## systemd user service

Copy the service unit to `~/.config/systemd/user/codex-whatsapp.service`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now codex-whatsapp.service
```

The unit expects the project at `~/.codex-whatsapp` by default. Adjust the paths if you install it elsewhere.

## Chat commands

- `/status` shows the active root, model, thread id, summary state, and saved session count.
- `/root /absolute/path` switches the working directory for this chat and clears the active live thread.
- `/model gpt-5.4` switches the model for this chat.
- `/title My Repo Fix` names the current session so `/resume` is usable.
- `/resume` lists saved sessions for the current chat.
- `/resume My Repo Fix` restores that saved session.
- `/new`, `/clear`, `/reset` archive the current session and start clean.
- `/compact` stores a compact carry-forward summary and clears the live thread.

## Notes

- The service expects Codex credentials in `~/.codex/auth.json`.
- The WhatsApp session lives under `~/.codex-whatsapp/whatsapp/session`.
- The default Codex working root is controlled by `CODEX_ROOT`.
- `.env` is intentionally gitignored. Use `.env.example` as the template.
