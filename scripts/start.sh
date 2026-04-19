#!/usr/bin/env bash
set -euo pipefail
systemctl --user restart "${SERVICE_NAME:-agent-whatsapp}.service"
