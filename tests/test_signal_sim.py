"""Per-signal simulation editing (the runtime-grid low/high + motion path).

Covers POST /api/devices/<id>/signals/<name>/sim: it applies live (next read),
persists to the device CSV, validates, and the 'step' staircase produces the
expected discrete values.
"""
import io

import pytest

from modbus_sim import register_map as rm_mod
from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file

SIGNAL = "Grid Voltage L1"   # holding/uint16 in SAMPLE_SIGNALS
_PORT = [5070]               # unique port per test (incremented in the fixture)


@pytest.fixture
def fixed_clock(monkeypatch):
    holder = {"t": 0.0}
    monkeypatch.setattr(rm_mod, "clock", lambda: holder["t"])
    return holder


@pytest.fixture
def running(tmp_path):
    _PORT[0] += 1
    engine, client = make_engine(tmp_path)
    yaml = make_config_yaml([{"id": "dev1", "ip": "127.0.0.1", "port": _PORT[0], "unit_id": 1}])
    upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
    assert client.post("/api/setup/start").status_code == 200
    assert client.post("/api/simulation", json={"enabled": True}).status_code == 200
    try:
        yield engine, client
    finally:
        engine.stop()


def _val(client):
    return client.get("/api/devices/dev1/values").get_json()[SIGNAL]


def test_step_motion_applies_live_and_persists(running, tmp_path, fixed_clock):
    engine, client = running

    r = client.post(f"/api/devices/dev1/signals/{SIGNAL}/sim",
                    json={"sim_mode": "step", "sim_min": 200, "sim_max": 300,
                          "sim_period": 2, "sim_step": 5})
    assert r.status_code == 200
    spec = r.get_json()["signal"]
    assert spec["sim_mode"] == "step" and spec["sim_step"] == 5

    # Live, deterministic staircase: 200 -> 205 -> ... every 2s.
    fixed_clock["t"] = 0.0
    assert _val(client) == 200
    fixed_clock["t"] = 2.0
    assert _val(client) == 205
    fixed_clock["t"] = 40.0
    assert _val(client) == 300

    # Persisted to the device CSV so it survives restart/restore.
    csv = (tmp_path / "devices" / "dev1.csv").read_text()
    assert "sim_step" in csv.splitlines()[0]
    row = [ln for ln in csv.splitlines() if ln.startswith(SIGNAL)][0]
    assert "step" in row and ",200," in row and ",300," in row


def test_setting_low_high_only_uses_default_motion(running, fixed_clock):
    engine, client = running
    # Just a range, no motion -> inherits the project default (oscillate) and moves.
    r = client.post(f"/api/devices/dev1/signals/{SIGNAL}/sim",
                    json={"sim_min": 3900, "sim_max": 4100})
    assert r.status_code == 200
    fixed_clock["t"] = 1.0
    v1 = _val(client)
    fixed_clock["t"] = 4.0
    v2 = _val(client)
    assert 3900 <= v1 <= 4100 and 3900 <= v2 <= 4100
    assert v1 != v2   # it is moving


def test_clearing_range_makes_it_static(running, fixed_clock):
    engine, client = running
    client.post(f"/api/devices/dev1/signals/{SIGNAL}/sim",
                json={"sim_min": 200, "sim_max": 300})
    # Clear both -> opt-out: reads return the stored default again.
    r = client.post(f"/api/devices/dev1/signals/{SIGNAL}/sim",
                    json={"sim_min": "", "sim_max": ""})
    assert r.status_code == 200
    fixed_clock["t"] = 5.0
    assert _val(client) == 4000   # SAMPLE_SIGNALS default_value for this signal


def test_invalid_mode_is_rejected(running):
    engine, client = running
    r = client.post(f"/api/devices/dev1/signals/{SIGNAL}/sim", json={"sim_mode": "bogus"})
    assert r.status_code == 400


def test_unknown_signal_is_404(running):
    engine, client = running
    r = client.post("/api/devices/dev1/signals/Nope/sim", json={"sim_min": 1, "sim_max": 2})
    assert r.status_code == 404
