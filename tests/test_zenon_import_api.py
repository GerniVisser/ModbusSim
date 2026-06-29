"""Integration tests for the Zenon import API endpoints."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

from tests.conftest import make_engine

# Re-use the sample row builder from the unit tests.
# Register in sys.modules before exec so @dataclass can resolve its module
# (required on Python 3.14+).
import sys as _sys

_ZENON_PATH = Path(__file__).parents[1] / "import" / "zenon_csv.py"
_spec = importlib.util.spec_from_file_location("zenon_csv", _ZENON_PATH)
_zmod = importlib.util.module_from_spec(_spec)
_sys.modules["zenon_csv"] = _zmod
_spec.loader.exec_module(_zmod)

HEADER = (
    "VariableName\tDriverName\tDriverType\tHWObjectType\tTypeName\tMatrix\tTagname"
    "\tUnit\tExternalReference\tDescription\tSOSourceName\tSystemModelGroup"
    "\tAlternateValue\tAlternateValueString\tRecourceslabel\tNetAddr\tDataBlock"
    "\tOffset\tBitAddr\tAlignment\tStringLength\tSymbAddr\tUpdatePriority\tStandby"
    "\tDigits\tSignalMin\tSignalMax\tRangeMin\tRangeMax\tUseMacro\tAdjustHardware"
    "\tAdjustZenon\tHystNeg\tHystPos\tArchHystValueType\tArchHystNeg\tArchHystPos"
    "\tArchHystRelativeMinus\tArchHystRelativePlus\tSwingingDoorAlgorithmToleranceType"
    "\tSwingingDoorAlgorithmTolerance\tSwingingDoorAlgorithmRelativeTolerance"
    "\tZeroClamping\tTimestampDeviation\tDDEActive\tArraySizeOld\tCounterGroup"
    "\tMaxGradient\tNormalStateActive\tNormalState\tAlarmPV0\tAlarmPV1\tAlarmPV2"
    "\tHDActive\tHDUpdate\tHDSize\tIsKDAActiv\tExternVisible\tExternVisibleFor"
    "\tReadWrite\tInitialValue\tProfilename\tAdressparam\tVargroup"
    "\tServiceGridAccessPermission\tIsRemaActiv\tAlarmQuitPV\tAlarmViewQuitPV"
    "\tAlarmQuitPVValue\tVarInASM\tAlarmViaEquipmentModel\tAreaName\tAreaName2"
    "\tAreaName3\tAreaName4\tPasswordlevel\teSignatureCommentRequiredForPerform"
    "\tSignatureMode\teSignatureVerificationLevel\teSignatureCommentRequiredForVerify"
    "\teSignatureApprobationLevel\teSignatureCommentRequiredForApprove\tSignatureText"
    "\tSignatureEditModus\tInOut\tSBO\tCancelOperate\tValueMin\tValueMax\tLockingName"
    "\tSetValueProtocol\tSV_Act\tSV_VBA\tVisualName\tMeaning\tWaterfallParam"
    "\tUse_in_ProcRec\tStyleGroup\tScaleStyle\tCurveStyle\tIEC870_TYPE\tIEC870_COA1"
    "\tIEC870_IOA1\tIEC870_PRIVATEINDEX"
)

_COLS = HEADER.split("\t")

_DEFAULTS = {c: "" for c in _COLS}
_DEFAULTS.update(dict(
    DriverName="Modbus Energy Driver 2", DriverType="MODBUS_ENERGY",
    HWObjectType="8", TypeName="UINT", NetAddr="7", DataBlock="0",
    Offset="1000", BitAddr="0", UpdatePriority="5", Digits="0",
    SignalMin="0", SignalMax="65535", RangeMin="0", RangeMax="65535",
    UseMacro="FALSE", ZeroClamping="FALSE", DDEActive="FALSE",
    ArraySizeOld="1", CounterGroup="0", MaxGradient="0",
    NormalStateActive="FALSE", NormalState="FALSE", HDActive="FALSE",
    IsKDAActiv="FALSE", ExternVisible="TRUE", ReadWrite="0",
    SetValueProtocol="1", SV_Act="FALSE", SV_VBA="FALSE",
    InOut="TRUE", SBO="FALSE", CancelOperate="FALSE",
    eSignatureCommentRequiredForPerform="TRUE",
    eSignatureCommentRequiredForVerify="TRUE",
    eSignatureCommentRequiredForApprove="TRUE",
))


def _row(**overrides) -> str:
    d = dict(_DEFAULTS)
    d.update(overrides)
    return "\t".join(str(d.get(c, "")) for c in _COLS)


def _csv_file(*rows) -> bytes:
    return (HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


def _upload(client, path, content, filename="export.csv"):
    return client.post(
        path,
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# Parse endpoint
# ---------------------------------------------------------------------------

def test_parse_returns_driver_list(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(
        _row(VariableName="Sig1", TypeName="UINT", NetAddr="7", Offset="1000"),
        _row(VariableName="Sig2", TypeName="DINT", NetAddr="7", Offset="1002"),
    )
    r = _upload(client, "/api/import/zenon/parse", data)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["drivers"]) == 1
    assert body["drivers"][0]["driver_name"] == "Modbus Energy Driver 2"
    assert body["drivers"][0]["net_addr"] == 7
    assert body["drivers"][0]["signal_count"] == 2
    assert body["total_signals"] == 2
    assert body["skipped_non_modbus"] == 0


def test_parse_skips_iec870(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(
        _row(VariableName="Good", DriverType="MODBUS_ENERGY"),
        _row(VariableName="Skip", DriverType="IEC870", DriverName="IEC driver", TypeName="BOOL"),
    )
    r = _upload(client, "/api/import/zenon/parse", data)
    body = r.get_json()
    assert body["skipped_non_modbus"] == 1
    assert body["total_signals"] == 1


def test_parse_saves_tmp_file(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(_row(VariableName="Sig"))
    _upload(client, "/api/import/zenon/parse", data)
    assert (tmp_path / "zenon_import.tmp").exists()


def test_parse_requires_file_field(tmp_path):
    _, client = make_engine(tmp_path)
    r = client.post("/api/import/zenon/parse", json={})
    assert r.status_code == 400


def test_parse_blocked_in_running_state(tmp_path):
    from tests.conftest import make_config_yaml, SAMPLE_SIGNALS
    engine, client = make_engine(tmp_path)
    cfg = make_config_yaml([{"id": "d1", "ip": "127.0.0.1", "port": 5502, "unit_id": 1}])
    _upload(client, "/api/setup/config", cfg.encode(), "sim_config.yaml")
    _upload(client, "/api/setup/signals/d1", SAMPLE_SIGNALS.encode(), "d1.csv")
    client.post("/api/setup/start")
    data = _csv_file(_row(VariableName="Sig"))
    r = _upload(client, "/api/import/zenon/parse", data)
    assert r.status_code == 409


def test_parse_blocked_after_config_locked(tmp_path):
    from tests.conftest import make_config_yaml
    _, client = make_engine(tmp_path)
    cfg = make_config_yaml([{"id": "d1", "ip": "127.0.0.1", "port": 5502, "unit_id": 1}])
    _upload(client, "/api/setup/config", cfg.encode(), "sim_config.yaml")
    # Config is now locked; parse should be blocked.
    data = _csv_file(_row(VariableName="Sig"))
    r = _upload(client, "/api/import/zenon/parse", data)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Generate endpoint
# ---------------------------------------------------------------------------

def test_generate_creates_config_and_signals(tmp_path):
    _, client = make_engine(tmp_path)

    # Parse first.
    data = _csv_file(
        _row(VariableName="Voltage", TypeName="UINT", NetAddr="7", Offset="1000", Unit="V"),
        _row(VariableName="Power", TypeName="DINT", NetAddr="7", Offset="1002", Unit="W"),
    )
    _upload(client, "/api/import/zenon/parse", data)

    # Generate.
    body = {
        "project_name": "Test Plant",
        "traffic_interface": "eth1",
        "web_ui_port": 5000,
        "drivers": [{
            "driver_name": "Modbus Energy Driver 2",
            "net_addr": 7,
            "unit_id": 1,
            "id": "driver2_n7",
            "name": "Driver 2",
            "ip": "10.4.1.7",
            "port": 502,
            "vlan": 100,
            "prefix_length": 24,
            "word_order": "little_endian",
        }],
    }
    r = client.post("/api/import/zenon/generate", json=body)
    assert r.status_code == 200
    resp = r.get_json()
    assert resp["ok"] is True
    assert resp["devices_generated"] == 1
    assert resp["total_signals"] == 2
    assert resp["ready_to_start"] is True


def test_generate_maps_hwobjecttype_to_register_type(tmp_path):
    import csv as _csv

    eng, client = make_engine(tmp_path)

    data = _csv_file(
        _row(VariableName="Heartbeat", HWObjectType="8", TypeName="REAL",
             NetAddr="7", Offset="40001"),
        _row(VariableName="Total_current", HWObjectType="64", TypeName="UDINT",
             NetAddr="7", Offset="7030"),
    )
    _upload(client, "/api/import/zenon/parse", data)

    r = client.post("/api/import/zenon/generate", json={
        "project_name": "Test Plant",
        "traffic_interface": "eth1",
        "drivers": [{
            "driver_name": "Modbus Energy Driver 2", "net_addr": 7,
            "unit_id": 1, "id": "driver2_n7", "name": "Driver 2",
            "ip": "10.4.1.7", "word_order": "little_endian",
        }],
    })
    assert r.status_code == 200

    csv_text = (eng.project_dir / "devices" / "driver2_n7.csv").read_text()
    by_name = {row["name"]: row for row in _csv.DictReader(io.StringIO(csv_text))}
    assert by_name["Heartbeat"]["register_type"] == "holding"
    assert by_name["Total_current"]["register_type"] == "input"


def test_generate_locks_config_and_uploads_signals(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(_row(VariableName="Sig1", TypeName="UINT", NetAddr="7", Offset="1000"))
    _upload(client, "/api/import/zenon/parse", data)

    client.post("/api/import/zenon/generate", json={
        "project_name": "Plant",
        "traffic_interface": "eth1",
        "drivers": [{
            "driver_name": "Modbus Energy Driver 2", "net_addr": 7, "unit_id": 1,
            "id": "dev1", "name": "Dev 1", "ip": "10.0.0.1",
            "port": 502, "vlan": 0, "prefix_length": 24, "word_order": "little_endian",
        }],
    })

    # Setup status should now say can_start=True.
    r = client.get("/api/setup/status")
    st = r.get_json()
    assert st["config_locked"] is True
    assert st["can_start"] is True


def test_generate_cleans_tmp_file(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(_row(VariableName="Sig", TypeName="UINT", NetAddr="1", Offset="100"))
    _upload(client, "/api/import/zenon/parse", data)
    assert (tmp_path / "zenon_import.tmp").exists()

    client.post("/api/import/zenon/generate", json={
        "project_name": "P", "traffic_interface": "eth1",
        "drivers": [{
            "driver_name": "Modbus Energy Driver 2", "net_addr": 1, "unit_id": 1,
            "id": "d1", "name": "D1", "ip": "10.0.0.1",
            "port": 502, "vlan": 0, "prefix_length": 24, "word_order": "little_endian",
        }],
    })
    assert not (tmp_path / "zenon_import.tmp").exists()


def test_generate_missing_ip_returns_400(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(_row(VariableName="Sig"))
    _upload(client, "/api/import/zenon/parse", data)

    r = client.post("/api/import/zenon/generate", json={
        "project_name": "P", "traffic_interface": "eth1",
        "drivers": [{
            "driver_name": "Modbus Energy Driver 2", "net_addr": 7, "unit_id": 1,
            "id": "d1", "name": "D1", "ip": "",
            "port": 502, "vlan": 0, "prefix_length": 24, "word_order": "little_endian",
        }],
    })
    assert r.status_code == 400


def test_generate_without_parse_returns_400(tmp_path):
    _, client = make_engine(tmp_path)
    r = client.post("/api/import/zenon/generate", json={
        "project_name": "P", "traffic_interface": "eth1",
        "drivers": [{"driver_name": "X", "unit_id": 1, "id": "d1",
                     "name": "D1", "ip": "10.0.0.1", "port": 502}],
    })
    assert r.status_code == 400


def test_generate_missing_project_name_returns_400(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(_row(VariableName="Sig"))
    _upload(client, "/api/import/zenon/parse", data)
    r = client.post("/api/import/zenon/generate", json={
        "traffic_interface": "eth1",
        "drivers": [{"driver_name": "Modbus Energy Driver 2", "net_addr": 7, "unit_id": 1,
                     "id": "d1", "name": "D1", "ip": "10.0.0.1", "port": 502}],
    })
    assert r.status_code == 400


def test_generate_blocked_when_config_locked(tmp_path):
    from tests.conftest import make_config_yaml
    _, client = make_engine(tmp_path)
    cfg = make_config_yaml([{"id": "d1", "ip": "127.0.0.1", "port": 5502, "unit_id": 1}])
    _upload(client, "/api/setup/config", cfg.encode(), "sim_config.yaml")
    r = client.post("/api/import/zenon/generate", json={
        "project_name": "P", "traffic_interface": "eth1",
        "drivers": [{"driver_name": "X", "unit_id": 1, "id": "d1",
                     "name": "D", "ip": "10.0.0.1", "port": 502}],
    })
    assert r.status_code == 409


def test_generate_two_devices(tmp_path):
    _, client = make_engine(tmp_path)
    data = _csv_file(
        _row(VariableName="Sig1", DriverName="Driver A", NetAddr="1", Offset="100"),
        _row(VariableName="Sig2", DriverName="Driver B", NetAddr="2", Offset="200"),
    )
    _upload(client, "/api/import/zenon/parse", data)

    r = client.post("/api/import/zenon/generate", json={
        "project_name": "Multi Device",
        "traffic_interface": "eth1",
        "drivers": [
            {"driver_name": "Driver A", "net_addr": 1, "unit_id": 1, "id": "dev_a",
             "name": "Device A", "ip": "10.0.0.1", "port": 502,
             "vlan": 100, "prefix_length": 24, "word_order": "little_endian"},
            {"driver_name": "Driver B", "net_addr": 2, "unit_id": 1, "id": "dev_b",
             "name": "Device B", "ip": "10.0.0.2", "port": 502,
             "vlan": 200, "prefix_length": 24, "word_order": "big_endian"},
        ],
    })
    assert r.status_code == 200
    resp = r.get_json()
    assert resp["devices_generated"] == 2
    assert resp["total_signals"] == 2

    st = client.get("/api/setup/status").get_json()
    assert st["can_start"] is True
    assert st["devices_total"] == 2
