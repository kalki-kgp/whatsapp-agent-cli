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

prompt_optional() {
  local label="$1"
  local answer
  read -r -p "$label: " answer
  printf '%s\n' "$answer"
}

prompt_yes_no() {
  local label="$1"
  local default_value="${2:-y}"
  local answer
  read -r -p "$label [$default_value]: " answer
  answer="${answer:-$default_value}"
  [[ "${answer,,}" =~ ^y(es)?$ ]]
}

choose_option() {
  local label="$1"
  local default_index="$2"
  shift 2

  local options=("$@")
  local answer
  local index

  echo "$label" >&2
  for index in "${!options[@]}"; do
    printf '  %d) %s\n' "$((index + 1))" "${options[$index]}" >&2
  done

  while true; do
    read -r -p "Select [$default_index]: " answer
    answer="${answer:-$default_index}"
    if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#options[@]} )); then
      printf '%s\n' "${options[$((answer - 1))]}"
      return
    fi
    echo "Pick a number from 1 to ${#options[@]}." >&2
  done
}

choose_backend() {
  choose_option "Backend" 1 "codex" "claude"
}

choose_mode() {
  choose_option "WhatsApp mode" 1 "bot" "self-chat"
}

choose_path_value() {
  local label="$1"
  local default_value="$2"
  local custom_label="${3:-Enter custom value}"
  local selection
  local custom_value

  selection="$(choose_option "$label" 1 "Use default: $default_value" "$custom_label")"
  if [[ "$selection" == "Use default: $default_value" ]]; then
    printf '%s\n' "$default_value"
    return
  fi

  custom_value="$(prompt_optional "$custom_label")"
  if [[ -z "$custom_value" ]]; then
    echo "A value is required." >&2
    exit 1
  fi
  printf '%s\n' "$custom_value"
}

choose_optional_value() {
  local label="$1"
  local default_value="$2"
  local custom_label="${3:-Enter custom value}"
  local selection

  selection="$(choose_option "$label" 1 "Use default: $default_value" "$custom_label")"
  if [[ "$selection" == "Use default: $default_value" ]]; then
    printf '%s\n' "$default_value"
    return
  fi

  prompt_optional "$custom_label"
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
  local model_choice

  backend="$(choose_backend)"
  mode="$(choose_mode)"
  allowed_users="$(prompt_optional 'Allowed users (comma-separated phone/LID ids, usually your full number with country code)')"
  root="$(choose_path_value 'Default working root' "$HOME" 'Enter custom working root')"
  port="$(choose_optional_value 'Bridge port' '3010' 'Enter custom bridge port')"

  if [[ "$backend" == "codex" ]]; then
    default_command="${CODEX_COMMAND:-codex}"
  else
    default_command="${CLAUDE_COMMAND:-$HOME/.local/bin/claude}"
  fi
  agent_command="$(choose_path_value 'CLI command path' "$default_command" 'Enter custom CLI command path')"

  model_choice="$(choose_option 'Default model' 1 'Use CLI default' 'Set a model explicitly')"
  if [[ "$model_choice" == 'Use CLI default' ]]; then
    model=""
  else
    model="$(prompt_optional 'Enter model name')"
  fi

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
