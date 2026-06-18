# whatsapp-agent-cli

**Your AI coding agent, on WhatsApp.**

Send a message from your phone. Claude or Codex runs on your server, edits files, fixes bugs, writes code — and replies directly in the chat.

<p align="left">
  <a href="https://pypi.org/project/whatsapp-agent-cli/"><img src="https://img.shields.io/pypi/v/whatsapp-agent-cli?color=25D366&label=pypi" alt="PyPI"></a>
  <a href="https://pypi.org/project/whatsapp-agent-cli/"><img src="https://img.shields.io/pypi/pyversions/whatsapp-agent-cli" alt="Python versions"></a>
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey" alt="Platform">
  <a href="https://github.com/kalki-kgp/whatsapp-agent-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT License"></a>
</p>

---

## Demo

> **📹 GIF coming soon** — screen recording of: typing a task in WhatsApp → agent running on server → reply arriving with the fix.
>
> To record your own: open WhatsApp on your phone, send a coding task, and capture the reply. Tools: [LICEcap](https://www.cockos.com/licecap/) (Windows/Mac) or `peek` (Linux) for GIF, or just upload an MP4 and GitHub will inline it.

---

## What is this?

You type a coding task on WhatsApp. Your server runs the actual `codex` CLI, or the Claude Code channel beta, with file reads, edits, shell commands, everything. The reply comes back in the same chat.

No SSH. No terminal app. No web UI to maintain. Just WhatsApp, which you already have open.

Codex is the stable path: each chat keeps its own working directory, model, and session context, so you can have one chat for your backend repo and another for your side project, and they stay completely separate.

Claude is beta. It no longer uses `claude -p`; it starts one long-lived Claude Code session through channels so usage flows through the interactive subscription pool. The WhatsApp channel plugin is still being built feature by feature, so expect fewer commands and rough edges than Codex for now.

---

## Install

One command on your Linux server:

```bash
curl -fsSL https://raw.githubusercontent.com/kalki-kgp/whatsapp-agent-cli/main/scripts/bootstrap.sh | bash
```

Then follow the interactive setup — it auto-detects Claude/Codex, picks a port, and installs a systemd service. Takes about 2 minutes.

**Requirements:** Linux with `systemd --user`, Python 3.10+, Node.js 18+, and `codex` already installed for the stable backend. Claude beta also requires Claude Code v2.1.80+, Bun, and claude.ai or Console authentication.

---

## What you can do from WhatsApp

Stable today on Codex:

- `"Fix the auth bug in user.py"` — agent edits the file, replies with what changed
- `"Add dark mode to the dashboard"` — full file edits, multi-step, all from your phone
- `"What's the status of the API refactor?"` — agent reads code and explains
- `"Run the tests and tell me what's failing"` — agent shells out, reports back
- Send a **voice note** — it gets transcribed via Whisper and sent to the agent
- Send an **image or document** — agent sees it (if the model supports vision)

---

## Features

- **Full CLI access** — shells out to the real `codex` binary; Claude beta uses Claude Code channels instead of `claude -p`
- **Per-chat session state** — stable for Codex: each WhatsApp chat has its own working directory, model, session ID, and 30 saved sessions
- **Long-term memory** — stable for Codex: daily rollover writes a carry-forward summary so context survives across days
- **Voice note transcription** — stable for Codex: optional Whisper integration turns voice messages into agent prompts
- **Bot or self-chat mode** — use a dedicated WhatsApp number for the agent, or text your own number
- **One-command install + upgrade** — `uv tool install whatsapp-agent-cli`, upgrades handled from within WhatsApp via `/yes`
- **100% self-hosted** — all data stays on your server, nothing goes through a third party

---

## Chat commands

These commands are stable on the Codex backend. Claude beta does not run the Python gateway message loop, so command parity will arrive gradually inside the channel plugin.

| Command | What it does |
|---|---|
| `/new` | Archive current session, start fresh |
| `/compact` | Summarize and continue — saves context window |
| `/root /path/to/repo` | Switch the working directory for this chat |
| `/model claude-opus-4` | Change the model on the fly |
| `/resume` | List saved sessions and restore one |
| `/search-session <query>` | Find a past session by description |
| `/memory` | View this chat's long-term memory files |
| `/status` | Backend, model, root, session info |
| `/help` | All commands |

---

## Quick start (non-interactive)

```bash
curl -fsSL https://raw.githubusercontent.com/kalki-kgp/whatsapp-agent-cli/main/scripts/bootstrap.sh \
  | WHATSAPP_ALLOWED_USERS=917385166726 bash -s -- install --non-interactive
```

Replace `917385166726` with your WhatsApp number in international format.

---

## How it works

The shape depends on the backend.

**Codex (default):**

```
You (WhatsApp)
     │
     ▼
bridge/bridge.js   ← Node.js + Baileys, handles WhatsApp WebSocket
     │  long-poll
     ▼
server/gateway.py  ← Python async, per-chat sessions and state
     │  subprocess
     ▼
codex              ← spawned per message
     │
     └──── reply ──▶ You (WhatsApp)
```

**Claude beta:** runs through the [official channels mechanism](https://code.claude.com/docs/en/channels) instead of `claude -p`, so usage hits the interactive subscription pool rather than the new Agent SDK credit pool. The gateway becomes a thin supervisor that registers the local marketplace shipped under `plugins/`, installs the `whatsapp` plugin from it, and keeps one long-lived `claude --dangerously-load-development-channels plugin:whatsapp@whatsapp-agent-cli …` process alive under a PTY. The WhatsApp connection and per-chat routing live inside the channel plugin in `plugins/whatsapp/` (a Bun MCP server). Today this path supports QR pairing, inbound text, and text replies; feature parity with Codex will arrive gradually inside the plugin.

Everything runs in `~/.agent-whatsapp/`. On Codex, the bridge stores WhatsApp credentials and the gateway stores per-chat state in `state.json` plus memory files under `memory/`. On Claude beta, channel/plugin state lives under the Claude channel plugin. Nothing leaves your box.

---

## CLI reference

```bash
whatsapp-agent install [--reconfigure]   # interactive setup or re-configure
whatsapp-agent pair                      # pair/re-pair WhatsApp for the Codex bridge
whatsapp-agent run                       # live monitor (foreground)
whatsapp-agent service start|stop|restart|status|logs
whatsapp-agent doctor                    # diagnose the install
whatsapp-agent uninstall                 # full teardown
whatsapp-agent --version
```

---

## Configuration

Settings live in `~/.agent-whatsapp/.env`. Re-run `whatsapp-agent install --reconfigure` to change them interactively.

| Var | Purpose |
|---|---|
| `AGENT_BACKEND` | `codex` or `claude` (`claude` is beta) |
| `AGENT_COMMAND` | Path to the CLI binary |
| `AGENT_MODEL` | Default model (blank = CLI default) |
| `AGENT_ROOT` | Default working directory for new chats |
| `WHATSAPP_MODE` | `bot` (dedicated number) or `self-chat` (your own number) |
| `WHATSAPP_ALLOWED_USERS` | Comma-separated phone numbers allowed to message the agent |
| `WHATSAPP_PORT` | Local bridge port (default `3010`) |
| `AGENT_MEMORY_ENABLED` | Set `0` to disable long-term memory |
| `AGENT_MEMORY_ROLLOVER_TIME` | Daily time (`HH:MM`) to update memory and roll sessions |
| `AGENT_TRANSCRIBE_AUDIO` | Set `1` to enable Whisper voice transcription |
| `AGENT_WHISPER_MODEL` | Whisper model size (`base`, `small`, `medium`) |
| `AGENT_UPGRADE_CHECK` | Set `0` to disable PyPI upgrade notices |
| `CW_LOG_LEVEL` | Python log level (default `INFO`) |
| `CLAUDE_BIN` | Claude backend only — path to the claude binary (default: `claude` on PATH) |
| `CLAUDE_PLUGIN_DIR` | Claude backend only — absolute path to the WhatsApp channel plugin (default: `~/.agent-whatsapp/plugins/whatsapp`) |
| `CLAUDE_CHANNEL_SPEC` | Claude backend only — channel spec (default: `plugin:whatsapp@whatsapp-agent-cli`) |
| `CLAUDE_CHANNEL_EXTRA_ARGS` | Claude backend only — extra args appended to the `claude` command (shell-split) |

---

## Troubleshooting

```bash
whatsapp-agent doctor   # checks Python, runtime deps, .env, and backend-specific deps
whatsapp-agent service logs   # live journalctl output
```

Common issues:
- **QR not scanning** — run `whatsapp-agent pair --reset --yes` to force a fresh QR
- **Messages not arriving** — check `WHATSAPP_ALLOWED_USERS` includes your number with country code
- **Agent not responding** — run `whatsapp-agent doctor` and check service logs

---

## Privacy

All data stays on your host. WhatsApp credentials, chat sessions, and CLI output never leave the box. The bridge speaks to WhatsApp's servers only — same as the official WhatsApp Web client. `.env` is mode `600`.

---

## License

MIT — see [LICENSE](./LICENSE).
