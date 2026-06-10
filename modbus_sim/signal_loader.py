"""Parse and validate a device signal CSV (REQUIREMENTS.md section 7).

Each device has one CSV listing every Modbus register the simulator responds to.
Validation produces row/column-level errors (section 11 ``/api/setup/signals``).
The float32 ``default_value`` is converted to a raw IEEE-754 u32 at load time
(section 17) so ``RegisterMap.set_defaults`` can write it as two 16-bit words
without re-interpreting types.
"""

from __future__ import annotations

import csv
import io
import struct
from dataclasses import dataclass, field
from typing import Optional

REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")
DATA_TYPES = ("uint16", "int16", "uint32", "int32", "float32", "bool")
WORD_ORDERS = ("big_endian", "little_endian")
WIDE_TYPES = ("uint32", "int32", "float32")  # occupy 2 registers, need word_order
# Per-signal simulation override modes (empty = inherit the project default).
VALID_SIM_MODES = ("static", "oscillate", "sawtooth", "triangle", "step", "toggle")

HEADER = [
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

# Optional simulation columns appended when writing CSVs. They are NOT part of the
# required-column check, so existing signal files without them still load fine.
SIM_COLUMNS = ["sim_mode", "sim_min", "sim_max", "sim_period", "sim_step"]
FULL_HEADER = HEADER + SIM_COLUMNS


@dataclass
class Signal:
    name: str
    register_type: str
    address: int
    data_type: str
    bit_index: Optional[int] = None
    word_order: Optional[str] = None
    scale: float = 1.0
    unit: str = ""
    section: str = "General"
    description: str = ""
    default_value: float = 0.0  # engineering value as written in the CSV
    writable: bool = False
    # Optional per-signal simulation override (empty sim_mode => inherit project
    # default; "static" => never fluctuate). sim_min/sim_max are the low/high the
    # value moves between (a numeric signal only fluctuates when both are set).
    # sim_period is the cycle length (or, for the "step" motion, the hold interval);
    # sim_step is the jump size for the "step" motion. See simulator.resolve_profile.
    sim_mode: str = ""
    sim_min: Optional[float] = None
    sim_max: Optional[float] = None
    sim_period: Optional[float] = None
    sim_step: Optional[float] = None
    # Raw register default(s), computed at load time. For float32 this is the
    # IEEE-754 u32; for ints it is the masked value; for bool it is 0/1.
    default_raw: int = field(default=0)

    @property
    def register_span(self) -> int:
        """Number of 16-bit registers this signal occupies (2 for wide types)."""
        return 2 if self.data_type in WIDE_TYPES else 1


@dataclass
class SignalError:
    row: int  # 1-based CSV row number including the header row
    column: str
    message: str

    def as_dict(self) -> dict:
        return {"row": self.row, "column": self.column, "message": self.message}


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _float_to_raw_u32(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]


def parse_and_validate(csv_text: str) -> tuple[list[Signal], list[SignalError]]:
    """Parse CSV text into Signals, collecting row/column-level errors.

    Returns ``(signals, [])`` on success or ``([], errors)`` on any failure.
    Row numbers are 1-based and include the header (so the first data row is 2),
    matching how a user views the file in a spreadsheet.
    """
    errors: list[SignalError] = []
    csv_text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
    first_line = csv_text[:csv_text.index("\n")] if "\n" in csv_text else csv_text
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)

    if reader.fieldnames is None:
        return [], [SignalError(1, "header", "file is empty")]

    missing = [c for c in HEADER if c not in reader.fieldnames]
    if missing:
        return [], [
            SignalError(1, "header", f"missing required column(s): {', '.join(missing)}")
        ]

    signals: list[Signal] = []
    seen_names: set[str] = set()
    # address -> set of bit_index used by bool signals (for (address,bit) uniqueness)
    bool_bits: dict[tuple[str, int], set[int]] = {}
    # (register_type, address) -> signal name, for non-bool overlap detection
    occupied: dict[tuple[str, int], str] = {}

    for offset, raw in enumerate(reader):
        row = offset + 2  # +1 for header, +1 for 1-based

        name = (raw.get("name") or "").strip()
        if not name:
            errors.append(SignalError(row, "name", "name is required"))
        elif name in seen_names:
            errors.append(SignalError(row, "name", f"duplicate signal name '{name}'"))
        else:
            seen_names.add(name)

        register_type = (raw.get("register_type") or "").strip()
        if register_type not in REGISTER_TYPES:
            errors.append(
                SignalError(row, "register_type",
                            f"'{register_type}' is not a valid register_type")
            )

        data_type = (raw.get("data_type") or "").strip()
        if data_type not in DATA_TYPES:
            errors.append(
                SignalError(row, "data_type", f"'{data_type}' is not a valid data type")
            )

        # address
        address: Optional[int] = None
        addr_str = (raw.get("address") or "").strip()
        try:
            address = int(addr_str)
            if address < 0:
                errors.append(SignalError(row, "address", "address must be non-negative"))
                address = None
        except ValueError:
            errors.append(SignalError(row, "address", f"address '{addr_str}' is not an integer"))

        # bit_index: required for a bool on holding/input (bit within a 16-bit word);
        # must be empty for bool on coil/discrete_input (each coil is its own address)
        # and for all non-bool types.
        bit_index: Optional[int] = None
        bit_str = (raw.get("bit_index") or "").strip()
        bool_in_word = data_type == "bool" and register_type in ("holding", "input")
        if bool_in_word:
            if bit_str == "":
                errors.append(
                    SignalError(row, "bit_index", f"missing for bool signal '{name}'")
                )
            else:
                try:
                    bit_index = int(bit_str)
                    if not (0 <= bit_index <= 15):
                        errors.append(SignalError(row, "bit_index", "must be 0-15"))
                        bit_index = None
                except ValueError:
                    errors.append(SignalError(row, "bit_index", f"'{bit_str}' is not an integer"))
        elif bit_str != "":
            errors.append(
                SignalError(row, "bit_index",
                            "must be empty for non-bool types and for coil/discrete_input bools")
            )

        # word_order (required iff wide type)
        word_order: Optional[str] = None
        wo_str = (raw.get("word_order") or "").strip()
        if data_type in WIDE_TYPES:
            if wo_str not in WORD_ORDERS:
                errors.append(
                    SignalError(row, "word_order",
                                f"required for {data_type}; must be big_endian or little_endian")
                )
            else:
                word_order = wo_str
        elif wo_str not in ("", *WORD_ORDERS):
            errors.append(
                SignalError(row, "word_order", f"'{wo_str}' is not a valid word_order")
            )
        elif wo_str in WORD_ORDERS:
            word_order = wo_str

        # scale
        scale = 1.0
        scale_str = (raw.get("scale") or "").strip()
        if scale_str:
            try:
                scale = float(scale_str)
            except ValueError:
                errors.append(SignalError(row, "scale", f"'{scale_str}' is not a number"))

        # default_value
        default_value: float = 0.0
        dv_str = (raw.get("default_value") or "").strip()
        if dv_str:
            try:
                default_value = float(dv_str)
            except ValueError:
                errors.append(
                    SignalError(row, "default_value", f"'{dv_str}' is not a number")
                )
        if data_type == "bool" and default_value not in (0.0, 1.0):
            errors.append(SignalError(row, "default_value", "bool default must be 0 or 1"))

        # Optional simulation columns (absent in legacy CSVs => empty / inherit).
        sim_mode = (raw.get("sim_mode") or "").strip()
        if sim_mode and sim_mode not in VALID_SIM_MODES:
            errors.append(
                SignalError(row, "sim_mode", f"'{sim_mode}' is not a valid sim_mode")
            )

        def _opt_float(col: str) -> Optional[float]:
            sv = (raw.get(col) or "").strip()
            if sv == "":
                return None
            try:
                return float(sv)
            except ValueError:
                errors.append(SignalError(row, col, f"'{sv}' is not a number"))
                return None

        sim_min = _opt_float("sim_min")
        sim_max = _opt_float("sim_max")
        sim_period = _opt_float("sim_period")
        sim_step = _opt_float("sim_step")

        # Overlap / bit-uniqueness checks (only when address + types are valid).
        if address is not None and data_type in DATA_TYPES and register_type in REGISTER_TYPES:
            if bool_in_word and bit_index is not None:
                # Multiple bools may share a holding/input word with distinct bits.
                key = (register_type, address)
                used = bool_bits.setdefault(key, set())
                if bit_index in used:
                    errors.append(
                        SignalError(row, "bit_index",
                                    f"address {address} bit {bit_index} already used")
                    )
                used.add(bit_index)
            else:
                # Non-bool spans 1-2 registers; coil/discrete bool owns one address.
                span = 2 if data_type in WIDE_TYPES else 1
                for off in range(span):
                    key = (register_type, address + off)
                    if key in occupied:
                        errors.append(
                            SignalError(row, "address",
                                        f"register {address + off} ({register_type}) "
                                        f"already used by '{occupied[key]}'")
                        )
                    occupied[key] = name

        if errors:
            # Keep scanning to collect all errors, but don't build the object.
            continue

        default_raw = _compute_default_raw(data_type, default_value)
        signals.append(
            Signal(
                name=name,
                register_type=register_type,
                address=address,
                data_type=data_type,
                bit_index=bit_index,
                word_order=word_order,
                scale=scale,
                unit=(raw.get("unit") or "").strip(),
                section=(raw.get("section") or "").strip() or "General",
                description=(raw.get("description") or "").strip(),
                default_value=default_value,
                writable=_to_bool(raw.get("writable") or ""),
                sim_mode=sim_mode,
                sim_min=sim_min,
                sim_max=sim_max,
                sim_period=sim_period,
                sim_step=sim_step,
                default_raw=default_raw,
            )
        )

    if errors:
        return [], errors
    return signals, []


