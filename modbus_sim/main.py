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

from .api_server import create_app
from .state_machine import LOCK_FILENAME, StateMachine

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
    return parser.parse_args(argv)


def enforce_lock(project_dir: Path, reset: bool) -> None:
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


def _schedule_exit() -> None:
    """Exit the process shortly after responding to POST /api/stop."""
    threading.Timer(0.4, lambda: os._exit(0)).start()


def main(argv=None) -> None:
    args = parse_args(argv)
    project_dir = Path(args.config)
    project_dir.mkdir(parents=True, exist_ok=True)

    enforce_lock(project_dir, args.reset)

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

    print(setup_banner(args.host, args.port, args.headless))
    # threaded=True so POST /api/stop and concurrent polling work; no reloader.
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
