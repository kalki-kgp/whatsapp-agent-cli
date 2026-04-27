#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# whatsapp-agent installer
# ─────────────────────────────────────────────────────────────────────────────

REPO_URL="${REPO_URL:-https://github.com/kalki-kgp/codex-whatsapp.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-whatsapp}"
SERVICE_NAME="${SERVICE_NAME:-agent-whatsapp}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

RECONFIGURE=0
NON_INTERACTIVE=0

# ── theme ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
  GREEN=$'\033[38;2;37;211;102m'
  GREEN_BG=$'\033[48;2;37;211;102m\033[30m'
  BOLD=$'\033[1m'
  DIM=$'\033[2m'
  RED=$'\033[31m'
  YELLOW=$'\033[33m'
  RESET=$'\033[0m'
  HIDE_CURSOR=$'\033[?25l'
  SHOW_CURSOR=$'\033[?25h'
else
  GREEN=""; GREEN_BG=""; BOLD=""; DIM=""; RED=""; YELLOW=""; RESET=""
  HIDE_CURSOR=""; SHOW_CURSOR=""
fi

cleanup() { printf '%s' "$SHOW_CURSOR"; }
trap 'cleanup; exit 130' INT TERM
trap 'cleanup' EXIT

# ── usage ────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
whatsapp-agent installer

Usage:
  bash scripts/install.sh [flags]

Flags:
  --reconfigure        Re-run the prompts using the values in
                       ~/.agent-whatsapp/.env as defaults.
  --non-interactive    No prompts. Read from env vars + auto-detect.
                       Fails only if WHATSAPP_ALLOWED_USERS is not set.
  -h, --help           Show this help.

Env vars consumed in --non-interactive mode (or as defaults):
  AGENT_BACKEND          codex | claude
  AGENT_COMMAND          path to the CLI binary
  AGENT_MODEL            model name (blank = CLI default)
  AGENT_MEMORY_DIR       memory root (default INSTALL_DIR/memory)
  AGENT_MEMORY_ENABLED   1/0 toggle for long-term memory
  AGENT_MEMORY_ROLLOVER_TIME
                         daily local rollover time, HH:MM (default 04:00)
  AGENT_PACKAGE_VERSION  installed whatsapp-agent-cli version
  AGENT_ROOT             default working directory
  WHATSAPP_MODE          bot | self-chat
  WHATSAPP_ALLOWED_USERS comma-separated phone numbers (E.164 digits)
  WHATSAPP_PORT          bridge HTTP port
  INSTALL_DIR            install root (default ~/.agent-whatsapp)
  SERVICE_NAME           systemd user service name (default agent-whatsapp)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reconfigure) RECONFIGURE=1 ;;
    --non-interactive|--noninteractive|-y) NON_INTERACTIVE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

INTERACTIVE=1
if [[ "$NON_INTERACTIVE" == "1" || ! -t 0 || ! -t 1 ]]; then
  INTERACTIVE=0
fi

# ── banner ───────────────────────────────────────────────────────────────────
banner() {
  printf '\n'
  printf '  %s▌▌%s  %swhatsapp-agent%s  %s▌▌%s  %s· installer%s\n' \
    "$GREEN" "$RESET" "$BOLD" "$RESET" "$GREEN" "$RESET" "$DIM" "$RESET"
  printf '  %s%s%s\n' "$DIM" "  run a coding cli behind a whatsapp number" "$RESET"
  printf '\n'
}

section() {
  printf '\n  %s%s%s %s%s%s\n' "$GREEN" "──" "$RESET" "$BOLD" "$1" "$RESET"
}

note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail() { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; }

# ── input primitives ─────────────────────────────────────────────────────────
read_key() {
  local k1="" k2="" k3=""
  IFS= read -rsn1 k1 || return 1
  if [[ "$k1" == $'\033' ]]; then
    IFS= read -rsn1 -t 1 k2 2>/dev/null || k2=""
    if [[ "$k2" == "[" || "$k2" == "O" ]]; then
      IFS= read -rsn1 -t 1 k3 2>/dev/null || k3=""
    fi
    printf '%s%s%s' "$k1" "$k2" "$k3"
  else
    printf '%s' "$k1"
  fi
}

