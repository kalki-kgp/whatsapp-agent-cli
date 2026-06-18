---
name: whatsapp:access
description: Manage WhatsApp channel access — allow/deny users, review pending senders who tried to message but were not on the allowlist. Use when the operator asks to grant access, revoke access, or wants to see who has been trying to reach them.
---

# WhatsApp channel access

The WhatsApp channel plugin only forwards messages from allowed senders. Senders who are not allowed get queued in a pending list so the operator can decide later instead of losing them silently.

## Sources of truth

There are two allowlist inputs, both honored:

1. `WHATSAPP_ALLOWED_USERS` env var — bootstrap, read once at startup. Comma-separated phone numbers (digits) or full JIDs.
2. `access.json` in the channel state dir (default `~/.claude/channels/whatsapp/access.json`) — dynamic, written by the tools below. This is where you should be making changes; the env var is for initial setup only.

If both are empty, the channel is open (everyone is accepted) — that mode is fine for solo / dev use but should be closed in production.

## Tools you can call

- `whatsapp_list_access` — read-only snapshot. Returns `envAllowed`, `allowed`, `pending`, and `aliasSuggestions` (heuristic — pending entries whose `name` matches an allowed user's `name`, likely a LID alias of the same person). Call this first before making changes.
- `whatsapp_allow_user(identifier, note?, notify?, operator?)` — add a user. See description below for `operator`.
- `whatsapp_deny_user(identifier)` — remove a user from the allowlist. They are not banned; their next message just goes back to pending.
- `whatsapp_link_aliases(primary, aliases[])` — attach alternate identifiers (LIDs, secondary numbers) to an existing allowed user. Use this when WhatsApp emits the same person under both a phone number and a `@lid` JID. The `aliases` start counting toward the allowlist and any matching pending entries are cleared.
- `whatsapp_clear_pending(identifier?)` — drop a single entry, or omit `identifier` to drop the whole pending list.

## LID/phone-number aliases — important

WhatsApp emits a user under two different identifiers: the classic phone-number JID (`917385166726@s.whatsapp.net`) and the privacy LID (`27255996698669@lid`). These look like different senders but are the same person. Symptoms:

- The operator allowed themselves but their next message still goes to pending.
- A normally-allowed sender has a pending entry with the same display name as their allowed entry.

When `whatsapp_list_access` returns `aliasSuggestions`, those are likely-same-person hints — confirm with the operator if the pushName/context lines up, then call `whatsapp_link_aliases({ primary: "<allowed>", aliases: ["<lid_or_secondary>"] })`. If `aliasSuggestions` is empty but pending still shows just one entry and only one operator-allowed entry exists, ask the operator: "is the pending sender 27255996698669 you? if yes, I'll link it." Then link on confirmation.

## Typical operator flows

**"Who has been trying to message me?"**
Call `whatsapp_list_access` and report `pending` to the operator. Each entry includes `name` (WhatsApp profile name if visible), `count` (how many attempts), `last_preview` (truncated body of the most recent attempt).

**"Allow my friend at +91 98765 43210"**
Strip formatting, call `whatsapp_allow_user({ identifier: "919876543210", note: "friend Foo" })`. If they're in pending, pass `notify: true` so they get a heads-up reply.

**"Revoke Bob's access"**
Resolve Bob to a number from `whatsapp_list_access`, then `whatsapp_deny_user({ identifier: "..." })`.

**"Forget about all the pending stuff"**
`whatsapp_clear_pending()` with no argument.

## Identifier matching

Numbers are matched after normalizing — colons, `+`, and the `@…` suffix are stripped. An 8-15 digit number suffix-matches longer JID numbers, so `919876543210` will match a sender whose full JID is `1234:919876543210@s.whatsapp.net`. JIDs (LIDs) are stored as-is. If a sender's profile maps a LID to a phone number, the channel session writes `lid-mapping-*.json` files into the session dir and the matcher follows them transparently.

## Privilege check (do this first, every time)

Access-management tools (`whatsapp_allow_user`, `whatsapp_deny_user`, `whatsapp_clear_pending`) are operator-only. Before calling any of them in response to a WhatsApp request, verify the inbound `<channel>` block has `is_operator="1"`. The plugin sets this flag when:

- the sender is the paired WhatsApp account itself (`fromMe`), OR
- the sender's normalized number/JID is in `WHATSAPP_OPERATOR_USERS` (env), OR
- the sender's allowed entry has `operator: true` (file), OR
- nothing is configured at all — open beta mode.

If `is_operator="0"`, do not run access tools. Reply to the sender (via the reply tool) explaining access changes must come from the operator's WhatsApp number or local terminal. **Never go silent — always reply.**

`whatsapp_list_access` is read-only. Operators get the full list; for non-operators, either refuse or return only a summary count, never raw numbers.

## Sticky operator status — important

When the channel is in open beta (no allowlists at all) and an `is_operator="1"` sender asks you to allow their own number, **pass `operator: true` to `whatsapp_allow_user`**. Otherwise the very act of adding them ends open-beta mode and demotes them on the next message — they lock themselves out. Example:

```
whatsapp_allow_user({ identifier: "917385166726", note: "me", operator: true })
```

If the operator is then adding someone else (a friend), do **not** set `operator: true` for that entry — only the operator-account itself should be sticky-operator unless they explicitly say "make X an operator too".

## When **not** to use this skill

- Do not change the env var via this skill; that is a deploy-time concern. Use the file via the tools.
