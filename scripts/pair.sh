#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENT_WHATSAPP_HOME:-${INSTALL_DIR:-$HOME/.agent-whatsapp}}"
PAIR_ACTION="${WHATSAPP_PAIR_ACTION:-}"
ASSUME_YES=0

usage() {
  cat <<'EOF'
Usage: whatsapp-agent pair [--reuse | --reset] [--yes]

Options:
  --reuse        Use the existing WhatsApp credentials and verify they connect.
  --reset, --new Back up the existing credentials and show a fresh QR code.
  -y, --yes      Do not prompt when --reset is selected.
  -h, --help     Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reuse)
      PAIR_ACTION="reuse"
      ;;
    --reset|--new)
      PAIR_ACTION="reset"
      ;;
    -y|--yes)
      ASSUME_YES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -f "$INSTALL_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTALL_DIR/.env"
  set +a
fi

PORT="${WHATSAPP_PORT:-3010}"
MODE="${WHATSAPP_MODE:-bot}"
SESSION_DIR="${WHATSAPP_SESSION_DIR:-$INSTALL_DIR/whatsapp/session}"

session_exists() {
  [[ -f "$SESSION_DIR/creds.json" ]]
}

choose_pair_action() {
  printf '\nExisting WhatsApp credentials were found:\n  %s\n\n' "$SESSION_DIR"
  printf 'What do you want to do?\n'
  printf '  1) Use existing session\n'
  printf '  2) Pair again with a fresh QR code\n'
  printf '  3) Cancel\n\n'

  local answer
  while true; do
    read -r -p "Choose [1-3]: " answer
    case "$answer" in
      1|"use"|"reuse"|"existing") PAIR_ACTION="reuse"; return ;;
      2|"pair"|"reset"|"new") PAIR_ACTION="reset"; return ;;
      3|"cancel"|"no"|"n") PAIR_ACTION="cancel"; return ;;
      *) printf 'Please choose 1, 2, or 3.\n' ;;
    esac
  done
}

confirm_reset() {
  if [[ "$ASSUME_YES" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    printf 'Refusing to reset WhatsApp credentials without --yes in a non-interactive shell.\n' >&2
    exit 1
  fi
  local answer
  read -r -p "Back up the old session and pair again? [y/N] " answer
  answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
  case "$answer" in
    y|yes) return 0 ;;
    *) printf 'Pairing cancelled.\n'; exit 1 ;;
  esac
}

if session_exists; then
  if [[ -z "$PAIR_ACTION" ]]; then
    if [[ -t 0 ]]; then
      choose_pair_action
    else
      printf 'Existing WhatsApp credentials found at %s.\n' "$SESSION_DIR" >&2
      printf 'Use --reuse to verify them, or --reset --yes to pair again.\n' >&2
      exit 1
    fi
  fi

  case "$PAIR_ACTION" in
    reuse)
      printf 'Using existing WhatsApp session. No QR will be shown unless WhatsApp rejects the credentials.\n\n'
      ;;
    reset)
      confirm_reset
      backup_dir="${SESSION_DIR}.backup.$(date +%Y%m%d-%H%M%S)"
      mv "$SESSION_DIR" "$backup_dir"
      printf 'Backed up previous WhatsApp session to:\n  %s\n\n' "$backup_dir"
      ;;
    cancel)
      printf 'Pairing cancelled.\n'
      exit 1
      ;;
    *)
      printf 'Unknown pair action: %s\n' "$PAIR_ACTION" >&2
      exit 2
      ;;
  esac
fi

mkdir -p "$SESSION_DIR"
cd "$INSTALL_DIR/bridge"
node bridge.js --pair-only --port "$PORT" --session "$SESSION_DIR" --mode "$MODE"
