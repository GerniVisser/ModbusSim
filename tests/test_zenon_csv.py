"""Unit tests for import/zenon_csv.py."""
from __future__ import annotations

import importlib.util
import io
import csv
from pathlib import Path

import pytest

# Load the standalone module (lives outside the modbus_sim package).
# Register in sys.modules before exec so @dataclass can resolve its module
# (required on Python 3.14+).
import sys as _sys

_MODULE_PATH = Path(__file__).parents[1] / "import" / "zenon_csv.py"
_spec = importlib.util.spec_from_file_location("zenon_csv", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_sys.modules["zenon_csv"] = _mod
_spec.loader.exec_module(_mod)

parse_file = _mod.parse_file
generate_signal_csv = _mod.generate_signal_csv
generate_config_yaml = _mod.generate_config_yaml


# ---------------------------------------------------------------------------
# Sample data (condensed from user's Zenon 15 export)
# ---------------------------------------------------------------------------

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

def _make_row(**overrides) -> str:
    """Build a tab-separated data row with sensible defaults."""
    defaults = dict(
        VariableName="Signal1",
        DriverName="Modbus Energy Driver 2",
        DriverType="MODBUS_ENERGY",
        HWObjectType="8",
        TypeName="UINT",
        Matrix="",
        Tagname="",
        Unit="V",
        ExternalReference="",
        Description="Test signal",
        SOSourceName="",
        SystemModelGroup="",
        AlternateValue="",
        AlternateValueString="",
        Recourceslabel="",
        NetAddr="7",
        DataBlock="0",
        Offset="1000",
        BitAddr="0",
        Alignment="",
        StringLength="",
        SymbAddr="",
        UpdatePriority="5",
        Standby="",
        Digits="0",
        SignalMin="0",
        SignalMax="65535",
        RangeMin="0",
        RangeMax="65535",
        UseMacro="FALSE",
        AdjustHardware="",
        AdjustZenon="",
        HystNeg="0",
        HystPos="0",
        ArchHystValueType="0",
        ArchHystNeg="0",
        ArchHystPos="0",
        ArchHystRelativeMinus="0",
        ArchHystRelativePlus="0",
        SwingingDoorAlgorithmToleranceType="0",
        SwingingDoorAlgorithmTolerance="0",
        SwingingDoorAlgorithmRelativeTolerance="0",
        ZeroClamping="FALSE",
        TimestampDeviation="",
        DDEActive="FALSE",
        ArraySizeOld="1",
        CounterGroup="0",
        MaxGradient="0",
        NormalStateActive="FALSE",
        NormalState="FALSE",
        AlarmPV0="",
        AlarmPV1="",
        AlarmPV2="",
        HDActive="FALSE",
        HDUpdate="",
        HDSize="",
        IsKDAActiv="FALSE",
        ExternVisible="TRUE",
        ExternVisibleFor="",
        ReadWrite="0",
        InitialValue="",
        Profilename="",
        Adressparam="",
        Vargroup="",
        ServiceGridAccessPermission="",
        IsRemaActiv="FALSE",
        AlarmQuitPV="",
        AlarmViewQuitPV="",
        AlarmQuitPVValue="",
        VarInASM="FALSE",
        AlarmViaEquipmentModel="",
        AreaName="",
        AreaName2="",
        AreaName3="",
        AreaName4="",
        Passwordlevel="0",
        eSignatureCommentRequiredForPerform="TRUE",
        SignatureMode="0",
        eSignatureVerificationLevel="0",
        eSignatureCommentRequiredForVerify="TRUE",
        eSignatureApprobationLevel="0",
        eSignatureCommentRequiredForApprove="TRUE",
        SignatureText="",
        SignatureEditModus="0",
        InOut="TRUE",
        SBO="FALSE",
        CancelOperate="FALSE",
        ValueMin="0",
        ValueMax="65535",
        LockingName="",
        SetValueProtocol="1",
        SV_Act="FALSE",
        SV_VBA="FALSE",
        VisualName="",
        Meaning="",
        WaterfallParam="",
        Use_in_ProcRec="FALSE",
        StyleGroup="",
        ScaleStyle="",
        CurveStyle="",
        IEC870_TYPE="",
        IEC870_COA1="",
        IEC870_IOA1="",
        IEC870_PRIVATEINDEX="",
    )
    defaults.update(overrides)
    cols = HEADER.split("\t")
    return "\t".join(str(defaults.get(c, "")) for c in cols)


def _make_csv(*rows) -> bytes:
    return (HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# parse_file tests
# ---------------------------------------------------------------------------

def test_parse_single_uint_signal():
    data = _make_csv(_make_row(VariableName="Voltage", TypeName="UINT", NetAddr="7", Offset="1000"))
    devices, skipped, _, _cols = parse_file(data)
    assert len(devices) == 1
    assert skipped == 0
    dev = devices[0]
    assert dev.driver_name == "Modbus Energy Driver 2"
    assert dev.net_addr == 7
    assert dev.signal_count == 1
    sig = dev.signals[0]
    assert sig.name == "Voltage"
    assert sig.data_type == "uint16"
    assert sig.address == 1000
    assert sig.bit_index is None


def test_parse_type_mapping():
    rows = [
        _make_row(VariableName="A", TypeName="UDINT", Offset="1000"),
        _make_row(VariableName="B", TypeName="DINT", Offset="1002"),
        _make_row(VariableName="C", TypeName="UINT", Offset="1004"),
        _make_row(VariableName="D", TypeName="INT", Offset="1005"),
        _make_row(VariableName="E", TypeName="BOOL", Offset="1006", BitAddr="0"),
        _make_row(VariableName="F", TypeName="REAL", Offset="1008"),
    ]
    data = _make_csv(*rows)
    devices, _, _dtc, _cols = parse_file(data)
    sigs = {s.name: s for s in devices[0].signals}
    assert sigs["A"].data_type == "uint32"
    assert sigs["B"].data_type == "int32"
    assert sigs["C"].data_type == "uint16"
    assert sigs["D"].data_type == "int16"
    assert sigs["E"].data_type == "bool"
    assert sigs["F"].data_type == "float32"


def test_bool_bit_index():
    row = _make_row(VariableName="Status_Bit3", TypeName="BOOL", Offset="1040", BitAddr="3")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    sig = devices[0].signals[0]
    assert sig.data_type == "bool"
    assert sig.bit_index == 3


def test_iec870_rows_skipped():
    modbus_row = _make_row(VariableName="ModbusSignal", DriverType="MODBUS_ENERGY")
    iec_row = _make_row(VariableName="IecSignal", DriverType="IEC870",
                        DriverName="IEC 60870-5-101_104", TypeName="BOOL")
    devices, skipped, _, _cols = parse_file(_make_csv(modbus_row, iec_row))
    assert skipped == 1
    assert len(devices) == 1
    assert devices[0].signals[0].name == "ModbusSignal"


def test_same_driver_different_net_addr_are_separate_devices():
    # (DriverName, NetAddr) is the key — same driver, different net_addr = separate physical devices.
    r1 = _make_row(VariableName="Sig1", DriverName="Modbus Energy Driver 2",
                   TypeName="UINT", NetAddr="7", Offset="1000")
    r2 = _make_row(VariableName="Sig2", DriverName="Modbus Energy Driver 2",
                   TypeName="UDINT", NetAddr="167", Offset="7050")
    r3 = _make_row(VariableName="Sig3", DriverName="Modbus Energy Driver 3",
                   TypeName="INT", NetAddr="1", Offset="500")
    devices, skipped, _, _cols = parse_file(_make_csv(r1, r2, r3))
    assert skipped == 0
    assert len(devices) == 3
    net_addrs = {d.net_addr for d in devices}
    assert 7 in net_addrs and 167 in net_addrs and 1 in net_addrs


def test_same_driver_and_net_addr_signals_grouped():
    # Same (DriverName, NetAddr) → same device, all signals collected together.
    rows = [
        _make_row(VariableName=f"Sig{i}", NetAddr="7", Offset=str(1000 + i))
        for i in range(3)
    ]
    devices, _, _dtc, _cols = parse_file(_make_csv(*rows))
    assert len(devices) == 1
    assert devices[0].signal_count == 3


def test_different_net_addr_different_devices():
    rows = [
        _make_row(VariableName=f"Sig{i}", NetAddr=str(i), Offset=str(1000 + i))
        for i in range(3)
    ]
    devices, _, _dtc, _cols = parse_file(_make_csv(*rows))
    assert len(devices) == 3


def test_suggested_id_format():
    row = _make_row(DriverName="Modbus Energy Driver 2", NetAddr="7")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    assert devices[0].suggested_id == "modbus_energy_driver_2_n7"


def test_unit_zero_stripped():
    row = _make_row(VariableName="Sig", Unit="0")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    assert devices[0].signals[0].unit == ""


def test_unit_preserved():
    row = _make_row(VariableName="Voltage", Unit="V")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    assert devices[0].signals[0].unit == "V"


def test_float_netaddr_and_offset():
    # Float-string NetAddr and Offset should both parse to integers.
    row = _make_row(VariableName="Sig", NetAddr="7.0", Offset="1000.0")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    assert devices[0].net_addr == 7
    assert devices[0].signals[0].address == 1000


def test_unknown_type_skipped():
    row = _make_row(VariableName="Sig", TypeName="LREAL")
    devices, skipped, _, _cols = parse_file(_make_csv(row))
    assert skipped == 1
    assert len(devices) == 0


def test_bom_utf8():
    row = _make_row(VariableName="BOM_Signal")
    raw = (HEADER + "\n" + row + "\n").encode("utf-8-sig")  # with BOM
    devices, _, _dtc, _cols = parse_file(raw)
    assert len(devices) == 1


# ---------------------------------------------------------------------------
# generate_signal_csv tests
# ---------------------------------------------------------------------------

def test_generate_csv_columns():
    row = _make_row(VariableName="GridV", TypeName="UINT", Offset="1000", Unit="V")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    csv_text = generate_signal_csv(devices[0])
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "GridV"
    assert r["register_type"] == "holding"
    assert r["address"] == "1000"
    assert r["data_type"] == "uint16"
    assert r["unit"] == "V"
    assert r["word_order"] == ""


def test_generate_csv_wide_type_word_order():
    row = _make_row(VariableName="Power", TypeName="UDINT", Offset="2000")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    csv_text = generate_signal_csv(devices[0], word_order="little_endian")
    reader = csv.DictReader(io.StringIO(csv_text))
    r = next(reader)
    assert r["data_type"] == "uint32"
    assert r["word_order"] == "little_endian"


def test_generate_csv_big_endian():
    row = _make_row(VariableName="Energy", TypeName="DINT", Offset="3000")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    csv_text = generate_signal_csv(devices[0], word_order="big_endian")
    reader = csv.DictReader(io.StringIO(csv_text))
    assert next(reader)["word_order"] == "big_endian"


def test_generate_csv_bool_bit_index():
    row = _make_row(VariableName="RunBit", TypeName="BOOL", Offset="1040", BitAddr="2")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    csv_text = generate_signal_csv(devices[0])
    reader = csv.DictReader(io.StringIO(csv_text))
    r = next(reader)
    assert r["data_type"] == "bool"
    assert r["bit_index"] == "2"
    assert r["word_order"] == ""


def test_generate_csv_passes_engine_validation():
    """CSV output must pass the engine's own signal_loader without errors."""
    from modbus_sim.signal_loader import parse_and_validate

    rows = [
        _make_row(VariableName="Voltage", TypeName="UINT", Offset="1000", Unit="V"),
        _make_row(VariableName="Power", TypeName="DINT", Offset="1002"),
        _make_row(VariableName="Energy", TypeName="UDINT", Offset="1004"),
        _make_row(VariableName="Temp", TypeName="INT", Offset="1006"),
        _make_row(VariableName="Status", TypeName="BOOL", Offset="1008", BitAddr="0"),
        _make_row(VariableName="Fault", TypeName="BOOL", Offset="1008", BitAddr="1"),
        _make_row(VariableName="DCVolts", TypeName="REAL", Offset="1010"),
    ]
    devices, _, _dtc, _cols = parse_file(_make_csv(*rows))
    csv_text = generate_signal_csv(devices[0])
    signals, errors = parse_and_validate(csv_text)
    assert errors == [], [e.message for e in errors]
    assert len(signals) == 7


# ---------------------------------------------------------------------------
# generate_config_yaml tests
# ---------------------------------------------------------------------------

def test_generate_config_passes_engine_validation():
    """Config output must pass the engine's config_loader without errors."""
    from modbus_sim.config_loader import load_and_validate

    row = _make_row(VariableName="Sig1", NetAddr="7")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    # Key is (driver_name, net_addr); unit_id is the Modbus slave address (user-supplied).
    device_params = {
        ("Modbus Energy Driver 2", 7): {
            "id": "driver2_n7",
            "name": "Driver 2 Device 7",
            "ip": "10.4.26.2",
            "port": 502,
            "unit_id": 1,
            "vlan": 100,
            "prefix_length": 24,
        }
    }
    yaml_text = generate_config_yaml(
        devices, device_params,
        project_name="Test Project",
        traffic_interface="eth1",
    )
    config, errors = load_and_validate(yaml_text)
    assert errors == [], errors
    assert config.project_name == "Test Project"
    assert len(config.devices) == 1
    assert config.devices[0].ip == "10.4.26.2"
    assert config.devices[0].unit_id == 1
    assert config.devices[0].vlan == 100


def test_generate_config_multiple_devices():
    from modbus_sim.config_loader import load_and_validate

    rows = [
        _make_row(VariableName="S1", DriverName="Driver A", NetAddr="1", Offset="100"),
        _make_row(VariableName="S2", DriverName="Driver B", NetAddr="2", Offset="200"),
    ]
    devices, _, _dtc, _cols = parse_file(_make_csv(*rows))
    params = {
        ("Driver A", 1): {"id": "dev_a", "name": "Device A", "ip": "10.0.0.1", "port": 502, "unit_id": 1, "vlan": 0},
        ("Driver B", 2): {"id": "dev_b", "name": "Device B", "ip": "10.0.0.2", "port": 502, "unit_id": 1, "vlan": 0},
    }
    yaml_text = generate_config_yaml(devices, params, project_name="Multi", traffic_interface="eth1")
    config, errors = load_and_validate(yaml_text)
    assert errors == [], errors
    assert len(config.devices) == 2


def test_yaml_quoting_special_chars():
    from modbus_sim.config_loader import load_and_validate

    row = _make_row(VariableName="Sig", NetAddr="7")
    devices, _, _dtc, _cols = parse_file(_make_csv(row))
    params = {("Modbus Energy Driver 2", 7): {
        "id": "dev1", "name": "Plant: Main Inverter", "ip": "10.0.0.1", "port": 502, "unit_id": 1, "vlan": 0
    }}
    yaml_text = generate_config_yaml(
        devices, params, project_name="Plant: Main Site", traffic_interface="eth1"
    )
    config, errors = load_and_validate(yaml_text)
    assert errors == [], errors
    assert "Plant: Main Site" in config.project_name
