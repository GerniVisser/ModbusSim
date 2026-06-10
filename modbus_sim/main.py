"""Engine entry point — CLI, lifecycle, banners (REQUIREMENTS.md sections 3, 13).

Starts the Flask REST API (main thread) in SETUP state. The Modbus servers run on a
dedicated asyncio loop in a daemon thread, created by the state machine when the user
triggers the SETUP -> RUNNING transition via the API. Network interfaces are never
torn down (the VM snapshot is the cleanup mechanism).

Usage::

    sudo python3 -m modbus_sim.main [--headless] [--config DIR] [--reset] [--port N]
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import threading
from pathlib import Path

from . import config_loader
from .api_server import create_app
from .network_manager import NetworkManager
from .state_machine import CONFIG_FILENAME, LOCK_FILENAME, StateMachine

DEFAULT_PROJECT_DIR = "./project"
DEFAULT_PORT = 5000


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="modbus_sim", description="Modbus TCP simulator engine")
    parser.add_argument("--headless", action="store_true", help="Run engine without serving the web UI")
    parser.add_argument("--config", default=DEFAULT_PROJECT_DIR, help="Project directory (default: ./project)")
    parser.add_argument("--reset", action="store_true", help="Clear a locked project directory and start fresh")
    parser.add_argument("--no-network", action="store_true",
                        help="Skip VLAN/IP setup; bind servers to already-present IPs "
                             "(loopback/management) — for testing without the USB-C NIC")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Web/API port (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address for the API (default: 0.0.0.0)")
    parser.add_argument("--restore", action="store_true",
                        help="Re-load an existing locked project from disk and go straight to RUNNING "
                             "(crash-recovery; skips the lock-file guard)")
    parser.add_argument("--internal-port", type=int, default=None,
                        help="Bind Flask to localhost:PORT (subprocess mode used by gui_server)")
    return parser.parse_args(argv)


def _teardown_old_network(project_dir: Path) -> None:
    """Read the locked config and tear down any interfaces it created.

    Called before wiping the project directory on --reset, so that a crash
    followed by a restart with --reset leaves the kernel network state clean.
    Silently skipped if the config file is missing or unparseable.
    """
    config_file = project_dir / CONFIG_FILENAME
    if not config_file.exists():
        return
    try:
        text = config_file.read_text(encoding="utf-8")
        cfg, errors = config_loader.load_and_validate(text)
        if errors or cfg is None:
            return
        NetworkManager(cfg).teardown()
        print("--reset: removed network interfaces from previous run.")
    except Exception:
        pass


def enforce_lock(project_dir: Path, reset: bool, manage_network: bool = True) -> None:
    """Refuse to start if a locked config exists (snapshot-revert workflow, §3)."""
    lock = project_dir / LOCK_FILENAME
    if lock.exists():
        if not reset:
            print(
                "\nERROR: a locked config already exists in "
                f"'{project_dir}'.\n"
                "This VM session already loaded a project. To load a different "
                "config, revert the VM to the 'Simulator Clean State' snapshot.\n"
                "(For development you may pass --reset to clear it.)\n",
                file=sys.stderr,
            )
            sys.exit(1)
        # Tear down old network interfaces before wiping files, so that
        # a crash + --reset restart leaves the kernel state clean.
        if manage_network:
            _teardown_old_network(project_dir)
        # --reset: wipe the project directory back to empty.
        for child in project_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        print(f"--reset: cleared project directory '{project_dir}'.")


def setup_banner(host: str, port: int, headless: bool) -> str:
    ui = "disabled (headless)" if headless else f"http://{host}:{port}"
    return (
        "\n"
        "+--------------------------------------------------------------+\n"
        "|  Generic Modbus TCP Simulator - Engine ready (SETUP)         |\n"
        "+--------------------------------------------------------------+\n"
        f"|  State   : SETUP (no simulation running)                     \n"
        f"|  Web UI  : {ui}\n"
        f"|  New cfg : upload sim_config.yaml via the API/UI to begin    \n"
        "+--------------------------------------------------------------+\n"
    )


def running_banner(engine: StateMachine) -> None:
    cfg = engine.config
    net = engine.network_state()
    vlan = "enabled" if cfg.is_vlan_mode else "disabled"
    lines = [
        "\n+----------------------------------------------------------------------+",
        "|  Generic Modbus TCP Simulator - RUNNING                              |",
        "+----------------------------------------------------------------------+",
        f"|  Project   : {cfg.project_name}",
        f"|  VLAN mode : {vlan} ({cfg.traffic_interface})",
        "+----------------------------------------------------------------------+",
        "|  Device              IP              Port  Unit  VLAN  Signals",
    ]
    for d in engine.get_devices():
        lines.append(
            f"|  {d['id']:<18.18} {d['ip']:<15} {d['port']:<5} {d['unit_id']:<4} "
            f"{d['vlan']:<5} {d['signal_count']}"
        )
    lines.append("+----------------------------------------------------------------------+")
    print("\n".join(lines))


def restore_banner(host: str, port: int, headless: bool) -> str:
    ui = "disabled (headless)" if headless else f"http://{host}:{port}"
    return (
        "\n"
        "+--------------------------------------------------------------+\n"
        "|  Generic Modbus TCP Simulator - RESTORED (RUNNING)           |\n"
        "+--------------------------------------------------------------+\n"
        f"|  State   : RUNNING (restored from disk)                      \n"
        f"|  Web UI  : {ui}\n"
        f"|  Tip     : revert the VM snapshot to start with a new config \n"
        "+--------------------------------------------------------------+\n"
    )


def _restore_from_disk(engine: StateMachine) -> bool:
    """Re-upload config + signals from the project directory and start the engine.

    Returns True if the engine reached RUNNING. On any recoverable failure (no/invalid
    config, a missing NIC, a bind error, …) it does NOT exit the process: it logs the
    reason and returns False so the caller keeps serving the web UI in SETUP. That way a
    correctable problem like the USB-C adapter not being plugged in surfaces in the UI
    (via setup_status' ``start_error``) instead of the engine silently dying.
    """
    project_dir = engine.project_dir
    config_file = project_dir / CONFIG_FILENAME
    if not config_file.exists():
        print(f"--restore: {config_file} not found; starting in SETUP.", file=sys.stderr)
        return False

    config_bytes = config_file.read_bytes()
    try:
        engine.config_locked = False
        result = engine.upload_config(config_bytes)
    except Exception as exc:
        print(f"--restore: config load failed: {exc}; starting in SETUP.", file=sys.stderr)
        return False

    print(f"--restore: loaded config ({result['device_count']} device(s))")

    for dev in engine.config.devices:
        sig_path = project_dir / dev.signals_file
        if not sig_path.exists():
            print(f"--restore: signal file {sig_path} not found; skipping.", file=sys.stderr)
            continue
        try:
            engine.upload_signals(dev.id, sig_path.read_bytes())
            print(f"--restore: loaded signals for '{dev.id}'")
        except Exception as exc:
            print(f"--restore: signals for '{dev.id}' failed: {exc}; starting in SETUP.",
                  file=sys.stderr)
            return False

    try:
        engine.start()
    except Exception as exc:
        print(f"--restore: start failed: {exc}", file=sys.stderr)
        print("--restore: engine staying in SETUP — fix the issue (e.g. plug in the "
              "network adapter) and press Start in the web UI.", file=sys.stderr)
        return False
    return True


def _schedule_exit() -> None:
    """Exit the process shortly after responding to POST /api/stop."""
    threading.Timer(0.4, lambda: os._exit(0)).start()


def main(argv=None) -> None:
    args = parse_args(argv)
    project_dir = Path(args.config)
    project_dir.mkdir(parents=True, exist_ok=True)

    if not args.restore:
        enforce_lock(project_dir, args.reset, manage_network=not args.no_network)

    engine = StateMachine(
        project_dir,
        manage_network=not args.no_network,
        on_stop=_schedule_exit,
        on_started=running_banner,
    )
    app = create_app(engine, headless=args.headless)

    def _on_signal(_signum, _frame):
        engine.stop()
        os._exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    if args.restore:
        if _restore_from_disk(engine):
            print(restore_banner(args.host, args.port, args.headless))
        else:
            # Restore could not reach RUNNING; serve the UI in SETUP so the user can
            # see why (setup_status.start_error) and retry once it is fixed.
            print(setup_banner(args.host, args.port, args.headless))
    else:
        print(setup_banner(args.host, args.port, args.headless))
    # threaded=True so POST /api/stop and concurrent polling work; no reloader.
    _host = "127.0.0.1" if args.internal_port else args.host
    _port = args.internal_port if args.internal_port else args.port
    app.run(host=_host, port=_port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