# Selects from an option list with arrow keys.
# Sets MENU_RESULT and MENU_INDEX (0-based).
# args: title, helper, default_index (1-based), opts...
MENU_RESULT=""
MENU_INDEX=0
menu_select() {
  local title="$1" helper="$2" default_idx="$3"
  shift 3
  local options=("$@")
  local n=${#options[@]}
  local cur=$((default_idx - 1))
  (( cur < 0 )) && cur=0
  (( cur >= n )) && cur=$((n - 1))

  if [[ $INTERACTIVE -eq 0 ]]; then
    MENU_RESULT="${options[$cur]}"
    MENU_INDEX=$cur
    return 0
  fi

  printf '\n  %s%s%s\n' "$BOLD" "$title" "$RESET"
  [[ -n "$helper" ]] && printf '  %s%s%s\n' "$DIM" "$helper" "$RESET"
  printf '\n'

  local i
  for ((i=0; i<n; i++)); do
    if [[ $i -eq $cur ]]; then
      printf '  %s▸%s %s%s%s\n' "$GREEN" "$RESET" "$BOLD" "${options[$i]}" "$RESET"
    else
      printf '    %s%s%s\n' "$DIM" "${options[$i]}" "$RESET"
    fi
  done

  printf '%s' "$HIDE_CURSOR"
  while true; do
    local key
    key=$(read_key) || key=""
    case "$key" in
      $'\033[A'|$'\033OA'|'k')  cur=$(( (cur - 1 + n) % n )) ;;
      $'\033[B'|$'\033OB'|'j')  cur=$(( (cur + 1) % n )) ;;
      "")  printf '%s' "$SHOW_CURSOR"
           MENU_RESULT="${options[$cur]}"
           MENU_INDEX=$cur
           return 0 ;;
      q|$'\033')  printf '%s\n' "$SHOW_CURSOR"
                  fail "Cancelled."
                  exit 130 ;;
    esac

    printf '\033[%dA' "$n"
    for ((i=0; i<n; i++)); do
      printf '\r\033[2K'
      if [[ $i -eq $cur ]]; then
        printf '  %s▸%s %s%s%s\n' "$GREEN" "$RESET" "$BOLD" "${options[$i]}" "$RESET"
      else
        printf '    %s%s%s\n' "$DIM" "${options[$i]}" "$RESET"
      fi
    done
  done
}

# Free-text prompt with optional default + validator function.
PROMPT_RESULT=""
VALIDATION_ERR=""
prompt_text() {
  local title="$1" helper="$2" default="${3:-}" validator="${4:-}"

  if [[ $INTERACTIVE -eq 0 ]]; then
    if [[ -n "$validator" ]] && ! "$validator" "$default"; then
      fail "$VALIDATION_ERR"
      exit 1
    fi
    PROMPT_RESULT="$default"
    return 0
  fi

  printf '\n  %s%s%s\n' "$BOLD" "$title" "$RESET"
  [[ -n "$helper" ]] && printf '  %s%s%s\n' "$DIM" "$helper" "$RESET"

  while true; do
    local hint=""
    [[ -n "$default" ]] && hint=" ${DIM}[${default}]${RESET}"
    local answer
    printf '  %s❯%s%s ' "$GREEN" "$RESET" "$hint"
    IFS= read -r answer || answer=""
    answer="${answer:-$default}"
    if [[ -n "$validator" ]] && ! "$validator" "$answer"; then
      fail "$VALIDATION_ERR"
      continue
    fi
    PROMPT_RESULT="$answer"
    return 0
  done
}

confirm() {
  local title="$1" helper="$2" default="${3:-y}"
  local default_idx=1
  [[ "$default" == "n" ]] && default_idx=2
  menu_select "$title" "$helper" "$default_idx" "Yes" "No"
  [[ "$MENU_RESULT" == "Yes" ]]
}

