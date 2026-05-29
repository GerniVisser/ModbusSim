"""Web UI serving + setup-status device list (REQUIREMENTS.md section 12)."""
from modbus_sim.api_server import create_app
from modbus_sim.state_machine import StateMachine

from .conftest import make_config_yaml, make_engine, upload_file


def _client(tmp_path, headless):
    engine = StateMachine(tmp_path, manage_network=False)
    app = create_app(engine, headless=headless)
    app.testing = True
    return app.test_client()


def test_index_served_in_full_mode(tmp_path):
    resp = _client(tmp_path, headless=False).get("/")
    assert resp.status_code == 200
    assert b"Modbus" in resp.data and b"setup-view" in resp.data


def test_static_assets_served(tmp_path):
    client = _client(tmp_path, headless=False)
    for asset in ("app.js", "setup.js", "runtime.js", "editor.js"):
        resp = client.get(f"/webui/{asset}")
        assert resp.status_code == 200, asset


def test_ui_disabled_in_headless(tmp_path):
    client = _client(tmp_path, headless=True)
    assert client.get("/").status_code == 404
    assert client.get("/webui/app.js").status_code == 404


def test_setup_status_lists_devices(tmp_path):
    _, client = make_engine(tmp_path)
    yaml = make_config_yaml([
        {"id": "dev1", "name": "Device One", "ip": "127.0.0.1", "port": 5060, "unit_id": 1},
    ])
    upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
    status = client.get("/api/setup/status").get_json()
    assert status["devices"] == [
        {"id": "dev1", "name": "Device One", "signals_uploaded": False}
    ]
