"""Start-failure handling: a missing network adapter must produce a clear, recoverable
error instead of silently crashing the engine process.

Regression for the case where the USB-C NIC is not plugged in: the engine must stay in
SETUP, surface the reason via setup_status, and (on --restore) NOT call sys.exit.
"""
from __future__ import annotations

import io

from modbus_sim import main as engine_main
from modbus_sim.api_server import create_app
from modbus_sim.state_machine import SETUP, StateMachine

from .conftest import SAMPLE_SIGNALS, make_config_yaml

# A NIC name that does not exist (mimics the adapter being unplugged).
MISSING_NIC = "bogus_nic_xyz0"
PORT = 5599


def _net_engine(tmp_path):
    """Engine + client with network management ON (so setup() runs against the NIC)."""
    engine = StateMachine(tmp_path, manage_network=True)
    app = create_app(engine, headless=False)
    app.testing = True
    return engine, app.test_client()


def _upload(client, url, text, fn):
    return client.post(
        url, data={"file": (io.BytesIO(text.encode("utf-8")), fn)},
        content_type="multipart/form-data",
    )


def _load(client):
    yaml = make_config_yaml(
        [{"id": "dev1", "ip": "127.0.0.1", "port": PORT, "unit_id": 1}],
        traffic_interface=MISSING_NIC, vlan_mode="disabled",
    )
    _upload(client, "/api/setup/config", yaml, "sim_config.yaml")
    _upload(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")


def test_start_missing_nic_returns_clear_error_and_stays_in_setup(tmp_path):
    engine, client = _net_engine(tmp_path)
    _load(client)

    resp = client.post("/api/setup/start")
    assert resp.status_code == 500
    assert MISSING_NIC in resp.get_json()["error"]

    # Engine is still usable in SETUP, and the reason is surfaced for the UI.
    assert engine.state == SETUP
    status = client.get("/api/setup/status").get_json()
    assert status["can_start"] is True
    assert status["start_error"] and MISSING_NIC in status["start_error"]


def test_restore_with_missing_nic_degrades_to_setup_without_exiting(tmp_path):
    # First, persist a locked project (config + signals) to disk.
    engine, client = _net_engine(tmp_path)
    _load(client)

    # A fresh engine restoring from that dir must not sys.exit when start fails.
    fresh = StateMachine(tmp_path, manage_network=True)
    restored = engine_main._restore_from_disk(fresh)  # would raise SystemExit on regression

    assert restored is False
    assert fresh.state == SETUP
    assert fresh.start_error and MISSING_NIC in fresh.start_error
