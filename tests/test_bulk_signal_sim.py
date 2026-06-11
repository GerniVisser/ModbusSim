"""Cross-device fuzzy search + bulk apply (the "All Devices" feature).

Covers GET /api/signals/search and POST /api/signals/{sim,value}/bulk: name-only
fuzzy matching across every device, bulk sim apply persisting to each affected
device's CSV, and bulk static-value writes that skip type-mismatched signals.
"""
import pytest

from modbus_sim import register_map as rm_mod
from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file

_PORT = [5300]   # unique base port per test (incremented in the fixture)


@pytest.fixture
def fixed_clock(monkeypatch):
    holder = {"t": 0.0}
    monkeypatch.setattr(rm_mod, "clock", lambda: holder["t"])
    return holder


@pytest.fixture
def running(tmp_path):
    """Two devices with the identical signal schema — the schema bulk apply targets."""
    _PORT[0] += 2
    engine, client = make_engine(tmp_path)
    yaml = make_config_yaml([
        {"id": "dev1", "name": "Meter 1", "ip": "127.0.0.1", "port": _PORT[0], "unit_id": 1},
        {"id": "dev2", "name": "Meter 2", "ip": "127.0.0.1", "port": _PORT[0] + 1, "unit_id": 1},
    ])
    upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
    upload_file(client, "/api/setup/signals/dev2", SAMPLE_SIGNALS, "dev2.csv")
    assert client.post("/api/setup/start").status_code == 200
    assert client.post("/api/simulation", json={"enabled": True}).status_code == 200
    try:
        yield engine, client
    finally:
        engine.stop()


def test_search_matches_name_across_devices(running):
    engine, client = running
    r = client.get("/api/signals/search?q=voltage")
    assert r.status_code == 200
    data = r.get_json()
    # "Grid Voltage L1", "DC Bus Voltage", "Input Voltage" on each of 2 devices = 6.
    assert data["total"] == 6
    assert data["truncated"] is False
    names = {m["name"] for m in data["matches"]}
    devs = {m["device_id"] for m in data["matches"]}
    assert names == {"Grid Voltage L1", "DC Bus Voltage", "Input Voltage"}
    assert devs == {"dev1", "dev2"}


def test_search_is_case_insensitive_and_ands_terms(running):
    engine, client = running
    # Two terms, both must appear in the name, any case.
    r = client.get("/api/signals/search?q=GRID%20voltage")
    matches = r.get_json()["matches"]
    assert {m["name"] for m in matches} == {"Grid Voltage L1"}
    assert len(matches) == 2   # one per device


def test_empty_query_matches_nothing(running):
    engine, client = running
    assert client.get("/api/signals/search?q=").get_json()["total"] == 0
    assert client.get("/api/signals/search?q=*").get_json()["total"] == 0


def test_bulk_sim_applies_to_all_matches_and_persists(running, tmp_path, fixed_clock):
    engine, client = running
    r = client.post("/api/signals/sim/bulk", json={
        "query": "active power",
        "sim": {"sim_mode": "step", "sim_min": 0, "sim_max": 100,
                "sim_period": 2, "sim_step": 5},
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied"] == 2 and body["devices"] == 2

    # Live on both devices: deterministic staircase from the shared clock.
    fixed_clock["t"] = 0.0
    assert client.get("/api/devices/dev1/values").get_json()["Active Power"] == 0
    assert client.get("/api/devices/dev2/values").get_json()["Active Power"] == 0
    fixed_clock["t"] = 2.0
    assert client.get("/api/devices/dev1/values").get_json()["Active Power"] == 5

    # Persisted to each affected device CSV.
    for dev in ("dev1", "dev2"):
        row = [ln for ln in (tmp_path / "devices" / f"{dev}.csv").read_text().splitlines()
               if ln.startswith("Active Power")][0]
        assert "step" in row


def test_bulk_sim_rejects_bad_fields_before_applying(running, tmp_path):
    engine, client = running
    r = client.post("/api/signals/sim/bulk",
                    json={"query": "voltage", "sim": {"sim_mode": "bogus"}})
    assert r.status_code == 400
    # Nothing was mutated.
    assert "bogus" not in (tmp_path / "devices" / "dev1.csv").read_text()


def test_bulk_sim_requires_query(running):
    engine, client = running
    assert client.post("/api/signals/sim/bulk", json={"sim": {}}).status_code == 400


def test_bulk_value_skips_type_mismatches(running):
    engine, client = running
    # "voltage" matches uint16 + float32 numerics only -> all accept 250.5? No:
    # uint16/int are integer-typed and reject a float. Use an int to land cleanly,
    # then assert a separate bool-inclusive query reports skips.
    r = client.post("/api/signals/value/bulk", json={"query": "voltage", "value": 250})
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied"] == 6 and body["skipped"] == 0
    assert client.get("/api/devices/dev1/values").get_json()["Input Voltage"] == 250

    # "Status" matches three bools (per device) -> an int 2 is rejected for bool,
    # so every match is skipped, nothing applied.
    r2 = client.post("/api/signals/value/bulk", json={"query": "status", "value": 2})
    b2 = r2.get_json()
    assert b2["applied"] == 0 and b2["skipped"] > 0


def test_bulk_value_requires_query_and_value(running):
    engine, client = running
    assert client.post("/api/signals/value/bulk", json={"query": "x"}).status_code == 400
    assert client.post("/api/signals/value/bulk", json={"value": 1}).status_code == 400
