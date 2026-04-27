# whatsapp-agent-cli

Run a coding CLI behind a dedicated WhatsApp number.

<p align="left">
  <a href="https://pypi.org/project/whatsapp-agent-cli/"><img src="https://img.shields.io/pypi/v/whatsapp-agent-cli?color=25D366&label=pypi" alt="PyPI"></a>
  <a href="https://pypi.org/project/whatsapp-agent-cli/"><img src="https://img.shields.io/pypi/pyversions/whatsapp-agent-cli" alt="Python versions"></a>
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey" alt="Platform">
  <a href="https://github.com/kalki-kgp/whatsapp-agent-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT License"></a>
</p>

`whatsapp-agent` installs a WhatsApp bridge on your server, wires it to a local coding CLI (`codex` or `claude`), and keeps per-chat session state — so you can message your server like a real operator instead of SSHing in every time.

```text
You (WhatsApp)  ──▶  Baileys bridge  ──▶  gateway  ──▶  codex / claude
                          ▲                  │
                          └──── replies ◀────┘
```

## Why

- **One number per server.** Your phone becomes the control surface. No web UIs, no port forwarding, no VPNs.
- **Per-chat memory.** Each WhatsApp chat keeps its own working directory, model, session, daily memory files, and saved session ids — so chat A can stay on one repo while chat B works somewhere else.
- **Real CLI access.** It's not a wrapper API — it shells out to the actual `codex` / `claude` binary on the host with full tool use, file edits, etc.
- **Self-hosted, local-only.** Everything (bridge, gateway, state) runs on your box. No third-party message broker.

## Requirements

- Linux server with `systemd --user` (or macOS for `whatsapp-agent run` foreground mode)
- Python 3.10+
- Node.js 18+
- One of `codex` or `claude`, already installed and authenticated on the server
- A WhatsApp account or number to pair with the bridge

## Install

The fastest path — one command, zero clones:

```bash
uvx whatsapp-agent-cli install
```

Or install the CLI persistently:

```bash
# uv (recommended)
uv tool install whatsapp-agent-cli

# pip
pip install whatsapp-agent-cli
```

Then run:

```bash
whatsapp-agent install
```

The installer is an interactive TUI (arrow keys to pick, Enter to confirm). It will:

- auto-detect `claude` and `codex` on your `PATH` and pick one
- ask whether to run in `bot` or `self-chat` mode
- ask for your allowed WhatsApp number(s)
- pick the next free bridge port starting at `3010`
- hide root / model / port / CLI-path behind an opt-in **Advanced** toggle
- show a review screen, then install Python + Node deps, write `.env`, and install the user service
- offer to pair WhatsApp immediately

Default install root: `~/.agent-whatsapp`. Default service: `agent-whatsapp.service`.

### Non-interactive install

```bash
WHATSAPP_ALLOWED_USERS=919876543210 \ (Your WhatsApp number with country code)
  whatsapp-agent install --non-interactive
```

Reads from env vars (`AGENT_BACKEND`, `AGENT_COMMAND`, `WHATSAPP_MODE`, `WHATSAPP_PORT`, `AGENT_ROOT`, `AGENT_MODEL`) and falls back to auto-detection. Only `WHATSAPP_ALLOWED_USERS` is mandatory.

### From source

```bash
git clone https://github.com/kalki-kgp/whatsapp-agent-cli.git
cd whatsapp-agent-cli
bash scripts/install.sh
```

## Pair WhatsApp

If you skipped pairing during install:

```bash
whatsapp-agent pair
```

A QR code prints in the terminal. Scan it from WhatsApp → **Linked devices**. The session is stored under `~/.agent-whatsapp/whatsapp/session` and survives restarts.

## CLI reference

