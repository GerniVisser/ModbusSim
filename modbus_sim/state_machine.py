"""Engine state machine and orchestration (REQUIREMENTS.md sections 3, 13).

Owns the lifecycle: SETUP -> RUNNING (-> STOPPING). Holds the loaded config, the
per-device signal lists and RegisterMaps, the NetworkManager, and the Modbus servers
(which run on a dedicated asyncio loop in a daemon thread, while Flask runs in the
main thread). The Flask API layer (api_server.py) is a thin translator over the
methods here; all engine state lives in this object.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import config_loader, signal_loader, simulator
from .config_loader import SimConfig
from .modbus_server import ModbusServerManager
from .network_manager import NetworkManager
from .register_map import RegisterMap

SETUP = "SETUP"
RUNNING = "RUNNING"
STOPPING = "STOPPING"

LOCK_FILENAME = ".config_locked"
CONFIG_FILENAME = "sim_config.yaml"


class StateError(Exception):
    """Endpoint called in the wrong engine state (-> HTTP 409)."""

    http_status = 409

    def __init__(self, message: str, current_state: str):
        super().__init__(message)
        self.message = message
        self.current_state = current_state


class ValidationError(Exception):
    """Config/signal validation failure (-> HTTP 400). ``errors`` is a list."""

    http_status = 400

    def __init__(self, errors: list):
        super().__init__("validation failed")
        self.errors = errors


class NotFoundError(Exception):
    """Unknown device or signal (-> HTTP 404)."""

    http_status = 404

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class EngineError(Exception):
    """Network/startup failure during transition (-> HTTP 500)."""

    http_status = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _rewrite_traffic_interface(config_path: Path, old: str, new: str) -> None:
    """Replace the traffic_interface value in the YAML file via regex."""
    import re
    text = config_path.read_text(encoding="utf-8")
    updated = re.sub(
        r"(traffic_interface:\s*)" + re.escape(old),
        r"\g<1>" + new,
        text,
    )
    config_path.write_text(updated, encoding="utf-8")


class StateMachine:
    def __init__(
        self,
        project_dir: str | Path,
        *,
        manage_network: bool = True,
        on_stop: Optional[Callable[[], None]] = None,
        on_started: Optional[Callable[["StateMachine"], None]] = None,
    ):
        self.project_dir = Path(project_dir)
        self.devices_dir = self.project_dir / "devices"
        self.manage_network = manage_network
        self._on_stop = on_stop
        self._on_started = on_started

        self.state = SETUP
        self.config: Optional[SimConfig] = None
        self.config_locked = False
        self.started_at: Optional[str] = None
        # Reason the last start attempt failed (surfaced in setup_status so the UI
        # can explain a failed start/auto-restore instead of leaving the user guessing).
        self.start_error: Optional[str] = None

        self._signals: dict[str, list] = {}            # device_id -> list[Signal]
        self._regmaps: dict[str, RegisterMap] = {}      # device_id -> RegisterMap
        self._servers = ModbusServerManager()
        self._network: Optional[NetworkManager] = None
        # Project-wide simulation settings (persisted as simulation.json).
        self._sim_defaults = simulator.load_defaults(self.project_dir)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._serve_future = None
        self._lock = threading.RLock()

    # ----------------------------------------------------------- state guards
    def _require(self, expected: str) -> None:
        if self.state != expected:
            raise StateError(
                f"endpoint not available in {self.state} state", self.state
            )

    # ------------------------------------------------------------- state info
    def state_info(self) -> dict:
        if self.state == SETUP:
            return {
                "state": SETUP,
                "config_locked": self.config_locked,
                "signal_files_loaded": len(self._signals),
                "signal_files_required": len(self.config.devices) if self.config else 0,
            }
        return {
            "state": self.state,
            "config_locked": self.config_locked,
            "started_at": self.started_at,
        }

    # --------------------------------------------------------------- setup ops
    def upload_config(self, file_bytes: bytes) -> dict:
        self._require(SETUP)
        if self.config_locked:
            raise StateError("config already locked for this session", self.state)

        text = file_bytes.decode("utf-8", errors="replace")
        config, errors = config_loader.load_and_validate(text)
        if errors:
            raise ValidationError(errors)

        self.project_dir.mkdir(parents=True, exist_ok=True)
        (self.project_dir / CONFIG_FILENAME).write_text(text, encoding="utf-8")
        (self.project_dir / LOCK_FILENAME).write_text("", encoding="utf-8")
        self.config = config
        self.config_locked = True

        return {
            "ok": True,
            "project_name": config.project_name,
            "device_count": len(config.devices),
            "devices": [
                {
                    "id": d.id,
                    "name": d.name,
                    "signals_file": d.signals_file,
                    "signals_uploaded": d.id in self._signals,
                }
                for d in config.devices
            ],
            "locked": True,
        }

    def upload_signals(self, device_id: str, file_bytes: bytes) -> dict:
        self._require(SETUP)
        if not self.config:
            raise StateError("config not uploaded yet", self.state)
        dev = self.config.device_by_id(device_id)
        if dev is None:
            raise NotFoundError(f"unknown device '{device_id}'")

        text = file_bytes.decode("utf-8", errors="replace")
        signals, errors = signal_loader.parse_and_validate(text)
        if errors:
            raise ValidationError([e.as_dict() for e in errors])

        target = self.project_dir / dev.signals_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self._signals[device_id] = signals

        remaining = [d.id for d in self.config.devices if d.id not in self._signals]
        return {
            "ok": True,
            "device_id": device_id,
            "signal_count": len(signals),
            "all_devices_ready": not remaining,
            "remaining": remaining,
        }

    def setup_status(self) -> dict:
        self._require(SETUP)
        total = len(self.config.devices) if self.config else 0
        pending = (
            [d.id for d in self.config.devices if d.id not in self._signals]
            if self.config else []
        )
        devices = (
            [
                {
                    "id": d.id,
                    "name": d.name,
                    "signals_uploaded": d.id in self._signals,
                }
                for d in self.config.devices
            ]
            if self.config else []
        )
        return {
            "config_uploaded": self.config is not None,
            "config_locked": self.config_locked,
            "traffic_interface": self.config.traffic_interface if self.config else "",
            "devices_total": total,
            "devices_ready": len(self._signals),
            "devices_pending": pending,
            "devices": devices,
            "can_start": self.config is not None and not pending,
            "start_error": self.start_error,
        }

    # --------------------------------------------------------- start transition
    def start(self) -> dict:
        self._require(SETUP)
        if not self.config:
            raise StateError("config not uploaded yet", self.state)
        pending = [d.id for d in self.config.devices if d.id not in self._signals]
        if pending:
            raise StateError(
                f"cannot start: devices missing signal files: {pending}", self.state
            )

        # Fresh attempt: clear any error recorded by a previous failed start.
        self.start_error = None

        # Root precondition (§17): needed for VLAN ops or privileged ports.
        needs_root = self.config.is_vlan_mode or any(
            d.port < 1024 for d in self.config.devices
        )
        if self.manage_network and needs_root and not _is_root():
            self.start_error = (
                "root privileges required (VLAN mode and/or port < 1024). "
                "Run the engine as root."
            )
            raise EngineError(self.start_error)

        # Build register maps from the validated signal lists.
        self._regmaps = {
            d.id: RegisterMap(self._signals[d.id], sim_defaults=self._sim_defaults)
            for d in self.config.devices
        }

        # Network setup (real ip commands on the VM). Errors abort the transition.
        if self.manage_network:
            self._network = NetworkManager(self.config)
            try:
                self._network.setup()
            except RuntimeError as exc:
                self._regmaps = {}
                self.start_error = str(exc)
                raise EngineError(str(exc)) from exc

        # Start the asyncio loop and bind/serve the Modbus servers.
        self._ensure_loop()
        devices = [(d, self._regmaps[d.id]) for d in self.config.devices]
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._servers.bind(devices), self._loop
            )
            fut.result(timeout=15)
        except Exception as exc:  # bind failure
            self._regmaps = {}
            self.start_error = f"failed to start Modbus servers: {exc}"
            raise EngineError(self.start_error) from exc
        self._serve_future = asyncio.run_coroutine_threadsafe(
            self._servers.serve(), self._loop
        )

        self.start_error = None
        self.state = RUNNING
        self.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self._on_started is not None:
            self._on_started(self)
        return {"ok": True, "state": RUNNING, "devices_started": len(devices)}

    # ------------------------------------------------------------- runtime ops
    def _regmap(self, device_id: str) -> RegisterMap:
        self._require(RUNNING)
        regmap = self._regmaps.get(device_id)
        if regmap is None:
            raise NotFoundError(f"unknown device '{device_id}'")
        return regmap

    def get_devices(self) -> list[dict]:
        self._require(RUNNING)
        out = []
        for d in self.config.devices:
            out.append({
                "id": d.id, "name": d.name, "ip": d.ip, "port": d.port,
                "unit_id": d.unit_id, "vlan": d.vlan,
                "signal_count": self._regmaps[d.id].signal_count(),
                "description": d.description, "status": "running",
            })
        return out

    def get_signals(self, device_id: str) -> list[dict]:
        regmap = self._regmap(device_id)
        return [signal_loader.signal_to_dict(s) for s in regmap.signals]

    def get_values(self, device_id: str) -> dict:
        return self._regmap(device_id).get_values_by_name()

    def set_value(self, device_id: str, name: str, value) -> dict:
        regmap = self._regmap(device_id)
        signal = regmap.get_signal(name)
        if signal is None:
            raise NotFoundError(f"unknown signal '{name}'")
        self._validate_value(signal, value)
        regmap.write_signal(signal, value)
        return {"ok": True, "name": name, "raw_value": regmap.read_signal(signal)}

    @staticmethod
    def _validate_value(signal, value) -> None:
        dt = signal.data_type
        if dt == "bool":
            if value not in (True, False, 0, 1):
                raise ValidationError([f"signal '{signal.name}' expects a boolean"])
        elif dt == "float32":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError([f"signal '{signal.name}' expects a number"])
        else:  # integer types
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError([f"signal '{signal.name}' expects an integer"])

    def simulate(self, device_id: str) -> dict:
        regmap = self._regmap(device_id)
        regmap.set_defaults()
        return {"ok": True, "device_id": device_id, "signals_set": regmap.signal_count()}

    def clear(self, device_id: str) -> dict:
        self._regmap(device_id).clear_all()
        return {"ok": True, "device_id": device_id}

    def simulate_all(self) -> dict:
        self._require(RUNNING)
        for regmap in self._regmaps.values():
            regmap.set_defaults()
        return {"ok": True, "devices_updated": len(self._regmaps)}

    def clear_all(self) -> dict:
        self._require(RUNNING)
        for regmap in self._regmaps.values():
            regmap.clear_all()
        return {"ok": True, "devices_updated": len(self._regmaps)}

    # --------------------------------------------------------------- hot reload
    def hot_reload_json(self, device_id: str, signal_rows: list) -> dict:
        csv_text = signal_loader.signals_from_json(signal_rows)
        return self._hot_reload(device_id, csv_text)

    def hot_reload_csv(self, device_id: str, file_bytes: bytes) -> dict:
        return self._hot_reload(device_id, file_bytes.decode("utf-8", errors="replace"))

    def _hot_reload(self, device_id: str, csv_text: str) -> dict:
        self._require(RUNNING)
        dev = self.config.device_by_id(device_id)
        if dev is None or device_id not in self._regmaps:
            raise NotFoundError(f"unknown device '{device_id}'")

        signals, errors = signal_loader.parse_and_validate(csv_text)
        if errors:
            # Reject; on-disk file and live simulation unchanged.
            raise ValidationError([e.as_dict() for e in errors])

        # Persist, rebuild, and atomically swap the runtime.
        (self.project_dir / dev.signals_file).write_text(csv_text, encoding="utf-8")
        new_regmap = RegisterMap(signals, sim_defaults=self._sim_defaults)
        self._servers.hot_reload(device_id, new_regmap)
        self._regmaps[device_id] = new_regmap
        self._signals[device_id] = signals
        return {
            "ok": True, "device_id": device_id,
            "signal_count": len(signals), "reloaded": True,
        }

    def signals_csv(self, device_id: str) -> str:
        regmap = self._regmap(device_id)
        return signal_loader.signals_to_csv(regmap.signals)

    # ------------------------------------------------------------- simulation
    def get_simulation(self) -> dict:
        """Current project-wide simulation settings (available in any state)."""
        return self._sim_defaults.to_dict()

    def set_simulation(self, patch: dict) -> dict:
        """Merge a partial update into the simulation settings, persist, apply live.

        Because values are generated on read, applying a change just rebuilds each
        register map's profiles — no server restart and no signal reload needed.
        """
        merged = {**self._sim_defaults.to_dict(), **(patch or {})}
        new = simulator.SimDefaults.from_dict(merged)
        errors = simulator.validate_defaults(new)
        if errors:
            raise ValidationError(errors)
        self._sim_defaults = new
        self.project_dir.mkdir(parents=True, exist_ok=True)
        simulator.save_defaults(self.project_dir, new)
        for regmap in self._regmaps.values():
            regmap.set_sim_defaults(new)
        return {"ok": True, "simulation": new.to_dict()}

    def set_device_simulation(self, device_id: str, sim_mode: str,
                              section: Optional[str] = None) -> dict:
        """Bulk-apply a per-signal sim_mode to one device (optionally one section)."""
        regmap = self._regmap(device_id)
        if sim_mode not in ("", *signal_loader.VALID_SIM_MODES):
            raise ValidationError([f"'{sim_mode}' is not a valid sim_mode"])
        rows = []
        for s in regmap.signals:
            row = signal_loader.signal_to_dict(s)
            if section is None or s.section == section:
                row["sim_mode"] = sim_mode
            rows.append(row)
        result = self.hot_reload_json(device_id, rows)
        result["sim_mode"] = sim_mode
        return result

    # --------------------------------------------------------- config / network
    def config_summary(self) -> dict:
        self._require(RUNNING)
        return {
            "project_name": self.config.project_name,
            "vlan_mode": self.config.is_vlan_mode,
            "traffic_interface": self.config.traffic_interface,
            "device_count": len(self.config.devices),
            "total_signals": sum(r.signal_count() for r in self._regmaps.values()),
            "web_ui_port": self.config.web_ui_port,
        }

    def network_state(self) -> dict:
        self._require(RUNNING)
        if self._network is None:
            return {
                "traffic_interface": self.config.traffic_interface,
                "vlan_interfaces": [],
                "assigned_ips": [],
                "managed": False,
            }
        return {
            "traffic_interface": self.config.traffic_interface,
            "vlan_interfaces": self._network.vlan_interfaces,
            "assigned_ips": self._network.assigned_ips,
            "managed": True,
        }

    # ------------------------------------------------------------------- loop
    def _ensure_loop(self) -> None:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._run_loop, name="modbus-loop", daemon=True
            )
            self._loop_thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ------------------------------------------------------------------ reset
    def reset(self) -> dict:
        """Stop simulation, tear down network, clear disk + memory, return to SETUP.
        The process stays alive. Requires RUNNING state.
        """
        with self._lock:
            self._require(RUNNING)
            self.state = STOPPING

        # Stop Modbus servers and the asyncio loop (same sequence as stop()).
        if self._loop is not None and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._servers.stop(), self._loop)
                fut.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread is not None:
                self._loop_thread.join(timeout=2)

        # Network teardown.
        if self.manage_network and self._network is not None:
            self._network.teardown()

        # Delete persisted files.
        for fname in (LOCK_FILENAME, CONFIG_FILENAME, simulator.SIM_FILENAME):
            try:
                (self.project_dir / fname).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            if self.devices_dir.exists():
                shutil.rmtree(self.devices_dir)
        except Exception:
            pass

        # Reset in-memory state.
        self.config = None
        self.config_locked = False
        self.started_at = None
        self._signals = {}
        self._regmaps = {}
        self._sim_defaults = simulator.SimDefaults()
        self._servers = ModbusServerManager()
        self._network = None
        self._loop = None
        self._loop_thread = None
        self._serve_future = None

        self.state = SETUP
        return {"ok": True, "message": "Engine reset to SETUP"}

    # -------------------------------------------------- change traffic interface
    def change_traffic_interface(self, new_interface: str) -> dict:
        """Hot-swap the Modbus traffic NIC. Causes a brief service outage."""
        with self._lock:
            self._require(RUNNING)

        # Stop Modbus servers — keep the asyncio loop alive.
        if self._loop is not None and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._servers.stop(), self._loop)
                fut.result(timeout=5)
            except Exception:
                pass

        # Teardown old network interfaces.
        if self.manage_network and self._network is not None:
            self._network.teardown()
            self._network = None

        # Update config in memory and rewrite the YAML on disk.
        old_interface = self.config.traffic_interface
        self.config.traffic_interface = new_interface
        _rewrite_traffic_interface(
            self.project_dir / CONFIG_FILENAME, old_interface, new_interface
        )

        # Setup network on the new interface.
        if self.manage_network:
            self._network = NetworkManager(self.config)
            try:
                self._network.setup()
            except RuntimeError as exc:
                self.config.traffic_interface = old_interface  # rollback memory
                raise EngineError(str(exc)) from exc

        # Restart Modbus servers on the still-running asyncio loop.
        self._servers = ModbusServerManager()
        devices = [(d, self._regmaps[d.id]) for d in self.config.devices]
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._servers.bind(devices), self._loop
            )
            fut.result(timeout=15)
        except Exception as exc:
            raise EngineError(f"failed to restart Modbus servers: {exc}") from exc
        self._serve_future = asyncio.run_coroutine_threadsafe(
            self._servers.serve(), self._loop
        )

        return {"ok": True, "old_interface": old_interface, "new_interface": new_interface}

    # ------------------------------------------------------------------- stop
    def stop(self) -> dict:
        self.state = STOPPING
        if self._loop is not None and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._servers.stop(), self._loop)
                fut.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self.manage_network and self._network is not None:
            self._network.teardown()
        if self._on_stop is not None:
            self._on_stop()
        return {"ok": True, "message": "Engine shutting down"}
