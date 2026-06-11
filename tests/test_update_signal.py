"""Per-variable edit endpoint: POST /api/devices/<id>/signals/<name>/update.

The runtime grid's "click a variable -> modal" Save path. The client sends only the
one edited signal; the server merges it, re-validates the whole device, hot-reloads,
and persists the CSV. Supports rename and full definition + simulation edits.
"""
import pytest

from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file

SIGNAL = "Grid Voltage L1"   # holding/uint16 @1000 in SAMPLE_SIGNALS
_PORT = [5080]


@pytest.fixture
def running(tmp_path):
    _PORT[0] += 1
    engine, client = make_engine(tmp_path)
    yaml = make_config_yaml([{"id": "dev1", "ip": "127.0.0.1", "port": _PORT[0], "unit_id": 1}])
    upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
    assert client.post("/api/setup/start").status_code == 200
    try:
        yield engine, client, tmp_path
    finally:
        engine.stop()


def _post(client, name, body):
    return client.post(f"/api/devices/dev1/signals/{name}/update", json=body)


def test_edit_definition_and_simulation_persists(running):
    engine, client, tmp_path = running
    body = {
        "name": SIGNAL, "register_type": "holding", "address": 1000,
        "data_type": "uint16", "bit_index": None, "word_order": "big_endian",
        "scale": 0.1, "unit": "kV", "section": "Grid", "description": "edited",
        "default_value": 4000, "writable": True,
        "sim_mode": "step", "sim_min": 200, "sim_max": 300, "sim_period": 2, "sim_step": 5,
    }
    r = _post(client, SIGNAL, body)
    assert r.status_code == 200 and r.get_json()["signal_count"] == 8

    # Reflected in the live signal list...
    sig = [s for s in client.get("/api/devices/dev1/signals").get_json() if s["name"] == SIGNAL][0]
    assert sig["unit"] == "kV" and sig["writable"] is True
    assert sig["sim_mode"] == "step" and sig["sim_step"] == 5
    # ...and persisted to the device CSV.
    csv = (tmp_path / "devices" / "dev1.csv").read_text()
    row = [ln for ln in csv.splitlines() if ln.startswith(SIGNAL + ",")][0]
    assert "kV" in row and "step" in row


def test_rename_changes_the_key(running):
    engine, client, _ = running
    r = _post(client, SIGNAL, {"name": "Grid V L1 (renamed)"})
    assert r.status_code == 200
    names = {s["name"] for s in client.get("/api/devices/dev1/signals").get_json()}
    assert "Grid V L1 (renamed)" in names and SIGNAL not in names


def test_invalid_edit_is_rejected_and_live_unchanged(running):
    engine, client, _ = running
    # address collision with another signal (Active Power @1004 spans 1004-1005)
    r = _post(client, SIGNAL, {"address": 1004, "data_type": "int32", "word_order": "big_endian"})
    assert r.status_code == 400
    # original signal still present and unchanged
    sig = [s for s in client.get("/api/devices/dev1/signals").get_json() if s["name"] == SIGNAL][0]
    assert sig["address"] == 1000


def test_unknown_signal_is_404(running):
    engine, client, _ = running
    assert _post(client, "Nope", {"unit": "x"}).status_code == 404
