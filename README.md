# CLI WhatsApp Bridge

Run a coding CLI behind a dedicated WhatsApp number.

## What it does

- Runs a Baileys-based WhatsApp bridge on the server
- Lets you choose a backend during install:
  - `codex`
  - `claude`
- Maps each WhatsApp chat to a persistent backend session
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

## Install

Clone the repo and run the installer:

```bash
git clone https://github.com/kalki-kgp/codex-whatsapp.git
cd codex-whatsapp
bash scripts/install.sh
```

The installer will:

- ask which backend you want to control (`codex` or `claude`)
- install Python and Node dependencies
- write `.env`
- install a user `systemd` service
- offer to pair WhatsApp immediately

By default it installs into `~/.agent-whatsapp` and uses the service name `agent-whatsapp.service`.

## Layout

- `bridge/` - Node WhatsApp bridge
- `server/gateway.py` - long-running backend-neutral gateway
- `scripts/install.sh` - guided installer
- `scripts/pair.sh` - QR pairing helper
- `systemd/agent-whatsapp.service` - generic user service unit

## Requirements

- Node.js 18+
- Python 3.11+ or `uv`
- `codex` or `claude` installed and authenticated on the server
- A WhatsApp account/number to pair with the bridge

## Quick start

```bash
bash scripts/install.sh
```

Pair WhatsApp:

```bash
bash ~/.agent-whatsapp/scripts/pair.sh
```

Run the gateway:

```bash
bash ~/.agent-whatsapp/scripts/start.sh
```

If port `3000` is already taken on your box, set `WHATSAPP_PORT` in `.env` to something else like `3010`.

## systemd user service

The installer copies `systemd/agent-whatsapp.service` to `~/.config/systemd/user/agent-whatsapp.service`, then reloads `systemd`.

```bash
systemctl --user daemon-reload
systemctl --user enable --now agent-whatsapp.service
```

The unit expects the project at `~/.agent-whatsapp` by default.

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

- The service expects the chosen backend to already be authenticated on the machine.
- The WhatsApp session lives under `~/.agent-whatsapp/whatsapp/session` on a default install.
- The default working root is controlled by `AGENT_ROOT`.
- `.env` is intentionally gitignored. Use `.env.example` as the template.
