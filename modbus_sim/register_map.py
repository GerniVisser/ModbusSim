"""Per-device thread-safe register store (REQUIREMENTS.md section 9).

``RegisterMap`` is the single source of truth for one Modbus device's register
values. Both the Modbus server (via a slave context wrapper) and the Flask API
read and write through the same instance, so it must be thread-safe.

Storage model:
- ``holding`` / ``input`` blocks store 16-bit words keyed by address. A ``bool``
  signal on these tables addresses a single ``bit_index`` within a word.
- ``coil`` / ``discrete_input`` blocks store one bit (0/1) per address; a bool
  signal there owns the whole coil and has no ``bit_index``.

Undefined addresses inside a block read back as 0. Reads outside the block range
are rejected by ``in_range`` so the server returns Modbus exception 02.
"""

from __future__ import annotations

import struct
import threading
from typing import Optional

from .signal_loader import Signal, WIDE_TYPES

REGISTER_TYPES = ("holding", "input", "coil", "discrete_input")


class RegisterMap:
    def __init__(self, signals: list[Signal]):
        self._signals = list(signals)
        self._by_name: dict[str, Signal] = {s.name: s for s in signals}
        self._lock = threading.RLock()
        # One sparse store per register type: {address: value}.
        self._store: dict[str, dict[int, int]] = {rt: {} for rt in REGISTER_TYPES}
        # Inclusive [min, max] address bounds per register type.
        self._bounds: dict[str, tuple[int, int]] = {}
        self._compute_bounds()
        self.set_defaults()

    # ------------------------------------------------------------------ setup
    def _compute_bounds(self) -> None:
        addrs: dict[str, set[int]] = {rt: set() for rt in REGISTER_TYPES}
        for s in self._signals:
            addrs[s.register_type].add(s.address)
            if s.register_span == 2:
                addrs[s.register_type].add(s.address + 1)
        for rt in REGISTER_TYPES:
            used = addrs[rt]
            if used:
                self._bounds[rt] = (min(used), max(used))
            else:
                # Minimal single-register block at address 0 (section 9).
                self._bounds[rt] = (0, 0)

    # --------------------------------------------------------------- signals
    @property
    def signals(self) -> list[Signal]:
        return list(self._signals)

    def signal_count(self) -> int:
        return len(self._signals)

    def get_signal(self, name: str) -> Optional[Signal]:
        return self._by_name.get(name)

    # ----------------------------------------------------------- raw register
    def in_range(self, register_type: str, address: int, count: int) -> bool:
        lo, hi = self._bounds[register_type]
        return address >= lo and (address + count - 1) <= hi and address >= 0

    def read_block(self, register_type: str, address: int, count: int) -> list[int]:
        with self._lock:
            store = self._store[register_type]
            return [store.get(address + i, 0) for i in range(count)]

    def write_block(self, register_type: str, address: int, values: list[int]) -> None:
        with self._lock:
            store = self._store[register_type]
            for i, v in enumerate(values):
                store[address + i] = int(v) & 0xFFFF

    def read_coil(self, register_type: str, address: int) -> bool:
        """Read a single coil/discrete-input bit (one bit per address)."""
        with self._lock:
            return bool(self._read_raw(register_type, address) & 1)

    def write_coil(self, register_type: str, address: int, value: bool) -> None:
        """Write a single coil/discrete-input bit (one bit per address)."""
        with self._lock:
            self._write_raw(register_type, address, 1 if value else 0)

    def _read_raw(self, register_type: str, address: int) -> int:
        return self._store[register_type].get(address, 0)

    def _write_raw(self, register_type: str, address: int, value: int) -> None:
        self._store[register_type][address] = int(value) & 0xFFFF

    # --------------------------------------------------------- typed access
    def read_signal(self, signal: Signal):
        """Return the typed engineering-domain value (raw register interpretation)."""
        rt, addr, dt = signal.register_type, signal.address, signal.data_type
        with self._lock:
            if dt == "uint16":
                return self._read_raw(rt, addr)
            if dt == "int16":
                raw = self._read_raw(rt, addr)
                return raw - 65536 if raw >= 32768 else raw
            if dt in WIDE_TYPES:
                u32 = self._read_u32(rt, addr, signal.word_order)
                if dt == "uint32":
                    return u32
                if dt == "int32":
                    return u32 - 2**32 if u32 >= 2**31 else u32
                # float32
                return struct.unpack(">f", struct.pack(">I", u32))[0]
            # bool
            if rt in ("coil", "discrete_input"):
                return bool(self._read_raw(rt, addr) & 1)
            word = self._read_raw(rt, addr)
            return bool((word >> (signal.bit_index or 0)) & 1)

    def write_signal(self, signal: Signal, value) -> None:
        """Write a typed value, converting to raw register representation."""
        self._store_raw(signal, self._typed_to_raw(signal, value))

    def _read_u32(self, register_type: str, address: int, word_order: Optional[str]) -> int:
        w0 = self._read_raw(register_type, address)
        w1 = self._read_raw(register_type, address + 1)
        if word_order == "little_endian":
            return (w1 << 16) | w0
        # big_endian (default): high word at the lower address
        return (w0 << 16) | w1

    def _typed_to_raw(self, signal: Signal, value) -> int:
        dt = signal.data_type
        if dt == "uint16":
            return int(value) & 0xFFFF
        if dt == "int16":
            v = int(value)
            return (v + 65536 if v < 0 else v) & 0xFFFF
        if dt == "uint32":
            return int(value) & 0xFFFFFFFF
        if dt == "int32":
            v = int(value)
            return (v + 2**32 if v < 0 else v) & 0xFFFFFFFF
        if dt == "float32":
            return struct.unpack(">I", struct.pack(">f", float(value)))[0]
        # bool
        return 1 if value else 0

    def _store_raw(self, signal: Signal, raw: int) -> None:
        """Store a raw integer (16 or 32 bit) or bit into the right register(s)."""
        rt, addr, dt = signal.register_type, signal.address, signal.data_type
        with self._lock:
            if dt in WIDE_TYPES:
                high = (raw >> 16) & 0xFFFF
                low = raw & 0xFFFF
                if signal.word_order == "little_endian":
                    self._write_raw(rt, addr, low)
                    self._write_raw(rt, addr + 1, high)
                else:  # big_endian
                    self._write_raw(rt, addr, high)
                    self._write_raw(rt, addr + 1, low)
            elif dt == "bool":
                if rt in ("coil", "discrete_input"):
                    self._write_raw(rt, addr, 1 if raw else 0)
                else:
                    # Atomic read-modify-write of a single bit in a 16-bit word.
                    word = self._read_raw(rt, addr)
                    bit = signal.bit_index or 0
                    if raw:
                        word |= (1 << bit)
                    else:
                        word &= ~(1 << bit)
                    self._write_raw(rt, addr, word)
            else:  # uint16 / int16
                self._write_raw(rt, addr, raw)

    # --------------------------------------------------------- bulk operations
    def set_defaults(self) -> None:
        """Load each signal's default_value into its register(s) (Simulate)."""
        with self._lock:
            for s in self._signals:
                self._store_raw(s, s.default_raw)

    def clear_all(self) -> None:
        """Zero every register in all four blocks (Clear)."""
        with self._lock:
            for rt in REGISTER_TYPES:
                self._store[rt].clear()

    def get_values_by_name(self) -> dict[str, object]:
        """Map of signal name -> current typed value (for GET /values)."""
        with self._lock:
            return {s.name: self.read_signal(s) for s in self._signals}
