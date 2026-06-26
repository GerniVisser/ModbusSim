"""Setup wizard + state-machine API tests (REQUIREMENTS.md sections 11, 18)."""
from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file


def _single_device_config(port):
    return make_config_yaml([
        {"id": "dev1", "ip": "127.0.0.1", "port": port, "unit_id": 1},
    ])


def test_initial_state_is_setup(tmp_path):
    _, client = make_engine(tmp_path)
    body = client.get("/api/state").get_json()
    assert body["state"] == "SETUP"
    assert body["config_locked"] is False


def test_health(tmp_path):
    _, client = make_engine(tmp_path)
    assert client.get("/api/health").get_json() == {"ok": True}


def test_runtime_endpoint_in_setup_returns_409(tmp_path):
    _, client = make_engine(tmp_path)
    resp = client.get("/api/devices")
    assert resp.status_code == 409
    assert resp.get_json()["current_state"] == "SETUP"


def test_invalid_config_returns_400_and_no_lock(tmp_path):
    _, client = make_engine(tmp_path)
    resp = upload_file(client, "/api/setup/config", "not: [valid", "sim_config.yaml")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    assert client.get("/api/state").get_json()["config_locked"] is False


def test_config_upload_locks_and_second_upload_409(tmp_path):
    _, client = make_engine(tmp_path)
    yaml = _single_device_config(5040)
    resp = upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    assert resp.status_code == 200
    assert resp.get_json()["locked"] is True
    assert client.get("/api/state").get_json()["config_locked"] is True
    # Second upload while locked -> 409
    resp2 = upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    assert resp2.status_code == 409


def test_invalid_signal_csv_returns_row_errors(tmp_path):
    _, client = make_engine(tmp_path)
    upload_file(client, "/api/setup/config", _single_device_config(5041), "sim_config.yaml")
    bad = "name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable\n" \
          "x,holding,0,uint64,,,1,,,,0,false\n"
    resp = upload_file(client, "/api/setup/signals/dev1", bad, "dev1.csv")
    assert resp.status_code == 400
    errs = resp.get_json()["errors"]
    assert errs[0]["row"] == 2 and errs[0]["column"] == "data_type"


def test_start_blocked_until_all_signals(tmp_path):
    _, client = make_engine(tmp_path)
    upload_file(client, "/api/setup/config", _single_device_config(5042), "sim_config.yaml")
    resp = client.post("/api/setup/start")
    assert resp.status_code == 409


def test_full_start_transition_and_setup_locked_out(tmp_path):
    engine, client = make_engine(tmp_path)
    try:
        upload_file(client, "/api/setup/config", _single_device_config(5043), "sim_config.yaml")
        upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
        status = client.get("/api/setup/status").get_json()
        assert status["can_start"] is True

        resp = client.post("/api/setup/start")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "RUNNING"
        assert client.get("/api/state").get_json()["state"] == "RUNNING"

        # Setup endpoints now rejected; runtime endpoints work.
        assert client.get("/api/setup/status").status_code == 409
        devices = client.get("/api/devices").get_json()
        assert devices[0]["id"] == "dev1"
        assert devices[0]["signal_count"] == 8
    finally:
        engine.stop()


def _vlan_device_config(port):
    """Config with VLAN tags present, so auto-mode would enable VLAN mode."""
    return make_config_yaml(
        [{"id": "dev1", "ip": "10.4.1.10", "port": port, "unit_id": 1, "vlan": 100}],
        vlan_mode="auto",
    )


def test_set_vlan_mode_disabled_updates_status_and_file(tmp_path):
    engine, client = make_engine(tmp_path)
    upload_file(client, "/api/setup/config", _vlan_device_config(5050), "sim_config.yaml")
    # auto + a device VLAN -> VLAN mode would be active
    assert client.get("/api/setup/status").get_json()["vlan_active"] is True

    resp = client.post("/api/setup/vlan_mode", json={"mode": "disabled"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "vlan_mode": "disabled", "vlan_active": False}

    status = client.get("/api/setup/status").get_json()
    assert status["vlan_mode"] == "disabled"
    assert status["vlan_active"] is False
    # Persisted to the on-disk config the engine starts from.
    assert "vlan_mode: disabled" in (tmp_path / "sim_config.yaml").read_text()


def test_set_vlan_mode_inserts_key_when_missing(tmp_path):
    engine, client = make_engine(tmp_path)
    # A config that omits vlan_mode entirely (defaults to auto).
    yaml = (
        "project:\n  name: P\n"
        "network:\n  traffic_interface: lo\n"
        "devices:\n  - id: dev1\n    name: dev1\n    ip: 127.0.0.1\n"
        "    port: 5051\n    unit_id: 1\n    signals_file: devices/dev1.csv\n"
    )
    upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")

    resp = client.post("/api/setup/vlan_mode", json={"mode": "disabled"})
    assert resp.status_code == 200
    assert "vlan_mode: disabled" in (tmp_path / "sim_config.yaml").read_text()


def test_set_vlan_mode_rejects_invalid(tmp_path):
    _, client = make_engine(tmp_path)
    upload_file(client, "/api/setup/config", _single_device_config(5052), "sim_config.yaml")
    resp = client.post("/api/setup/vlan_mode", json={"mode": "bogus"})
    assert resp.status_code == 400


def test_set_vlan_mode_rejected_after_start(tmp_path):
    engine, client = make_engine(tmp_path)
    try:
        upload_file(client, "/api/setup/config", _single_device_config(5053), "sim_config.yaml")
        upload_file(client, "/api/setup/signals/dev1", SAMPLE_SIGNALS, "dev1.csv")
        client.post("/api/setup/start")
        resp = client.post("/api/setup/vlan_mode", json={"mode": "disabled"})
        assert resp.status_code == 409
    finally:
        engine.stop()
