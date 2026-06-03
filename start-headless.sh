#!/usr/bin/env bash
# Headless mode: REST API only (no Web UI), with VLAN/IP network management.
# Requires root and the USB-C NIC connected as traffic_interface in sim_config.yaml.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/python ]]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec sudo .venv/bin/python -m modbus_sim.main --headless --config ./project "$@"
