"""Tests for StateMachine.reset() and NetworkManager.teardown()."""
from __future__ import annotations

from unittest.mock import call, patch

import pytest

from modbus_sim.network_manager import NetworkManager
from modbus_sim.state_machine import RUNNING, SETUP

from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file

PORT_A = 5045
PORT_B = 5046


def _yaml(port, device_id="dev1"):
    return make_config_yaml([{"id": device_id, "ip": "127.0.0.1", "port": port, "unit_id": 1}])


# ------------------------------------------------------------------ state machine


def test_state_machine_reset(tmp_path):
    """reset() stops simulation, clears disk + memory, and returns to SETUP."""
    engine, client = make_engine(tmp_path)

    upload_file(client, "/api/setup/config", _yaml(PORT_A), "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
    assert client.post("/api/setup/start").status_code == 200
    assert engine.state == RUNNING

    resp = client.post("/api/reset")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # State is back to SETUP.
    assert engine.state == SETUP
    assert client.get("/api/state").get_json()["state"] == SETUP

    # Disk files are gone.
    assert not (tmp_path / ".config_locked").exists()
    assert not (tmp_path / "sim_config.yaml").exists()
    assert not (tmp_path / "devices").exists()

    # In-memory state is cleared.
    assert engine.config is None
    assert engine.config_locked is False
    assert engine.started_at is None
    assert engine._signals == {}
    assert engine._regmaps == {}
    assert engine._loop is None
    assert engine._network is None

    # Setup endpoints available; runtime endpoints blocked.
    assert client.get("/api/setup/status").status_code == 200
    assert client.get("/api/devices").status_code == 409


def test_reset_then_new_config_cycle(tmp_path):
    """After reset, a complete new config + signals + start cycle succeeds."""
    engine, client = make_engine(tmp_path)

    # First cycle.
    upload_file(client, "/api/setup/config", _yaml(PORT_B, "dev1"), "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
    assert client.post("/api/setup/start").status_code == 200

    # Reset.
    assert client.post("/api/reset").status_code == 200

    # Second cycle with a different device id and port.
    upload_file(client, "/api/setup/config", _yaml(PORT_B + 100, "dev2"), "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev2", SAMPLE_SIGNALS, "dev2.csv")
    resp = client.post("/api/setup/start")
    assert resp.status_code == 200
    assert resp.get_json()["state"] == RUNNING
    assert engine.state == RUNNING
    assert engine.config.devices[0].id == "dev2"

    engine.stop()


def test_reset_requires_running_state(tmp_path):
    """reset() returns 409 when called outside RUNNING state."""
    _, client = make_engine(tmp_path)
    resp = client.post("/api/reset")
    assert resp.status_code == 409
    assert resp.get_json()["current_state"] == SETUP


# ------------------------------------------------------------------ network manager


def _nm_vlan():
    """NetworkManager with pre-populated state as if setup() ran with two VLAN devices."""
    yaml = make_config_yaml(
        [
            {"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1, "vlan": 100},
            {"id": "b", "ip": "10.4.2.20", "port": 502, "unit_id": 2, "vlan": 200},
        ],
        traffic_interface="eth1",
        vlan_mode="enabled",
    )
    from modbus_sim.config_loader import load_and_validate
    cfg, errs = load_and_validate(yaml)
    assert not errs
    nm = NetworkManager(cfg)
    nm._vlan_interfaces = ["eth1.100", "eth1.200"]
    nm._assigned_ips = [
        {"ip": "10.4.1.10", "interface": "eth1.100", "prefix_length": 24},
        {"ip": "10.4.2.20", "interface": "eth1.200", "prefix_length": 24},
    ]
    return nm


def test_teardown_issues_correct_ip_commands():
    nm = _nm_vlan()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        nm.teardown()

    calls = mock_run.call_args_list
    # IP deletions in reverse order.
    assert call(["ip", "addr", "del", "10.4.2.20/24", "dev", "eth1.200"],
                capture_output=True, text=True) in calls
    assert call(["ip", "addr", "del", "10.4.1.10/24", "dev", "eth1.100"],
                capture_output=True, text=True) in calls
    # VLAN deletions in reverse order.
    assert call(["ip", "link", "set", "eth1.200", "down"], capture_output=True, text=True) in calls
    assert call(["ip", "link", "delete", "eth1.200"], capture_output=True, text=True) in calls
    assert call(["ip", "link", "set", "eth1.100", "down"], capture_output=True, text=True) in calls
    assert call(["ip", "link", "delete", "eth1.100"], capture_output=True, text=True) in calls


def test_teardown_clears_internal_state():
    nm = _nm_vlan()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        nm.teardown()
    assert nm._assigned_ips == []
    assert nm._vlan_interfaces == []


def test_teardown_continues_on_subprocess_failure():
    """Teardown must not raise even if every subprocess call fails."""
    nm = _nm_vlan()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = OSError("ip not found")
        nm.teardown()   # must not raise
    assert nm._assigned_ips == []
    assert nm._vlan_interfaces == []


def test_teardown_no_op_when_empty():
    """teardown() on a fresh NetworkManager calls no subprocess commands."""
    from modbus_sim.config_loader import load_and_validate
    cfg, _ = load_and_validate(
        make_config_yaml([{"id": "a", "ip": "10.0.0.1", "port": 502, "unit_id": 1}])
    )
    nm = NetworkManager(cfg)
    with patch("subprocess.run") as mock_run:
        nm.teardown()
    mock_run.assert_not_called()