# ── validators ───────────────────────────────────────────────────────────────
validate_users() {
  local v="${1:-}"
  v="${v// /}"
  if [[ -z "$v" ]]; then
    VALIDATION_ERR="At least one number is required (E.164, e.g. 917385166726)."
    return 1
  fi
  if [[ ! "$v" =~ ^[0-9,]+$ ]]; then
    VALIDATION_ERR="Use digits and commas only (e.g. 917385166726, 14155551212)."
    return 1
  fi
  PROMPT_RESULT="$v"
  return 0
}

validate_port() {
  local v="${1:-}"
  if [[ ! "$v" =~ ^[0-9]+$ ]] || (( v < 1 || v > 65535 )); then
    VALIDATION_ERR="Port must be a number between 1 and 65535."
    return 1
  fi
  return 0
}

validate_time_hhmm() {
  local v="${1:-}"
  if [[ ! "$v" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
    VALIDATION_ERR="Use 24-hour HH:MM format, e.g. 04:00 or 23:30."
    return 1
  fi
  return 0
}

validate_path() {
  local v="${1:-}"
  if [[ -z "$v" ]]; then
    VALIDATION_ERR="A path is required."
    return 1
  fi
  return 0
}

validate_nonempty() {
  local v="${1:-}"
  if [[ -z "$v" ]]; then
    VALIDATION_ERR="Value is required."
    return 1
  fi
  return 0
}

# ── helpers ──────────────────────────────────────────────────────────────────
port_in_use() {
  local port="$1"
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1
  else
    (echo > "/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
  fi
}

find_free_port() {
  local p="${1:-3010}"
  local guard=0
  while port_in_use "$p"; do
    p=$((p + 1))
    guard=$((guard + 1))
    (( guard > 200 )) && break
  done
  printf '%s\n' "$p"
}

detect_cli_path() {
  local name="$1"
  local p
  p="$(command -v "$name" 2>/dev/null || true)"
  if [[ -n "$p" && -x "$p" ]]; then printf '%s\n' "$p"; return 0; fi
  for candidate in \
    "$HOME/.local/bin/$name" \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name" \
    "/usr/bin/$name"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

detect_package_version() {
  if [[ -n "${AGENT_PACKAGE_VERSION:-}" ]]; then
    printf '%s\n' "$AGENT_PACKAGE_VERSION"
    return 0
  fi
  if [[ -f "$INSTALL_DIR/pyproject.toml" ]]; then
    sed -n 's/^version = "\(.*\)"/\1/p' "$INSTALL_DIR/pyproject.toml" | head -n 1
  fi
}

load_existing_env() {
  [[ -f "$INSTALL_DIR/.env" ]] || return 0
  local k v
  while IFS='=' read -r k v || [[ -n "$k" ]]; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    # only seed if not already set in env
    if [[ -z "${!k:-}" ]]; then
      export "$k=$v"
    fi
  done < "$INSTALL_DIR/.env"
}

# ── long-running step runner with spinner ────────────────────────────────────
run_step() {
  local msg="$1"; shift
  local logfile
  logfile="$(mktemp)"

  if [[ $INTERACTIVE -eq 0 ]]; then
    printf '  • %s ... ' "$msg"
    if "$@" >"$logfile" 2>&1; then
      printf 'ok\n'
      rm -f "$logfile"
      return 0
    fi
    printf 'failed\n'
    sed 's/^/    /' "$logfile" >&2
    rm -f "$logfile"
    return 1
  fi

  ( "$@" ) >"$logfile" 2>&1 &
  local pid=$!
  local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
  local i=0
  printf '%s' "$HIDE_CURSOR"
  while kill -0 "$pid" 2>/dev/null; do
    printf '\r  %s%s%s %s' "$GREEN" "${frames[$((i % 10))]}" "$RESET" "$msg"
    sleep 0.08
    i=$((i + 1))
  done
  set +e
  wait "$pid"
  local rc=$?
  set -e
  printf '\r\033[2K'
  printf '%s' "$SHOW_CURSOR"
  if [[ $rc -eq 0 ]]; then
    ok "$msg"
  else
    fail "$msg"
    echo
    note "Last output:"
    tail -n 20 "$logfile" | sed 's/^/    /'
  fi
  rm -f "$logfile"
  return $rc
}

# ── install ops ──────────────────────────────────────────────────────────────
ensure_uv() {
  if command -v uv >/dev/null 2>&1; then return 0; fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
}

clone_or_update_repo() {
  # Skip cloning when the runtime is already populated (e.g. when the
  # python-package wrapper has copied bundled files into INSTALL_DIR), or
  # when the caller explicitly asks to skip.
  if [[ "${SKIP_CLONE:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -f "$INSTALL_DIR/bridge/bridge.js" ]]; then
    return 0
  fi
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
}

install_python_deps() {
  export PATH="$HOME/.local/bin:$PATH"
  uv venv --clear "$INSTALL_DIR/.venv" --python "$PYTHON_BIN"
  uv pip install --python "$INSTALL_DIR/.venv/bin/python" \
    -r "$INSTALL_DIR/requirements.txt"
}

install_node_deps() {
  ( cd "$INSTALL_DIR/bridge" && npm install )
}

write_env_file() {
  cat > "$INSTALL_DIR/.env" <<EOF
AGENT_WHATSAPP_HOME=$INSTALL_DIR
SERVICE_NAME=$SERVICE_NAME
AGENT_BACKEND=$BACKEND
AGENT_COMMAND=$AGENT_COMMAND
AGENT_MODEL=$MODEL
AGENT_MEMORY_DIR=$MEMORY_DIR
AGENT_MEMORY_ENABLED=$MEMORY_ENABLED
AGENT_MEMORY_ROLLOVER_TIME=$MEMORY_ROLLOVER_TIME
AGENT_PACKAGE_VERSION=$PACKAGE_VERSION
AGENT_ROOT=$ROOT
WHATSAPP_MODE=$MODE
WHATSAPP_ALLOWED_USERS=$ALLOWED_USERS
WHATSAPP_REPLY_PREFIX=
WHATSAPP_PORT=$PORT
CW_LOG_LEVEL=INFO
EOF
  chmod 600 "$INSTALL_DIR/.env"
}

install_systemd_unit() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — skipping user service install (not Linux?)."
    return 0
  fi
  mkdir -p "$HOME/.config/systemd/user"
  sed "s#__INSTALL_DIR__#$INSTALL_DIR#g" \
    "$INSTALL_DIR/systemd/agent-whatsapp.service" \
    > "$HOME/.config/systemd/user/$SERVICE_NAME.service"
  systemctl --user daemon-reload
  systemctl --user enable "$SERVICE_NAME.service"
}

# ── advanced sub-menu ────────────────────────────────────────────────────────
advanced_menu() {
  while true; do
    local model_show="${MODEL:-CLI default}"
    local labels=(
      "Working root        ${DIM}·${RESET} $ROOT"
      "Bridge port         ${DIM}·${RESET} $PORT"
      "Model               ${DIM}·${RESET} $model_show"
      "CLI command         ${DIM}·${RESET} $AGENT_COMMAND"
      "Memory dir          ${DIM}·${RESET} $MEMORY_DIR"
      "Memory rollover     ${DIM}·${RESET} $MEMORY_ROLLOVER_TIME"
      "Done"
    )
    menu_select "Advanced settings" \
      "Pick a value to override; choose Done when finished." \
      7 "${labels[@]}"
    case "$MENU_INDEX" in
      0) prompt_text "Working root" \
           "Default working directory for new chats." \
           "$ROOT" validate_path && ROOT="$PROMPT_RESULT" ;;
      1) prompt_text "Bridge port" \
           "Local HTTP port for the WhatsApp bridge." \
           "$PORT" validate_port && PORT="$PROMPT_RESULT" ;;
      2) prompt_text "Model" \
           "Leave blank to use the CLI default." \
           "$MODEL" && MODEL="$PROMPT_RESULT" ;;
      3) prompt_text "CLI command" \
           "Path to the codex / claude binary." \
           "$AGENT_COMMAND" validate_path && AGENT_COMMAND="$PROMPT_RESULT" ;;
      4) prompt_text "Memory dir" \
           "Long-term memory root. Each WhatsApp chat gets its own folder." \
           "$MEMORY_DIR" validate_path && MEMORY_DIR="$PROMPT_RESULT" ;;
      5) prompt_text "Memory rollover time" \
           "Daily local time to update memories and preload the next session summary." \
           "$MEMORY_ROLLOVER_TIME" validate_time_hhmm && MEMORY_ROLLOVER_TIME="$PROMPT_RESULT" ;;
      6) return ;;
    esac
  done
}

