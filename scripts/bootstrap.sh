#!/usr/bin/env bash
set -euo pipefail

# One-command installer for users on PEP 668 systems where system pip is blocked.
# It installs uv into the user's account when needed, installs the PyPI CLI
# persistently as a uv tool, then runs it.

PACKAGE="${WHATSAPP_AGENT_PACKAGE:-whatsapp-agent-cli}"
VERSION="${WHATSAPP_AGENT_CLI_VERSION:-}"
COMMAND="${WHATSAPP_AGENT_COMMAND_NAME:-whatsapp-agent}"

if [[ $# -eq 0 ]]; then
  set -- install
fi

if [[ -n "$VERSION" ]]; then
  PACKAGE_SPEC="${PACKAGE}==${VERSION}"
else
  PACKAGE_SPEC="$PACKAGE"
fi

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
  GREEN=$'\033[38;2;37;211;102m'
  YELLOW=$'\033[33m'
  RED=$'\033[31m'
  DIM=$'\033[2m'
  RESET=$'\033[0m'
else
  GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi

ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail() { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1" >&2; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }

add_user_bins_to_path() {
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    ok "uv found: $(command -v uv)"
    return 0
  fi

  note "uv not found; installing it into your user account."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    fail "curl or wget is required to install uv."
    note "Install curl first, then re-run this installer."
    exit 1
  fi

  add_user_bins_to_path
  if ! command -v uv >/dev/null 2>&1; then
    fail "uv was installed, but it is not on PATH yet."
    note "Run: export PATH=\"\$HOME/.local/bin:\$HOME/.cargo/bin:\$PATH\""
    exit 1
  fi
  ok "uv installed: $(command -v uv)"
}

check_node() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    fail "Node.js 18+ and npm are required for the WhatsApp bridge."
    if command -v apt-get >/dev/null 2>&1; then
      note "On Ubuntu/Debian, run: sudo apt update && sudo apt install -y nodejs npm"
    elif command -v dnf >/dev/null 2>&1; then
      note "On Fedora/RHEL, run: sudo dnf install -y nodejs npm"
    elif command -v brew >/dev/null 2>&1; then
      note "On macOS, run: brew install node"
    fi
    exit 1
  fi

  local major
  major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf '0')"
  if [[ ! "$major" =~ ^[0-9]+$ ]] || (( major < 18 )); then
    fail "Node.js 18+ is required; found $(node --version 2>/dev/null || printf unknown)."
    exit 1
  fi
  ok "node found: $(node --version)"
  ok "npm found: $(npm --version)"
}

restore_tty_stdin() {
  if [[ ! -t 0 && -r /dev/tty ]]; then
    exec < /dev/tty
  fi
}

install_cli_tool() {
  note "Installing $PACKAGE_SPEC as a user CLI tool."
  uv tool install --upgrade "$PACKAGE_SPEC"
  add_user_bins_to_path

  if ! command -v "$COMMAND" >/dev/null 2>&1; then
    fail "$COMMAND was installed, but it is not on PATH yet."
    note "Run: export PATH=\"\$HOME/.local/bin:\$PATH\""
    note "Then retry: $COMMAND $*"
    exit 1
  fi
  ok "$COMMAND installed: $(command -v "$COMMAND")"
}

main() {
  add_user_bins_to_path
  printf '\n  whatsapp-agent bootstrap\n\n'
  install_uv
  check_node
  install_cli_tool "$@"
  restore_tty_stdin
  printf '\n'
  note "Running: $COMMAND $*"
  exec "$COMMAND" "$@"
}

main "$@"