```bash
whatsapp-agent install [--reconfigure] [--non-interactive]
                                    # interactive setup; --reconfigure re-runs
                                    # prompts using saved .env as defaults
whatsapp-agent pair                 # re-pair WhatsApp (prints QR)
whatsapp-agent run                  # foreground gateway (no systemd; macOS too)
whatsapp-agent service start        # systemd user service controls
whatsapp-agent service stop
whatsapp-agent service restart
whatsapp-agent service status
whatsapp-agent service logs         # journalctl -f
whatsapp-agent doctor               # diagnose the install
whatsapp-agent path                 # print the install dir
whatsapp-agent uninstall            # stop service and remove install dir/state
whatsapp-agent --version
```

`--install-dir <path>` works on every subcommand if you want to manage multiple installs side-by-side.

`whatsapp-agent uninstall --yes` removes the systemd user service plus the full install directory, including `.env`, `.venv`, `node_modules`, WhatsApp pairing data, logs, and state. Use it before a clean reinstall if a previous runtime directory is wedged.

## Chat commands

Send these as WhatsApp messages from any allowed number:

| Command | What it does |
|---|---|
| `/status` | Backend, root, active thread, model, summary state, saved-session count |
| `/new` (or `/clear`) | Archive the current session, start fresh |
| `/reset` | Clear the live session immediately |
| `/resume` | List saved sessions for this chat |
| `/resume <name>` | Restore a saved session by name |
| `/title <name>` | Name the current session |
| `/root /abs/path` | Change the working directory for this chat |
| `/model <name>` | Change the model for this chat |
| `/compact` | Roll the conversation into a carry-forward summary |
| `/memory` | Show this chat's long-term memory index and session ids |
| `/memory update` | Update memory files, archive the active session, and preload the next session summary |
| `/yes` | Approve a pending gateway action, such as a package upgrade |
| `/no` | Dismiss a pending gateway action |
| `/help` | Show the command list |

## Configuration

Settings live in `~/.agent-whatsapp/.env`. Edit by hand or re-run `whatsapp-agent install --reconfigure`.

| Var | Purpose |
|---|---|
| `AGENT_BACKEND` | `codex` or `claude` |
| `AGENT_COMMAND` | Path or command name for the selected CLI |
| `AGENT_MODEL` | Default model (blank = CLI default) |
| `AGENT_ROOT` | Default working directory for new chats |
| `WHATSAPP_MODE` | `bot` (separate WhatsApp account) or `self-chat` (your own number) |
| `WHATSAPP_ALLOWED_USERS` | Comma-separated phone numbers / LIDs allowed to message the bridge |
| `WHATSAPP_PORT` | Local bridge HTTP port (default `3010`) |
| `CW_LOG_LEVEL` | Python log level (default `INFO`) |
| `AGENT_MEMORY_ENABLED` | Set to `0` to disable long-term memory and daily rollovers |
| `AGENT_MEMORY_DIR` | Memory root; each chat gets a folder containing `MEMORY.md` |
| `AGENT_MEMORY_ROLLOVER_TIME` | Daily local time, `HH:MM`, to update memory and roll sessions forward |
| `AGENT_MEMORY_FILES` | Comma-separated core memory files; default includes `user.md`, `career.md`, `projects.md`, `preferences.md`, `open-loops.md` |
| `AGENT_UPGRADE_CHECK` | Set to `0` to disable PyPI upgrade notices |
| `AGENT_PACKAGE_VERSION` | Installed package version used for upgrade notices |
| `SERVICE_NAME` | systemd user service name used by approved upgrades |

For `WHATSAPP_ALLOWED_USERS`, full international format is the safest:

```env
WHATSAPP_ALLOWED_USERS=917385166726, 14155551212
```

The bridge also tolerates suffix-only input and resolves LID↔phone via `bridge/allowlist.js`, but country-code format is the cleanest.

When the gateway sees a newer `whatsapp-agent-cli` release on PyPI, it appends an upgrade approval prompt to replies. Reply `/yes` to let the agent run the upgrade command in that same chat, or `/no` to dismiss that version.

## Long-Term Memory