# ── summary ──────────────────────────────────────────────────────────────────
print_summary() {
  local model_show="${MODEL:-CLI default}"
  local port_show="$PORT"
  [[ "$PORT_AUTO" == "1" ]] && port_show="$PORT  ${DIM}(auto)${RESET}"

  printf '\n  %s┌─ Review %s%s\n' "$GREEN" "──────────────────────────────────────" "$RESET"
  printf '  %s│%s  %-16s %s%s%s\n' "$GREEN" "$RESET" "Backend"        "$BOLD" "$BACKEND" "$RESET"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "CLI command"   "$AGENT_COMMAND"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Mode"          "$MODE"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Allowed users" "$ALLOWED_USERS"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Working root"  "$ROOT"
  printf '  %s│%s  %-16s %b\n' "$GREEN" "$RESET" "Bridge port"   "$port_show"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Model"         "$model_show"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Memory dir"    "$MEMORY_DIR"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Memory time"   "$MEMORY_ROLLOVER_TIME"
  printf '  %s│%s  %-16s %s\n' "$GREEN" "$RESET" "Install dir"   "$INSTALL_DIR"
  printf '  %s└──────────────────────────────────────────────%s\n' "$GREEN" "$RESET"
}

# ── main ─────────────────────────────────────────────────────────────────────
main() {
  banner

  if [[ "$RECONFIGURE" == "1" || "$NON_INTERACTIVE" == "1" ]]; then
    load_existing_env
  fi

  # ── detect ────────────────────────────────────────────────────────────────
  section "Detecting installed CLIs"

  local claude_path codex_path
  claude_path="$(detect_cli_path claude || true)"
  codex_path="$(detect_cli_path codex || true)"

  if [[ -n "$claude_path" ]]; then ok "claude    ${DIM}$claude_path${RESET}"
  else                              note "claude    ${DIM}not found${RESET}"; fi
  if [[ -n "$codex_path" ]];  then ok "codex     ${DIM}$codex_path${RESET}"
  else                              note "codex     ${DIM}not found${RESET}"; fi

  # ── backend ───────────────────────────────────────────────────────────────
  BACKEND="${AGENT_BACKEND:-}"
  if [[ -z "$BACKEND" ]]; then
    if [[ -n "$claude_path" && -n "$codex_path" ]]; then
      menu_select "Which CLI should answer messages?" \
        "Both detected. Pick one — you can change later with --reconfigure." \
        1 "claude" "codex"
      BACKEND="$MENU_RESULT"
    elif [[ -n "$claude_path" ]]; then
      BACKEND="claude"
      ok "Using ${BOLD}claude${RESET}"
    elif [[ -n "$codex_path" ]]; then
      BACKEND="codex"
      ok "Using ${BOLD}codex${RESET}"
    else
      fail "Neither claude nor codex was found on PATH."
      note "Install one first, then re-run this script."
      note "  claude:  https://docs.anthropic.com/claude/cli"
      note "  codex:   https://github.com/openai/codex"
      exit 1
    fi
  fi

  # default agent command from detection / env
  if [[ -n "${AGENT_COMMAND:-}" ]]; then
    AGENT_COMMAND="$AGENT_COMMAND"
  elif [[ "$BACKEND" == "claude" && -n "$claude_path" ]]; then
    AGENT_COMMAND="$claude_path"
  elif [[ "$BACKEND" == "codex"  && -n "$codex_path"  ]]; then
    AGENT_COMMAND="$codex_path"
  else
    AGENT_COMMAND="$BACKEND"
  fi

  # ── mode ──────────────────────────────────────────────────────────────────
  MODE="${WHATSAPP_MODE:-bot}"
  if [[ $INTERACTIVE -eq 1 ]]; then
    local mode_default=1
    [[ "$MODE" == "self-chat" ]] && mode_default=2
    menu_select "WhatsApp mode" \
      "How will messages reach the agent?" \
      $mode_default \
      "bot        (a separate WhatsApp account DMs the agent — recommended)" \
      "self-chat  (you message yourself from your own WhatsApp account)"
    # strip the trailing description back off
    MODE="${MENU_RESULT%% *}"
  fi

  # ── allowed users ─────────────────────────────────────────────────────────
  ALLOWED_USERS="${WHATSAPP_ALLOWED_USERS:-}"
  prompt_text "Allowed numbers" \
    "Comma-separated, full international format. e.g. 917385166726, 14155551212" \
    "$ALLOWED_USERS" validate_users
  ALLOWED_USERS="$PROMPT_RESULT"

  # ── defaults for the rest ─────────────────────────────────────────────────
  ROOT="${AGENT_ROOT:-$HOME}"
  MODEL="${AGENT_MODEL:-}"
  MEMORY_DIR="${AGENT_MEMORY_DIR:-$INSTALL_DIR/memory}"
  MEMORY_ENABLED="${AGENT_MEMORY_ENABLED:-1}"
  MEMORY_ROLLOVER_TIME="${AGENT_MEMORY_ROLLOVER_TIME:-04:00}"
  PACKAGE_VERSION="${AGENT_PACKAGE_VERSION:-}"

  PORT_AUTO=0
  if [[ -n "${WHATSAPP_PORT:-}" ]]; then
    PORT="$WHATSAPP_PORT"
  else
    PORT="$(find_free_port 3010)"
    PORT_AUTO=1
    if [[ "$PORT" != "3010" ]]; then
      note "Port 3010 was busy → using $PORT."
    fi
  fi

  # ── advanced ──────────────────────────────────────────────────────────────
  if [[ $INTERACTIVE -eq 1 ]]; then
    if confirm "Advanced settings?" \
        "Override working root, port, model, or CLI path. Most users say No." \
        "n"; then
      advanced_menu
    fi
  fi

  # ── summary ───────────────────────────────────────────────────────────────
  print_summary

  if [[ $INTERACTIVE -eq 1 ]]; then
    if ! confirm "Apply this setup?" "" "y"; then
      note "Re-edit any value below, then you'll see this screen again."
      advanced_menu
      print_summary
      if ! confirm "Apply this setup?" "" "y"; then
        fail "Aborted."
        exit 1
      fi
    fi
  fi

  # ── install ───────────────────────────────────────────────────────────────
  section "Installing"

  mkdir -p "$INSTALL_DIR"

  if [[ "$RECONFIGURE" != "1" ]]; then
    run_step "fetching repo into $INSTALL_DIR" clone_or_update_repo
  else
    note "Reconfigure mode — skipping repo fetch."
  fi
  run_step "ensuring uv is available"        ensure_uv
  run_step "creating python venv + deps"     install_python_deps
  run_step "installing node bridge deps"     install_node_deps
  if [[ -z "$PACKAGE_VERSION" ]]; then
    PACKAGE_VERSION="$(detect_package_version || true)"
  fi
  run_step "writing $INSTALL_DIR/.env"       write_env_file
  run_step "installing systemd user service" install_systemd_unit

  # ── pair (optional) ───────────────────────────────────────────────────────
  local paired=0
  if [[ $INTERACTIVE -eq 1 ]]; then
    if confirm "Pair WhatsApp now?" \
        "Prints a QR code in this terminal. Scan with WhatsApp → Linked devices." \
        "y"; then
      "$INSTALL_DIR/scripts/pair.sh"
      if command -v systemctl >/dev/null 2>&1; then
        systemctl --user restart "$SERVICE_NAME.service" || true
      fi
      paired=1
    fi
  fi

  print_finished "$paired"
}

