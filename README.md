# whatsapp-agent

Run a coding CLI behind a dedicated WhatsApp number.

`whatsapp-agent` installs a WhatsApp bridge on a server, connects it to a local CLI agent, and keeps per-chat session state so you can message your server like a real operator instead of SSHing in every time.

## Features

- Supports multiple backends:
  - `codex`
  - `claude`
- Guided installer with menu-based setup for the common choices
- Dedicated WhatsApp bridge using Baileys
- Persistent per-chat agent sessions
- Per-chat root, model, title, saved sessions, and compacted summaries
- `systemd --user` service for long-running deployment
- Isolated WhatsApp setup that does not need to reuse your existing Telegram or other agent integrations

## How it works

The package runs two pieces:

- `bridge/bridge.js`
  Connects to WhatsApp and exposes a small local HTTP bridge.
- `server/gateway.py`
  Polls the bridge for incoming messages, routes each chat into the selected CLI backend, and sends replies back to WhatsApp.

Each WhatsApp chat gets its own persisted session state, so one chat can stay pointed at one repo while another chat works somewhere else.

## Supported backends

- `codex`
- `claude`

The installer asks which backend you want to control. The selected CLI must already be installed and authenticated on the server.

## Requirements

- Linux server with `systemd --user`
- Node.js 18+
- Python 3.11+ or `uv`
- One of:
  - `codex`
  - `claude`
- A WhatsApp account or number to pair with the bridge

## Install

### Quick install (PyPI)

```bash
# zero-install run with uv
uvx whatsapp-agent-cli install

# or as a persistent tool
uv tool install whatsapp-agent-cli
whatsapp-agent install

# or classic pip
pip install whatsapp-agent-cli
whatsapp-agent install
```

The CLI exposes:

```bash
whatsapp-agent install [--reconfigure] [--non-interactive]
whatsapp-agent pair                          # re-pair WhatsApp (prints QR)
whatsapp-agent run                           # foreground gateway (no systemd)
whatsapp-agent service start|stop|status|restart|logs
whatsapp-agent doctor                        # diagnose the install
whatsapp-agent path                          # print the install dir
whatsapp-agent --version
```

### From source

```bash
git clone https://github.com/kalki-kgp/whatsapp-agent-cli.git
cd whatsapp-agent-cli
bash scripts/install.sh
```

Both paths run the same interactive TUI (arrow keys to pick, Enter to confirm). It will:

- auto-detect `claude` and `codex` on your `PATH` and pick one
- ask whether to run in `bot` or `self-chat` mode
- ask for your allowed WhatsApp number(s)
- pick the next free bridge port starting at `3010`
- hide root / model / port / CLI-path behind an opt-in **Advanced** toggle
- show a review screen, then install Python + Node deps, write `.env`, and install the user service
- offer to pair WhatsApp immediately

By default, the runtime is installed into `~/.agent-whatsapp` and the service is named `agent-whatsapp.service`.

### Installer flags

```bash
bash scripts/install.sh --reconfigure      # re-run prompts using values from existing .env as defaults
bash scripts/install.sh --non-interactive  # no prompts; uses env vars + auto-detect (CI-friendly)
bash scripts/install.sh --help             # full flag + env-var reference
```

In `--non-interactive` mode the only required input is `WHATSAPP_ALLOWED_USERS`. Everything else falls back to auto-detection or the saved `.env`.

## Pairing

If you skip pairing during install, run:

```bash
bash ~/.agent-whatsapp/scripts/pair.sh
```

That prints a QR code in the terminal. Scan it with WhatsApp and the session will be stored under:

```bash
~/.agent-whatsapp/whatsapp/session
```

## Running

Start the service:

```bash
systemctl --user start agent-whatsapp.service
```

Check status:

```bash
systemctl --user status agent-whatsapp.service --no-pager
```

Inspect logs:

```bash
journalctl --user -u agent-whatsapp.service -n 100 --no-pager
```

Stop it:

```bash
systemctl --user stop agent-whatsapp.service
```

## Configuration

The installer writes `~/.agent-whatsapp/.env`.

Important settings:

- `AGENT_BACKEND`
  `codex` or `claude`
- `AGENT_COMMAND`
  Path or command name for the selected CLI
- `AGENT_MODEL`
  Optional default model
- `AGENT_ROOT`
  Default working directory
- `WHATSAPP_MODE`
  `bot` or `self-chat`
- `WHATSAPP_ALLOWED_USERS`
  Comma-separated phone numbers or WhatsApp IDs allowed to talk to the bridge
- `WHATSAPP_PORT`
  Local bridge port

For allowlisting, full international format is the safest, for example:

```env
WHATSAPP_ALLOWED_USERS=917385166726
```

The bridge is also tolerant of common suffix-only input and LID mappings, but full country-code format is still the cleanest option.

## Chat commands

- `/status`
  Show backend, root, active thread, model, summary state, and saved-session count.
- `/new`
  Archive the current session and start fresh.
- `/clear`
  Same behavior as `/new`.
- `/reset`
  Clear the live session immediately.
- `/resume`
  List saved sessions for the current chat.
- `/resume <name>`
  Restore a saved session by name.
- `/title <name>`
  Name the current session.
- `/root /absolute/path`
  Change the working directory for the current chat.
- `/model <name>`
  Change the model for the current chat.
- `/compact`
  Roll the current conversation into a carry-forward summary and clear the live thread.
- `/help`
  Show the command list.

## Repository layout

- `bridge/`
  Node-based WhatsApp bridge
- `server/`
  Python gateway that manages sessions and CLI execution
- `scripts/install.sh`
  Guided install flow
- `scripts/pair.sh`
  Pairing helper
- `scripts/start.sh`
  Service start helper
- `scripts/stop.sh`
  Service stop helper
- `systemd/agent-whatsapp.service`
  User service unit template

## Notes

- This project is designed to run as an isolated WhatsApp control layer for coding CLIs.
- It does not need to attach itself to your existing Telegram setup or other local agent workflows.
- `.env` is intentionally not committed.
- If you wipe `~/.agent-whatsapp`, you also wipe the saved WhatsApp session and will need to pair again.