def _compute_default_raw(data_type: str, default_value: float) -> int:
    """Convert the CSV engineering default to its raw integer representation."""
    if data_type == "float32":
        return _float_to_raw_u32(default_value)
    if data_type == "bool":
        return 1 if default_value else 0
    iv = int(default_value)
    if data_type == "int16":
        return iv & 0xFFFF
    if data_type == "uint16":
        return iv & 0xFFFF
    if data_type == "int32":
        return iv & 0xFFFFFFFF
    if data_type == "uint32":
        return iv & 0xFFFFFFFF
    return iv


def signals_to_csv(signals: list[Signal]) -> str:
    """Serialize a Signal list back to the canonical CSV text (for download / disk)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FULL_HEADER)
    for s in signals:
        writer.writerow([
            s.name,
            s.register_type,
            s.address,
            s.data_type,
            "" if s.bit_index is None else s.bit_index,
            s.word_order or "",
            _fmt_num(s.scale),
            s.unit,
            s.section,
            s.description,
            _fmt_num(s.default_value),
            str(s.writable).lower(),
            s.sim_mode or "",
            "" if s.sim_min is None else _fmt_num(s.sim_min),
            "" if s.sim_max is None else _fmt_num(s.sim_max),
            "" if s.sim_period is None else _fmt_num(s.sim_period),
            "" if s.sim_step is None else _fmt_num(s.sim_step),
        ])
    return out.getvalue()


def _fmt_num(value: float) -> str:
    """Render a number without a trailing .0 when it is integral."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _blank_if_none(value) -> str:
    """Empty string for unset optional fields; otherwise the value as-is."""
    return "" if value is None or value == "" else value


