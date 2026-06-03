#!/usr/bin/env bash
# Run the pytest suite. Pass any pytest args through, e.g. -k test_register_map -v.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/python ]]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
    exit 1
fi

exec .venv/bin/python -m pytest "$@"
