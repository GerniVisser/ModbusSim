"""GUI process manager — runs independently, survives engine restarts and crashes.

Listens on port 80 (all interfaces) so the Windows host can reach the UI at
http://192.168.99.2/. Manages the engine (modbus_sim.main) as a subprocess and
proxies /api/* to the engine's internal Flask API on localhost:5001. Exposes
/control/* endpoints for start/stop/restart and log viewing.
"""

from __future__ import annotations

import argparse
import collections
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory

WEBUI_DIR = Path(__file__).parent / "webui"
ENGINE_MODULE = "modbus_sim.main"
ENGINE_INTERNAL_PORT = 5001
ENGINE_STARTUP_TIMEOUT = 20  # seconds to wait for /api/health after spawn
LOG_RING_SIZE = 500

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_CRASHED = "crashed"

# Imported from state_machine to stay in sync.
LOCK_FILENAME = ".config_locked"


class EngineProcess:
    """Manages the engine subprocess and captures its stdout/stderr."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self._proc: Optional[subprocess.Popen] = None
        self._state = STATE_STOPPED
        self._exit_code: Optional[int] = None
        self._log: collections.deque = collections.deque(maxlen=LOG_RING_SIZE)
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._proc is None:
                return self._state
            rc = self._proc.poll()
            if rc is None:
                return STATE_RUNNING if self._state != STATE_STARTING else STATE_STARTING
            # Process has exited since last check.
            if self._state not in (STATE_STOPPED,):
                self._state = STATE_CRASHED if rc != 0 else STATE_STOPPED
                self._exit_code = rc
                self._proc = None
            return self._state

    def start(self) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return {"ok": False, "error": "engine already running"}
            lock_file = self.project_dir / LOCK_FILENAME
            restore = lock_file.exists()
            cmd = [
                sys.executable, "-m", ENGINE_MODULE,
                "--config", str(self.project_dir),
                "--internal-port", str(ENGINE_INTERNAL_PORT),
            ]
            if restore:
                cmd.append("--restore")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._state = STATE_STARTING
            self._exit_code = None
            threading.Thread(
                target=self._read_output, daemon=True, name="engine-log-reader"
            ).start()

        threading.Thread(
            target=self._wait_for_health, daemon=True, name="engine-health-waiter"
        ).start()
        return {"ok": True, "restore": restore}

    def stop(self) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return {"ok": False, "error": "engine not running"}
            try:
                self._proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            self._state = STATE_STOPPED
        return {"ok": True}

    def restart(self) -> dict:
        self.stop()
        # Brief pause to allow the engine process to exit and release sockets.
        time.sleep(1.5)
        return self.start()

    def status(self) -> dict:
        s = self.state  # property call updates _state on exit
        with self._lock:
            return {
                "state": s,
                "pid": self._proc.pid if self._proc else None,
                "exit_code": self._exit_code,
                "log": list(self._log),
            }

    def _read_output(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            with self._lock:
                self._log.append(line.rstrip("\n"))
        rc = proc.wait()
        with self._lock:
            if self._state in (STATE_STARTING, STATE_RUNNING):
                self._state = STATE_CRASHED if rc != 0 else STATE_STOPPED
                self._exit_code = rc
                self._proc = None

    def _wait_for_health(self) -> None:
        url = f"http://127.0.0.1:{ENGINE_INTERNAL_PORT}/api/health"
        deadline = time.monotonic() + ENGINE_STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(url, timeout=1)
                with self._lock:
                    if self._state == STATE_STARTING:
                        self._state = STATE_RUNNING
                return
            except Exception:
                time.sleep(0.4)
        # Timeout reached — leave state as-is; the log reader will handle exit.


def _proxy_request(target_url: str) -> Response:
    """Forward the current Flask request to target_url using urllib."""
    body = request.get_data() or None
    headers = {
        k: v for k, v in request.headers
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }
    req = urllib.request.Request(
        target_url,
        data=body,
        headers=headers,
        method=request.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            resp_headers = dict(resp.headers)
            status = resp.status
    except urllib.error.HTTPError as exc:
        content = exc.read()
        resp_headers = dict(exc.headers)
        status = exc.code
    except urllib.error.URLError as exc:
        return jsonify({"error": f"engine unreachable: {exc.reason}"}), 503

    _SKIP = {"transfer-encoding", "connection", "content-encoding"}
    return Response(
        content,
        status=status,
        headers={k: v for k, v in resp_headers.items() if k.lower() not in _SKIP},
    )


def create_gui_app(engine_proc: EngineProcess) -> Flask:
    app = Flask(__name__)

    # ---------------------------------------------------------------- web UI files
    @app.get("/")
    def index():
        return send_from_directory(WEBUI_DIR, "index.html")

    @app.get("/webui/<path:filename>")
    def webui_static(filename):
        return send_from_directory(WEBUI_DIR, filename)

    # ----------------------------------------------------------- control endpoints
    @app.get("/control/status")
    def control_status():
        return jsonify(engine_proc.status())

    @app.post("/control/start")
    def control_start():
        return jsonify(engine_proc.start())

    @app.post("/control/stop")
    def control_stop():
        return jsonify(engine_proc.stop())

    @app.post("/control/restart")
    def control_restart():
        return jsonify(engine_proc.restart())

    # ------------------------------------------------------------- API proxy
    @app.get("/api/interfaces")
    def api_interfaces_local():
        """Serve interface list directly so it works even when engine is stopped."""
        import json as _json
        import subprocess as _sp
        result = _sp.run(["ip", "-j", "link", "show"], capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify([])
        try:
            raw = _json.loads(result.stdout)
        except _json.JSONDecodeError:
            return jsonify([])
        _EXCLUDE = ("lo", "vmnet", "docker", "veth", "virbr", "br-")
        out = []
        for iface in raw:
            name = iface.get("ifname", "")
            if iface.get("link_type") == "loopback":
                continue
            if any(name.startswith(p) for p in _EXCLUDE):
                continue
            if "." in name:
                continue
            out.append({
                "name": name,
                "mac": iface.get("address", ""),
                "state": iface.get("operstate", "?"),
            })
        return jsonify(out)

    @app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def proxy_api(path):
        if engine_proc.state not in (STATE_RUNNING, STATE_STARTING):
            return jsonify({
                "error": "engine not running",
                "engine_state": engine_proc.state,
            }), 503
        qs = request.query_string.decode()
        target = f"http://127.0.0.1:{ENGINE_INTERNAL_PORT}/api/{path}"
        if qs:
            target += "?" + qs
        return _proxy_request(target)

    return app


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="modbus_sim_gui",
                                     description="ModbusSim GUI process manager")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--config", default="./project",
                        help="Project directory (same as engine --config)")
    parser.add_argument("--no-autostart", action="store_true",
                        help="Don't start the engine automatically on launch")
    args = parser.parse_args(argv)

    engine_proc = EngineProcess(project_dir=args.config)

    if not args.no_autostart:
        threading.Thread(
            target=engine_proc.start, daemon=True, name="engine-autostart"
        ).start()

    app = create_gui_app(engine_proc)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