def signals_from_json(rows: list[dict]) -> str:
    """Convert a JSON signal list (web-UI editor / hot-reload body) to CSV text.

    The CSV is then run through ``parse_and_validate`` so JSON and file uploads
    share exactly one validation path.
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FULL_HEADER)
    for r in rows:
        bit = r.get("bit_index")
        writer.writerow([
            r.get("name", ""),
            r.get("register_type", ""),
            r.get("address", ""),
            r.get("data_type", ""),
            "" if bit is None else bit,
            r.get("word_order") or "",
            r.get("scale", ""),
            r.get("unit", ""),
            r.get("section", ""),
            r.get("description", ""),
            r.get("default_value", ""),
            r.get("writable", False),
            r.get("sim_mode") or "",
            _blank_if_none(r.get("sim_min")),
            _blank_if_none(r.get("sim_max")),
            _blank_if_none(r.get("sim_period")),
            _blank_if_none(r.get("sim_step")),
        ])
    return out.getvalue()


def signal_to_dict(s: Signal) -> dict:
    """JSON representation matching the CSV schema (for GET /signals)."""
    return {
        "name": s.name,
        "register_type": s.register_type,
        "address": s.address,
        "data_type": s.data_type,
        "bit_index": s.bit_index,
        "word_order": s.word_order,
        "scale": s.scale,
        "unit": s.unit,
        "section": s.section,
        "description": s.description,
        "default_value": s.default_value,
        "writable": s.writable,
        "sim_mode": s.sim_mode,
        "sim_min": s.sim_min,
        "sim_max": s.sim_max,
        "sim_period": s.sim_period,
        "sim_step": s.sim_step,
    }