# ── final cheat sheet ────────────────────────────────────────────────────────
print_finished() {
  local paired="$1"
  local has_systemctl=0
  command -v systemctl >/dev/null 2>&1 && has_systemctl=1

  printf '\n  %s──%s %s%s%s\n' \
    "$GREEN" "$RESET" "$BOLD" "Congratulations — your WhatsApp coding agent is installed." "$RESET"
  ok "Installed into ${BOLD}$INSTALL_DIR${RESET}"
  if [[ "$paired" == "1" ]]; then
    if [[ $has_systemctl -eq 1 ]]; then
      ok "WhatsApp paired and service restarted."
    else
      ok "WhatsApp paired."
    fi
    note "Send a message from an allowed number to test it."
  else
    note "Pair when you're ready: ${BOLD}bash $INSTALL_DIR/scripts/pair.sh${RESET}"
  fi

  printf '\n  %s%s%s\n' "$BOLD" "Cheatsheet" "$RESET"
  printf '  %s%s%s\n\n' "$DIM" "Re-run anytime — these are safe to bookmark." "$RESET"

  printf '  %sChange CLI / model / port / root / numbers%s\n' "$BOLD" "$RESET"
  printf '    bash %s/scripts/install.sh --reconfigure\n\n' "$INSTALL_DIR"

  printf '  %sAdd or remove allowed numbers%s\n' "$BOLD" "$RESET"
  printf '    bash %s/scripts/install.sh --reconfigure\n' "$INSTALL_DIR"
  printf '    %sor edit WHATSAPP_ALLOWED_USERS in %s/.env, then restart the service%s\n\n' \
    "$DIM" "$INSTALL_DIR" "$RESET"

  printf '  %sRe-pair WhatsApp%s  %s(switched phone, lost session)%s\n' "$BOLD" "$RESET" "$DIM" "$RESET"
  printf '    bash %s/scripts/pair.sh\n\n' "$INSTALL_DIR"

  if [[ $has_systemctl -eq 1 ]]; then
    printf '  %sService control%s\n' "$BOLD" "$RESET"
    printf '    systemctl --user start    %s.service\n' "$SERVICE_NAME"
    printf '    systemctl --user stop     %s.service\n' "$SERVICE_NAME"
    printf '    systemctl --user restart  %s.service\n' "$SERVICE_NAME"
    printf '    systemctl --user status   %s.service --no-pager\n\n' "$SERVICE_NAME"

    printf '  %sLive logs%s\n' "$BOLD" "$RESET"
    printf '    journalctl --user -u %s.service -f\n\n' "$SERVICE_NAME"
  else
    printf '  %sRun the gateway%s  %s(no systemd on this OS — run manually)%s\n' \
      "$BOLD" "$RESET" "$DIM" "$RESET"
    printf '    source %s/.venv/bin/activate && python %s/server/gateway.py\n\n' \
      "$INSTALL_DIR" "$INSTALL_DIR"
  fi

  printf '  %sEdit raw config%s\n' "$BOLD" "$RESET"
  printf '    $EDITOR %s/.env\n\n' "$INSTALL_DIR"

  printf '  %sIn-chat commands%s  %s(send via WhatsApp)%s\n' "$BOLD" "$RESET" "$DIM" "$RESET"
  printf '    /status   /new   /reset   /resume   /title <name>\n'
  printf '    /root /abs/path   /model <name>   /compact   /memory   /help\n\n'
}

main "$@"
