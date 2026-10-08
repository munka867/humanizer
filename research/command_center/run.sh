#!/usr/bin/env bash
# Start the command centre on 127.0.0.1:8765 (override with CC_PORT). Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m command_center.server "$@"