Memory is enabled by default. At `AGENT_MEMORY_ROLLOVER_TIME` each day, the gateway finds active chats whose current session started before that time, asks the live agent session to update long-term memory files and write a carry-forward summary, archives the current session id, and starts the next session with that summary preloaded.

Each chat gets its own folder under `AGENT_MEMORY_DIR`:

```text
memory/<chat-id-hash>/
  MEMORY.md
  user.md
  career.md
  projects.md
  preferences.md
  open-loops.md
  sessions/
```

`MEMORY.md` is the index. Topic files hold the details, and `sessions/` stores daily rollover records with the archived session id. Send `/memory` to see the paths and active/previous session ids, or `/memory update` to force the rollover and summary handoff immediately.

## Troubleshooting

Run a self-diagnostic:

```bash
whatsapp-agent doctor
```

It checks Python / Node / uv versions, install dir population, `.env` validity, CLI binary existence, the runtime venv, and bridge `node_modules` — and tells you exactly what's broken.

For deeper digging, tail the live logs:

```bash
whatsapp-agent service logs       # systemd
# or, in foreground / on macOS
whatsapp-agent run
```

## Architecture

Two processes, one wrapper CLI on top.

```text
┌────────────────────────────────────────────────────────────────┐
│  ~/.agent-whatsapp/                                            │
│                                                                │
│   bridge/bridge.js  (Node + Baileys)                           │
│     ├─ pairs with WhatsApp                                     │
│     ├─ exposes 127.0.0.1:WHATSAPP_PORT                         │
│     └─ stores creds in whatsapp/session/                       │
│                                                                │
│   server/gateway.py  (Python + aiohttp)                        │
│     ├─ polls bridge /messages                                  │
│     ├─ per-chat asyncio.Lock                                   │
│     ├─ persists state.json                                     │
│     └─ shells out to codex / claude with --resume              │
│                                                                │
│   .venv/             (python deps for the gateway)             │
│   bridge/node_modules/                                         │
│   memory/            (per-chat MEMORY.md + topic files)        │
│   .env               (config, mode 600)                        │
│   state.json         (per-chat session metadata)               │
└────────────────────────────────────────────────────────────────┘
```

`whatsapp-agent install` populates this layout from a wheel-bundled copy of `bridge/`, `server/`, `scripts/`, and `systemd/`, then runs `npm install` and `uv pip install`. Subsequent `whatsapp-agent install --reconfigure` rewrites only `.env`, leaving the venv / node_modules / WhatsApp session intact.

Per-chat session state stores: `thread_id`, `root`, `model`, `summary`, memory rollover metadata, and up to 30 `saved_sessions`.

## Repository layout

| Path | What it is |
|---|---|
| `src/whatsapp_agent/cli.py` | The `whatsapp-agent` Python CLI (entry point) |
| `bridge/` | Node WhatsApp bridge (Baileys) |
| `server/gateway.py` | Python async gateway + per-chat session state |
| `scripts/install.sh` | The TUI installer (also runnable standalone) |
| `scripts/pair.sh` | Pairing helper |
| `scripts/start.sh`, `stop.sh` | Service convenience wrappers |
| `systemd/agent-whatsapp.service` | User-mode systemd unit template |
| `pyproject.toml` | Hatchling build, ships the runtime inside the wheel |

## Privacy

- All data stays on your host. WhatsApp credentials, chat sessions, and CLI output never leave the box.
- The bridge speaks to WhatsApp's servers only — same as the official WhatsApp Web client.
- `.env` is mode `600` and intentionally not committed.

## Notes

- Wiping `~/.agent-whatsapp` also wipes the WhatsApp session — you'll need to `whatsapp-agent pair` again.
- This project is designed as an isolated WhatsApp control layer for coding CLIs. It does not need to attach itself to your existing Telegram setup or other local agent workflows.
- macOS hosts work for `whatsapp-agent install` and `whatsapp-agent run`; the `service` subcommands need Linux + `systemd --user`.

## License

MIT — see [LICENSE](./LICENSE).
