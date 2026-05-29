"""pymodbus 3.13 async TCP servers (REQUIREMENTS.md sections 10, 17).

pymodbus 3.11+ replaced the classic ``ModbusSlaveContext`` (with overridable
``getValues``/``setValues``) by a ``SimData``/``SimDevice`` model. We target that
new API while keeping the spec's intent: ``RegisterMap`` stays the single source of
truth shared with Flask. The bridge is ``SimDevice.action`` — an async callback the
server runs on every request with the live register list:

- On a READ (``values is None``) we populate the requested register slice from the
  device's ``RegisterMap`` so the response reflects current state.
- On a WRITE we push the incoming values into the ``RegisterMap``.

In this model the request address maps 1:1 to our CSV address (block ``start_address``
is subtracted), so the historical pymodbus +1 offset (§17) does not apply here.

Devices sharing an ``(ip, port)`` are served by one TCP server with a ``SimDevice``
per ``unit_id`` (§10 grouping). Hot reload rebuilds a device's ``SimDevice`` and swaps
the runtime in the server's ``SimCore`` without restarting the server or dropping
client connections.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
from pymodbus.simulator.simruntime import SimRuntime

from .config_loader import DeviceConfig
from .register_map import RegisterMap
from .signal_loader import Signal

# Modbus function code -> our register-type name.
FC_TO_RT = {
    1: "coil", 5: "coil", 15: "coil",
    2: "discrete_input",
    3: "holding", 6: "holding", 16: "holding", 22: "holding", 23: "holding",
    4: "input",
}
BIT_FCS = {1, 2, 5, 15}


def _addr_bounds(signals: list[Signal], register_type: str) -> tuple[int, int]:
    """Return inclusive (min, max) address used by a register type, or (0, 0)."""
    used: set[int] = set()
    for s in signals:
        if s.register_type != register_type:
            continue
        used.add(s.address)
        if s.register_span == 2:
            used.add(s.address + 1)
    return (min(used), max(used)) if used else (0, 0)


def _make_action(regmap: RegisterMap):
    """Build the async per-request bridge between pymodbus and a RegisterMap."""

    async def action(func_code, start_address, address, count, registers, values):
        register_type = FC_TO_RT.get(func_code)
        if register_type is None:
            return ExcCodes.ILLEGAL_FUNCTION

        if func_code in BIT_FCS:
            # Bit block: ``address`` is the bit address, ``count`` is a register
            # count, ``registers`` holds 16 bits each (bit 0 = LSB).
            offset = (address // 16) - start_address
            if offset < 0:
                return ExcCodes.ILLEGAL_ADDRESS
            if values is None:
                for r in range(count):
                    base_bit = (start_address + offset + r) * 16
                    word = 0
                    for b in range(16):
                        if regmap.read_coil(register_type, base_bit + b):
                            word |= (1 << b)
                    registers[offset + r] = word
            else:
                for i, v in enumerate(values):
                    regmap.write_coil(register_type, address + i, bool(v))
            return None

        # Register block: ``address`` is a register address, ``count`` a register count.
        offset = address - start_address
        if offset < 0:
            return ExcCodes.ILLEGAL_ADDRESS
        if values is None:
            words = regmap.read_block(register_type, address, count)
            for i, w in enumerate(words):
                registers[offset + i] = w
        else:
            regmap.write_block(register_type, address, [int(v) for v in values])
        return None

    return action


def build_sim_device(cfg: DeviceConfig, regmap: RegisterMap) -> SimDevice:
    """Construct a SimDevice (4 non-shared blocks) backed by ``regmap``.

    Each block is one contiguous SimData spanning the addresses its register type
    uses, so every in-range address is valid and out-of-range reads raise
    exception 02. The ``action`` keeps the real values in ``regmap``.
    """
    signals = regmap.signals

    def reg_block(register_type: str, readonly: bool) -> list[SimData]:
        lo, hi = _addr_bounds(signals, register_type)
        return [SimData(address=lo, count=(hi - lo + 1), values=0,
                        datatype=DataType.REGISTERS, readonly=readonly)]

    def bit_block(register_type: str, readonly: bool) -> list[SimData]:
        lo, hi = _addr_bounds(signals, register_type)
        return [SimData(address=lo, count=(hi - lo + 1), values=False,
                        datatype=DataType.BITS, readonly=readonly)]

    simdata = (
        bit_block("coil", readonly=False),            # coils (FC1/5/15)
        bit_block("discrete_input", readonly=True),   # discrete inputs (FC2)
        reg_block("holding", readonly=False),         # holding registers (FC3/6/16)
        reg_block("input", readonly=True),            # input registers (FC4)
    )
    return SimDevice(
        id=cfg.unit_id,
        simdata=simdata,
        use_bit_addressing=True,
        action=_make_action(regmap),
    )


class ModbusServerManager:
    """Owns one TCP server per (ip, port); supports hot reload of a device."""

    def __init__(self):
        self._servers: dict[tuple[str, int], ModbusTcpServer] = {}
        # device_id -> (ip, port, unit_id)
        self._index: dict[str, tuple[str, int, int]] = {}
        self._cfgs: dict[str, DeviceConfig] = {}

    async def bind(self, devices: list[tuple[DeviceConfig, RegisterMap]]) -> None:
        """Build servers and start listening. Returns once all sockets are bound.

        Must be awaited on the asyncio loop (constructing a server needs a running
        loop). Groups devices sharing an (ip, port) into one server. Raises
        RuntimeError if any socket fails to bind so the caller can report it.
        """
        groups: dict[tuple[str, int], list[tuple[DeviceConfig, RegisterMap]]] = {}
        for cfg, regmap in devices:
            groups.setdefault((cfg.ip, cfg.port), []).append((cfg, regmap))
            self._index[cfg.id] = (cfg.ip, cfg.port, cfg.unit_id)
            self._cfgs[cfg.id] = cfg

        for (ip, port), members in groups.items():
            sim_devices = [build_sim_device(cfg, regmap) for cfg, regmap in members]
            server = ModbusTcpServer(context=sim_devices, address=(ip, port))
            if not await server.listen():
                raise RuntimeError(f"could not bind Modbus server to {ip}:{port}")
            self._servers[(ip, port)] = server

    async def serve(self) -> None:
        """Keep all bound servers alive until shutdown. Call after ``bind``."""
        await asyncio.gather(*(s.serving for s in self._servers.values()))

    def hot_reload(self, device_id: str, new_regmap: RegisterMap) -> None:
        """Atomically swap a device's runtime to use ``new_regmap``.

        Rebuilds the SimDevice (so changed address ranges are honoured) and replaces
        the SimRuntime in the server's SimCore. Other devices and open TCP
        connections are unaffected. Assigning the dict entry is a single reference
        swap, safe under the GIL relative to in-flight requests.
        """
        ip, port, unit_id = self._index[device_id]
        server = self._servers[(ip, port)]
        new_device = build_sim_device(self._cfgs[device_id], new_regmap)
        server.context.devices[unit_id] = SimRuntime(new_device)

    async def stop(self) -> None:
        for server in self._servers.values():
            await server.shutdown()

    @property
    def server_count(self) -> int:
        return len(self._servers)
