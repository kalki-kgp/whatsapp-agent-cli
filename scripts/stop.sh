#!/usr/bin/env bash
set -euo pipefail
systemctl --user stop "${SERVICE_NAME:-agent-whatsapp}.service"
