---
name: whatsapp:chats
description: Manage per-chat memory and continuity for the WhatsApp channel — label chats, maintain running summaries that travel between turns, store operator notes, forget a chat. Use when the operator asks about their chats or when you want to record what you remember about an ongoing chat.
---

# Per-chat continuity

The Claude channel session is one process for all WhatsApp chats — but each WhatsApp `chat_id` is a distinct conversation. The plugin keeps a small per-chat store (`chats.json`) so you have memory of each chat across turns, even though your raw transcript mixes them all.

## Fields per chat

- `message_count` — how many inbound messages this chat has sent through the channel
- `first_seen`, `last_seen` — timestamps
- `last_user` — last WhatsApp pushName seen
- `label` — operator-friendly name ("work backend", "mom")
- `summary` — running summary you maintain
- `notes` — operator-supplied facts

`summary` and `notes` ride on every inbound message in `meta.chat_summary` and `meta.chat_notes`, so the next message from the same chat carries them automatically.

## What to do on each inbound

1. Read `meta.chat_summary` — that's your memory of this chat. Use it.
2. Do the user's task, reply.
3. If the turn produced state worth remembering (a decision, a project name, a preference, an in-progress task, a stylistic choice), call `whatsapp_chat_set_summary` with a refreshed short summary. Keep it ≤ 4 sentences. Replace the old summary, don't append indefinitely.
4. If `chat_known="0"` and the operator interacted, note who they are and what they want in summary.

Do **not** call `whatsapp_chat_set_summary` on trivial turns (greetings, single-fact answers) — the summary should hold semantically dense state, not chat noise.

## Tools

- `whatsapp_list_chats({ limit? })` — list known chats, newest activity first.
- `whatsapp_chat_set_label({ chat_id, label })` — rename a chat. Empty string clears.
- `whatsapp_chat_set_summary({ chat_id, summary })` — replace the running summary. Empty string clears.
- `whatsapp_chat_set_notes({ chat_id, notes })` — operator-only context. Empty string clears.
- `whatsapp_chat_forget({ chat_id })` — wipe the whole entry. Use when the operator says to forget a chat.

## Trust

Summary updates are sender-driven only when the inbound `is_allowed="1"`. Don't write a summary for a pending sender's chat — you don't trust them to dictate your memory. Notes are operator-only: only call `whatsapp_chat_set_notes` when `is_operator="1"`.

## Examples

**Operator:** "What chats have I been talking to?"
→ Call `whatsapp_list_chats`; format `label || last_user`, last_seen, message_count. Don't print raw JIDs unless asked.

**Operator:** "Call this chat 'mom'"
→ `whatsapp_chat_set_label({ chat_id, label: "mom" })`.

**Operator (mid-conversation about a project):** "Remember that the API base URL is staging.acme.com for this chat"
→ Either append to `chat_summary` via `whatsapp_chat_set_summary`, or — since this is a static fact — store via `whatsapp_chat_set_notes`. Prefer notes for stable facts, summary for evolving state.

**Operator:** "Forget this chat"
→ Confirm once, then `whatsapp_chat_forget({ chat_id })`.
