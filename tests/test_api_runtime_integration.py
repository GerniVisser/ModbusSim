"""End-to-end runtime test: API lifecycle + real Modbus client + hot reload.

Drives the engine through config -> signals -> start via the Flask test client, then
connects a real pymodbus async client to the loopback Modbus sockets to verify values
and hot reload (REQUIREMENTS.md sections 11, 18 — Hot Reload, Modbus Server).

Runs anywhere (loopback, no NetworkManager). Only NetworkManager itself is Linux/root
specific and is covered separately.
"""
import asyncio

from pymodbus.client import AsyncModbusTcpClient

from .conftest import SAMPLE_SIGNALS, make_config_yaml, make_engine, upload_file

PORT = 5044


async def _read_hr(addr, unit, count=1):
    c = AsyncModbusTcpClient("127.0.0.1", port=PORT)
    await c.connect()
    try:
        r = await c.read_holding_registers(addr, count=count, device_id=unit)
        return None if r.isError() else r.registers
    finally:
        c.close()


def _signal_rows(grid_default):
    return [
        {"name": "Grid Voltage L1", "register_type": "holding", "address": 1000,
         "data_type": "uint16", "bit_index": None, "word_order": "big_endian",
         "scale": 0.1, "unit": "V", "section": "Grid", "description": "",
         "default_value": grid_default, "writable": False},
        {"name": "Active Power", "register_type": "holding", "address": 1004,
         "data_type": "int32", "bit_index": None, "word_order": "big_endian",
         "scale": 1, "unit": "W", "section": "Grid", "description": "",
         "default_value": 50, "writable": False},
    ]


def test_runtime_modbus_and_hot_reload(tmp_path):
    engine, client = make_engine(tmp_path)
    yaml = make_config_yaml([
        {"id": "dev_a", "ip": "127.0.0.1", "port": PORT, "unit_id": 1},
        {"id": "dev_b", "ip": "127.0.0.1", "port": PORT, "unit_id": 2},
    ])
    try:
        upload_file(client, "/api/setup/config", yaml, "sim_config.yaml")
        upload_file(client, "/api/setup/signals/dev_a", SAMPLE_SIGNALS, "dev_a.csv")
        upload_file(client, "/api/setup/signals/dev_b", SAMPLE_SIGNALS, "dev_b.csv")
        assert client.post("/api/setup/start").status_code == 200

        # Two devices share (ip, port) via distinct unit ids -> one server, grouped.
        assert engine._servers.server_count == 1

        # Defaults loaded and readable on both units.
        assert asyncio.run(_read_hr(1000, unit=1)) == [4000]
        assert asyncio.run(_read_hr(1000, unit=2)) == [4000]

        # Set a value via API; reflected on unit 1's next poll, unit 2 unaffected.
        resp = client.post("/api/devices/dev_a/set",
                           json={"name": "Grid Voltage L1", "value": 1234})
        assert resp.status_code == 200
        assert asyncio.run(_read_hr(1000, unit=1)) == [1234]
        assert asyncio.run(_read_hr(1000, unit=2)) == [4000]

        # Out-of-range read -> Modbus exception (None here).
        assert asyncio.run(_read_hr(50000, unit=1)) is None

        # Hot reload dev_a with a new signal list (new default + fewer signals).
        resp = client.post("/api/devices/dev_a/signals",
                           json={"signals": _signal_rows(grid_default=4242)})
        assert resp.status_code == 200
        assert resp.get_json()["signal_count"] == 2
        # New default visible without restart; dev_b untouched; connections survive.
        assert asyncio.run(_read_hr(1000, unit=1)) == [4242]
        assert asyncio.run(_read_hr(1000, unit=2)) == [4000]

        # Invalid hot reload -> 400, live values unchanged.
        bad = {"signals": [{"name": "x", "register_type": "holding", "address": 0,
                            "data_type": "uint64", "default_value": 0}]}
        resp = client.post("/api/devices/dev_a/signals", json=bad)
        assert resp.status_code == 400
        assert asyncio.run(_read_hr(1000, unit=1)) == [4242]
    finally:
        engine.stop()
