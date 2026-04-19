#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENT_WHATSAPP_HOME:-${INSTALL_DIR:-$HOME/.agent-whatsapp}}"

if [[ -f "$INSTALL_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTALL_DIR/.env"
  set +a
fi

PORT="${WHATSAPP_PORT:-3010}"
MODE="${WHATSAPP_MODE:-bot}"
SESSION_DIR="${WHATSAPP_SESSION_DIR:-$INSTALL_DIR/whatsapp/session}"

mkdir -p "$SESSION_DIR"
cd "$INSTALL_DIR/bridge"
node bridge.js --pair-only --port "$PORT" --session "$SESSION_DIR" --mode "$MODE"
