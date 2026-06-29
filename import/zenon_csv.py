"""Zenon 15 Engineering Studio variable export → Modbus Simulator files.

Standalone module — zero dependencies on modbus_sim.*.  Can be used as a
library (called by the API server) or as a CLI tool:

    python3 import/zenon_csv.py --input export.csv --output-dir ./project/ \\
        --project-name "My Plant" --traffic-interface eth1
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

def _is_modbus_driver(driver_type: str, driver_name: str) -> bool:
    """Return True if the row belongs to a Modbus TCP driver.

    Matches any DriverType or DriverName that contains 'MODBUS' (case-insensitive),
    which covers MODBUS_ENERGY, MODBUS_TCP, MODBUS_TCPIP, MODBUS_ENERGY_2, etc.
    """
    combined = (driver_type + " " + driver_name).upper()
    return "MODBUS" in combined

# Zenon TypeName → simulator data_type
TYPE_MAP: dict[str, str] = {
    "UDINT": "uint32",
    "DINT": "int32",
    "UINT": "uint16",
    "INT": "int16",
    "SINT": "int16",
    "USINT": "uint16",
    "BOOL": "bool",
    "FLOAT": "float32",
    "REAL": "float32",
    "WORD": "uint16",
    "DWORD": "uint32",
    "LINT": "int32",
}

# Types that occupy 2 registers and require word_order in the signal CSV.
WIDE_TYPES = frozenset({"uint32", "int32", "float32"})

# Zenon HWObjectType → Modbus register area. Values outside this map fall back
# to "holding" (Modbus Energy exports only use 8 and 64 in practice).
HWOBJECT_TYPE_MAP: dict[int, str] = {8: "holding", 64: "input"}
DEFAULT_REGISTER_TYPE = "holding"


@dataclass
class ParsedSignal:
    name: str
    address: int
    data_type: str
    register_type: str     # holding / input — derived from Zenon HWObjectType
    bit_index: int | None  # None for non-bool; 0-15 for bool in holding register
    unit: str
    description: str
    writable: bool


@dataclass
class ParsedDevice:
    driver_name: str
    net_addr: int          # Zenon NetAddr — uniquely identifies a physical device within a driver
    suggested_id: str      # sanitised slug for use as sim device id
    signals: list[ParsedSignal] = field(default_factory=list)

    @property
    def signal_count(self) -> int:
        return len(self.signals)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file(file_bytes: bytes) -> tuple[list[ParsedDevice], int, dict[str, int], list[str]]:
    """Parse a Zenon 15 variable export (tab, semicolon, or comma delimited).

    Groups signals by (DriverName, NetAddr) — one ParsedDevice per unique pair.
    A Zenon driver instance can hold up to 255 devices (NetAddr 0-254), so large
    projects use multiple drivers. The Modbus TCP IP and unit/slave address are
    NOT in the export; they must be supplied by the user when generating config.

    Returns (devices, skipped_count, driver_type_counts, found_columns).
    driver_type_counts maps every unique DriverType value seen to row count,
    useful for diagnosing filter mismatches when no Modbus signals are found.
    """
    text = _decode(file_bytes)
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    # Strip whitespace from column names — handles Zenon exports that pad headers.
    _ = reader.fieldnames  # trigger lazy header read
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]

    found_columns = list(reader.fieldnames or [])

    devices: dict[tuple[str, int], ParsedDevice] = {}
    skipped = 0
    driver_type_counts: dict[str, int] = {}

    for row in reader:
        driver_type = (row.get("DriverType") or "").strip()
        driver_name = (row.get("DriverName") or "").strip()
        driver_type_counts[driver_type] = driver_type_counts.get(driver_type, 0) + 1

        if not _is_modbus_driver(driver_type, driver_name):
            skipped += 1
            continue

        type_name = (row.get("TypeName") or "").strip().upper()
        data_type = TYPE_MAP.get(type_name)
        if data_type is None:
            skipped += 1
            continue

        name = (row.get("VariableName") or "").strip()
        if not name:
            skipped += 1
            continue

        net_addr = _parse_int(row.get("NetAddr"))
        if net_addr is None:
            skipped += 1
            continue

        address = _parse_int(row.get("Offset"))
        if address is None:
            skipped += 1
            continue

        hw_obj = _parse_int(row.get("HWObjectType"))
        register_type = HWOBJECT_TYPE_MAP.get(hw_obj, DEFAULT_REGISTER_TYPE)

        bit_index: int | None = None
        if data_type == "bool":
            bit_index = _parse_int(row.get("BitAddr")) or 0

        unit_str = (row.get("Unit") or "").strip()
        if unit_str in ("0", "0.0"):
            unit_str = ""

        description = (row.get("Description") or "").strip()
        writable = _to_bool(row.get("ReadWrite") or "")

        key = (driver_name, net_addr)
        if key not in devices:
            devices[key] = ParsedDevice(
                driver_name=driver_name,
                net_addr=net_addr,
                suggested_id=_make_suggested_id(driver_name, net_addr),
            )

        devices[key].signals.append(
            ParsedSignal(
                name=name,
                address=address,
                data_type=data_type,
                register_type=register_type,
                bit_index=bit_index,
                unit=unit_str,
                description=description,
                writable=writable,
            )
        )

    return list(devices.values()), skipped, driver_type_counts, found_columns


def generate_signal_csv(
    device: ParsedDevice,
    word_order: str = "little_endian",
) -> str:
    """Return a signal CSV string for one device.

    The output passes modbus_sim.signal_loader.parse_and_validate without
    modification, so it can be fed directly to engine.upload_signals().
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "name",
            "register_type",
            "address",
            "data_type",
            "bit_index",
            "word_order",
            "scale",
            "unit",
            "section",
            "description",
            "default_value",
            "writable",
        ]
    )
    for sig in device.signals:
        wo = word_order if sig.data_type in WIDE_TYPES else ""
        bi = "" if sig.bit_index is None else sig.bit_index
        writer.writerow(
            [
                sig.name,
                sig.register_type,
                sig.address,
                sig.data_type,
                bi,
                wo,
                "1",
                sig.unit,
                "General",
                sig.description,
                "0",
                "true" if sig.writable else "false",
            ]
        )
    return out.getvalue()


