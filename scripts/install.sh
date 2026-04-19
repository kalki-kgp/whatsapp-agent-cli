#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/kalki-kgp/codex-whatsapp.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-whatsapp}"
SERVICE_NAME="${SERVICE_NAME:-agent-whatsapp}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

prompt_default() {
  local label="$1"
  local default_value="$2"
  local answer
  read -r -p "$label [$default_value]: " answer
  if [[ -z "$answer" ]]; then
    printf '%s\n' "$default_value"
  else
    printf '%s\n' "$answer"
  fi
}

prompt_yes_no() {
  local label="$1"
  local default_value="${2:-y}"
  local answer
  read -r -p "$label [$default_value]: " answer
  answer="${answer:-$default_value}"
  [[ "${answer,,}" =~ ^y(es)?$ ]]
}

choose_backend() {
  local backend
  backend="$(prompt_default 'Backend (codex/claude)' 'codex')"
  backend="${backend,,}"
  if [[ "$backend" != "codex" && "$backend" != "claude" ]]; then
    echo "Invalid backend: $backend" >&2
    exit 1
  fi
  printf '%s\n' "$backend"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
}

clone_or_update_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
}

install_runtime() {
  export PATH="$HOME/.local/bin:$PATH"
  uv venv "$INSTALL_DIR/.venv" --python "$PYTHON_BIN"
  uv pip install --python "$INSTALL_DIR/.venv/bin/python" -r "$INSTALL_DIR/requirements.txt"
  (cd "$INSTALL_DIR/bridge" && npm install)
}

write_env() {
  local backend="$1"
  local agent_command="$2"
  local model="$3"
  local root="$4"
  local mode="$5"
  local allowed_users="$6"
  local port="$7"

  cat > "$INSTALL_DIR/.env" <<EOF
AGENT_WHATSAPP_HOME=$INSTALL_DIR
AGENT_BACKEND=$backend
AGENT_COMMAND=$agent_command
AGENT_MODEL=$model
AGENT_ROOT=$root
WHATSAPP_MODE=$mode
WHATSAPP_ALLOWED_USERS=$allowed_users
WHATSAPP_REPLY_PREFIX=
WHATSAPP_PORT=$port
CW_LOG_LEVEL=INFO
EOF
  chmod 600 "$INSTALL_DIR/.env"
}

install_service() {
  mkdir -p "$HOME/.config/systemd/user"
  sed "s#__INSTALL_DIR__#$INSTALL_DIR#g" \
    "$INSTALL_DIR/systemd/agent-whatsapp.service" \
    > "$HOME/.config/systemd/user/$SERVICE_NAME.service"
  systemctl --user daemon-reload
  systemctl --user enable "$SERVICE_NAME.service"
}

main() {
  local backend mode allowed_users root port agent_command default_command model

  backend="$(choose_backend)"
  mode="$(prompt_default 'WhatsApp mode (bot/self-chat)' 'bot')"
  allowed_users="$(prompt_default 'Allowed users (comma-separated phone/LID ids)' '')"
  root="$(prompt_default 'Default working root' "$HOME")"
  port="$(prompt_default 'Bridge port' '3010')"

  if [[ "$backend" == "codex" ]]; then
    default_command="${CODEX_COMMAND:-codex}"
  else
    default_command="${CLAUDE_COMMAND:-$HOME/.local/bin/claude}"
  fi
  agent_command="$(prompt_default 'CLI command path' "$default_command")"
  model="$(prompt_default 'Default model (leave blank for CLI default)' '')"

  mkdir -p "$INSTALL_DIR"
  clone_or_update_repo
  ensure_uv
  install_runtime
  write_env "$backend" "$agent_command" "$model" "$root" "$mode" "$allowed_users" "$port"
  install_service

  echo
  echo "Installed into: $INSTALL_DIR"
  echo "Service name: $SERVICE_NAME.service"
  echo
  echo "Next step: pair WhatsApp"
  echo "  $INSTALL_DIR/scripts/pair.sh"
  echo

  if prompt_yes_no 'Pair now?' 'y'; then
    "$INSTALL_DIR/scripts/pair.sh"
    systemctl --user restart "$SERVICE_NAME.service"
    echo
    echo "Pairing complete and service restarted."
  else
    echo "Pair later, then start the service with:"
    echo "  systemctl --user start $SERVICE_NAME.service"
  fi
}

main "$@"
