#!/usr/bin/env bash
# Dev mode: REST API + Web UI, loopback only — no VLAN setup, no root required.
# Use device IPs of 127.0.0.1 and ports >= 1024 (e.g. 5020) in sim_config.yaml.
# Pass --reset to clear a locked project directory and start fresh.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/python ]]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
    exit 1
fi

exec .venv/bin/python -m modbus_sim.main --no-network --config ./project --port 5000 "$@"