def generate_config_yaml(
    devices: list[ParsedDevice],
    device_params: dict[tuple[str, int], dict],
    project_name: str,
    traffic_interface: str,
    web_ui_port: int = 5000,
) -> str:
    """Return a sim_config.yaml string.

    device_params maps (driver_name, net_addr) -> {id, name, ip, port,
    unit_id, vlan, prefix_length}.  The Modbus unit_id and IP are
    user-supplied and must be present in the params dict.
    Keys not in device_params fall back to safe defaults.
    """
    lines = [
        "project:",
        f"  name: {_yaml_str(project_name)}",
        "",
        "network:",
        f"  traffic_interface: {traffic_interface}",
        f"  web_ui_port: {web_ui_port}",
        "  vlan_mode: auto",
        "",
        "devices:",
    ]
    for dev in devices:
        p = device_params.get((dev.driver_name, dev.net_addr), {})
        dev_id = (p.get("id") or dev.suggested_id).strip()
        name = (p.get("name") or dev.driver_name).strip()
        ip = p.get("ip") or "0.0.0.0"
        port = int(p.get("port") or 502)
        unit_id = int(p.get("unit_id") or 1)
        vlan = int(p.get("vlan") or 0)
        prefix = int(p.get("prefix_length") or 24)
        lines += [
            f"  - id: {dev_id}",
            f"    name: {_yaml_str(name)}",
            f"    ip: {ip}",
            f"    port: {port}",
            f"    unit_id: {unit_id}",
            f"    vlan: {vlan}",
            f"    prefix_length: {prefix}",
            f"    signals_file: devices/{dev_id}.csv",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_delimiter(text: str) -> str:
    """Return the most likely field delimiter by counting candidates in the header."""
    first_line = text.split("\n")[0]
    counts = {"\t": first_line.count("\t"), ";": first_line.count(";"), ",": first_line.count(",")}
    return max(counts, key=counts.get)


def _decode(data: bytes) -> str:
    # UTF-16 must only be attempted when the BOM is present; without it the codec
    # silently misinterprets UTF-8 bytes as UTF-16 pairs, producing garbage.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16").replace("\r\n", "\n").replace("\r", "\n")
        except (UnicodeDecodeError, UnicodeError):
            pass

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("latin-1", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y")


def _slugify(text: str, max_len: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:max_len]


def _make_suggested_id(driver_name: str, net_addr: int) -> str:
    slug = _slugify(driver_name)
    return f"{slug}_n{net_addr}"[:40]


def _yaml_str(value: str) -> str:
    """Wrap a YAML scalar in quotes if it contains special characters."""
    needs_quoting = any(c in value for c in ':{}[]|>&*!,\'"@`#?\\')
    if needs_quoting or not value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a Zenon 15 variable export to simulator config + signal files."
    )
    parser.add_argument("--input", required=True, help="Zenon tab-delimited CSV file")
    parser.add_argument("--output-dir", default="./project", help="Output directory (default: ./project)")
    parser.add_argument("--project-name", default="Imported Project", help="Project name for sim_config.yaml")
    parser.add_argument("--traffic-interface", default="eth1", help="traffic_interface value")
    parser.add_argument(
        "--word-order",
        default="little_endian",
        choices=["big_endian", "little_endian"],
        help="Word order for 32-bit types (default: little_endian)",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    file_bytes = Path(args.input).read_bytes()
    devices, skipped, _dtc, _cols = parse_file(file_bytes)

    print(f"Parsed {len(devices)} device(s), {skipped} non-Modbus row(s) skipped.")
    for dev in devices:
        print(f"  {dev.driver_name!r}  net_addr={dev.net_addr}  ->  {dev.suggested_id}  ({dev.signal_count} signals)")

    config_yaml = generate_config_yaml(
        devices,
        {},
        project_name=args.project_name,
        traffic_interface=args.traffic_interface,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sim_config.yaml").write_text(config_yaml, encoding="utf-8")
    print(f"\nWrote {out_dir / 'sim_config.yaml'}")
    print("  NOTE: IPs, VLANs, and Modbus unit addresses are placeholder -- edit before use.\n")

    devices_dir = out_dir / "devices"
    devices_dir.mkdir(exist_ok=True)
    for dev in devices:
        csv_text = generate_signal_csv(dev, args.word_order)
        csv_path = devices_dir / f"{dev.suggested_id}.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        print(f"  Wrote {csv_path}  ({dev.signal_count} signals)")


if __name__ == "__main__":
    _cli()
