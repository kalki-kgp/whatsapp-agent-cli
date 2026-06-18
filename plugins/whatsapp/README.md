# WhatsApp channel plugin

Companion plugin to `whatsapp-agent-cli`. It bridges a WhatsApp Web session
into a running Claude Code session via the official
[channels](https://code.claude.com/docs/en/channels) mechanism, so Claude
messages route through the interactive subscription pool instead of the Agent
SDK pool used by `claude -p`.

Status: **Claude beta, v0.2**.

Working now:

- Baileys WhatsApp transport inside `server.ts`
- QR pairing printed to `~/.agent-whatsapp/logs/claude-channel.log`
- inbound text messages delivered with `notifications/claude/channel`
- `reply` MCP tool sends text back to WhatsApp
- reconnect handling, self-chat echo filtering, optional `WHATSAPP_ALLOWED_USERS`
- `status` MCP tool for basic connection diagnostics

Still missing:

- `/whatsapp:access` skill and first-class pairing/allowlist state
- media download into channel metadata
- attachments on outbound replies
- reactions and message edits
- per-chat session naming/continuity helpers
- permission relay

Mirror the reference plugin while building this out:
[external_plugins/telegram](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram).

## Structure

```
plugins/whatsapp/
├── .claude-plugin/plugin.json   # plugin manifest
├── .mcp.json                    # registers server.ts as an MCP server
├── .npmrc
├── package.json                 # Bun deps
├── server.ts                    # Baileys transport + MCP channel server
└── skills/                      # /whatsapp:configure, /whatsapp:access (TODO)
```

## State

Default state lives at:

```
~/.claude/channels/whatsapp/
├── .env       # optional plugin env overrides
└── session/   # Baileys multi-file auth state
```

Useful env vars:

| Var | Purpose |
|---|---|
| `WHATSAPP_STATE_DIR` | Override channel state root |
| `WHATSAPP_SESSION_DIR` | Override Baileys auth directory |
| `WHATSAPP_MODE` | `bot` or `self-chat` |
| `WHATSAPP_ALLOWED_USERS` | Optional comma-separated phone numbers/LIDs |
| `WHATSAPP_REPLY_PREFIX` | Prefix self-chat replies to avoid echo loops |
| `WHATSAPP_TEXT_CHUNK_LIMIT` | Reply chunk size, default `3500` |
| `WHATSAPP_DEBUG` | Set `1` for verbose channel logs |

## Run It

The supervisor in `server/channel_supervisor.py` does all of this for you on
startup when `AGENT_BACKEND=claude`. For reference, the underlying calls are:

```bash
# one-time, idempotent, run by the supervisor
claude plugin marketplace add <abs path to plugins/>
claude plugin marketplace update whatsapp-agent-cli
claude plugin install whatsapp@whatsapp-agent-cli
claude plugin update whatsapp@whatsapp-agent-cli

# then, on every launch:
claude \
  --dangerously-load-development-channels plugin:whatsapp@whatsapp-agent-cli \
  --add-dir <workspace root> \
  --permission-mode bypassPermissions
```

`--dangerously-load-development-channels` is required while the plugin
isn't on Anthropic's allowlist or in an org's `allowedChannelPlugins`.
The supervisor invokes claude under a PTY so it stays in interactive mode.

On first launch, tail:

```bash
tail -f ~/.agent-whatsapp/logs/claude-channel.log
```

Scan the QR from WhatsApp -> Linked devices. Once connected, inbound text
messages should appear in the running Claude Code session as channel messages,
and Claude should call `reply` to send text back.
