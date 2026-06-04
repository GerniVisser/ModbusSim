"""Flask REST API — the Engine/UI contract (REQUIREMENTS.md section 11).

Thin translation layer over ``StateMachine``: it parses requests, calls the matching
engine method, and renders JSON. Engine exceptions carry an ``http_status`` and are
mapped to the documented status codes (400 validation, 404 not found, 409 wrong
state, 500 engine failure). Handler exceptions are always returned as JSON so a Flask
error never crashes the process (§16).

``GET /`` serves the web UI when one is present and the engine is not headless; the UI
itself is deferred to a later pass, so this currently 404s unless a built UI exists.
"""

from __future__ import annotations

import collections
import importlib.util
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .state_machine import (
    EngineError,
    NotFoundError,
    StateError,
    StateMachine,
    ValidationError,
)

WEBUI_DIR = Path(__file__).parent / "webui"
_ZENON_CSV_PATH = Path(__file__).parents[1] / "import" / "zenon_csv.py"

# Lazily loaded; None if the import module is not installed.
_zenon_csv_module = None


def _get_zenon_csv():
    import sys
    global _zenon_csv_module
    if _zenon_csv_module is None:
        spec = importlib.util.spec_from_file_location("zenon_csv", _ZENON_CSV_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["zenon_csv"] = mod  # required for dataclasses on Python 3.14+
        spec.loader.exec_module(mod)
        _zenon_csv_module = mod
    return _zenon_csv_module

ENGINE_ERRORS = (StateError, ValidationError, NotFoundError, EngineError)


def _error_response(exc) -> tuple[Response, int]:
    status = getattr(exc, "http_status", 500)
    if isinstance(exc, ValidationError):
        body = {"ok": False, "errors": exc.errors}
    elif isinstance(exc, StateError):
        body = {"error": exc.message, "current_state": exc.current_state}
    elif isinstance(exc, (NotFoundError, EngineError)):
        body = {"error": exc.message}
    else:
        body = {"error": str(exc)}
    return jsonify(body), status


def _file_bytes() -> bytes:
    if "file" not in request.files:
        raise ValidationError(["multipart form field 'file' is required"])
    return request.files["file"].read()


def create_app(engine: StateMachine, headless: bool = False) -> Flask:
    app = Flask(__name__)

    def _handle_engine_error(exc):
        return _error_response(exc)

    for _exc_cls in ENGINE_ERRORS:
        app.register_error_handler(_exc_cls, _handle_engine_error)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc):  # never crash the process (§16)
        # Engine errors are handled above; re-dispatch if one slips through.
        if isinstance(exc, ENGINE_ERRORS):
            return _error_response(exc)
        return jsonify({"error": f"internal error: {exc}"}), 500

    # --------------------------------------------------------- system (any state)
    @app.get("/")
    def index():
        if headless:
            return jsonify({"error": "UI disabled (headless mode)"}), 404
        if (WEBUI_DIR / "index.html").exists():
            return send_from_directory(WEBUI_DIR, "index.html")
        return jsonify({"error": "web UI not installed"}), 404

    @app.get("/webui/<path:filename>")
    def webui_static(filename):
        if headless:
            return jsonify({"error": "UI disabled (headless mode)"}), 404
        return send_from_directory(WEBUI_DIR, filename)

    @app.get("/api/state")
    def state():
        return jsonify(engine.state_info())

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    # ----------------------------------------------------------- setup endpoints
    @app.post("/api/setup/config")
    def setup_config():
        return jsonify(engine.upload_config(_file_bytes()))

    @app.post("/api/setup/signals/<device_id>")
    def setup_signals(device_id):
        return jsonify(engine.upload_signals(device_id, _file_bytes()))

    @app.get("/api/setup/status")
    def setup_status():
        return jsonify(engine.setup_status())

    @app.post("/api/setup/start")
    def setup_start():
        return jsonify(engine.start())

    # --------------------------------------------------------- runtime endpoints
    @app.get("/api/devices")
    def devices():
        return jsonify(engine.get_devices())

    @app.get("/api/devices/<device_id>/signals")
    def get_signals(device_id):
        return jsonify(engine.get_signals(device_id))

    @app.get("/api/devices/<device_id>/values")
    def get_values(device_id):
        return jsonify(engine.get_values(device_id))

    @app.post("/api/devices/<device_id>/set")
    def set_value(device_id):
        body = request.get_json(silent=True) or {}
        if "name" not in body or "value" not in body:
            raise ValidationError(["body must contain 'name' and 'value'"])
        return jsonify(engine.set_value(device_id, body["name"], body["value"]))

    @app.post("/api/devices/<device_id>/simulate")
    def simulate_device(device_id):
        return jsonify(engine.simulate(device_id))

    @app.post("/api/devices/<device_id>/clear")
    def clear_device(device_id):
        return jsonify(engine.clear(device_id))

    @app.post("/api/simulate")
    def simulate_all():
        return jsonify(engine.simulate_all())

    @app.post("/api/clear")
    def clear_all():
        return jsonify(engine.clear_all())

    @app.post("/api/devices/<device_id>/signals")
    def hot_reload(device_id):
        body = request.get_json(silent=True) or {}
        if "signals" not in body or not isinstance(body["signals"], list):
            raise ValidationError(["body must contain a 'signals' array"])
        return jsonify(engine.hot_reload_json(device_id, body["signals"]))

    @app.post("/api/devices/<device_id>/signals/upload")
    def hot_reload_upload(device_id):
        return jsonify(engine.hot_reload_csv(device_id, _file_bytes()))

    @app.get("/api/devices/<device_id>/signals/download")
    def download_signals(device_id):
        csv_text = engine.signals_csv(device_id)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={device_id}.csv"
            },
        )

    @app.get("/api/config")
    def config_summary():
        return jsonify(engine.config_summary())

    @app.get("/api/network")
    def network():
        return jsonify(engine.network_state())

    @app.post("/api/stop")
    def stop():
        return jsonify(engine.stop())

    @app.post("/api/reset")
    def reset():
        return jsonify(engine.reset())

    # ------------------------------------------------- Zenon import (SETUP only)
    @app.post("/api/import/zenon/parse")
    def zenon_parse():
        if engine.state != "SETUP":
            raise StateError("import only available in SETUP state", engine.state)
        if engine.config_locked:
            raise StateError("config already locked; revert VM snapshot to re-import", engine.state)
        if not _ZENON_CSV_PATH.exists():
            return jsonify({"ok": False, "error": "zenon_csv import module not found"}), 500

        file_bytes = _file_bytes()
        zmod = _get_zenon_csv()
        devices, skipped, driver_type_counts, found_columns = zmod.parse_file(file_bytes)

        # Persist the raw file so the generate call can re-parse without re-upload.
        tmp_path = engine.project_dir / "zenon_import.tmp"
        engine.project_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(file_bytes)

        return jsonify({
            "ok": True,
            "drivers": [
                {
                    "driver_name": d.driver_name,
                    "net_addr": d.net_addr,
                    "suggested_id": d.suggested_id,
                    "signal_count": d.signal_count,
                }
                for d in devices
            ],
            "skipped_non_modbus": skipped,
            "total_signals": sum(d.signal_count for d in devices),
            "driver_type_counts": driver_type_counts,
            "detected_delimiter": repr(zmod._detect_delimiter(file_bytes.decode("utf-8-sig", errors="replace"))),
            "found_columns": found_columns,
        })

    @app.post("/api/import/zenon/generate")
    def zenon_generate():
        if engine.state != "SETUP":
            raise StateError("import only available in SETUP state", engine.state)
        if engine.config_locked:
            raise StateError("config already locked; revert VM snapshot to re-import", engine.state)

        tmp_path = engine.project_dir / "zenon_import.tmp"
        if not tmp_path.exists():
            raise ValidationError(["No parsed Zenon file found. Upload via /api/import/zenon/parse first."])

        body = request.get_json(silent=True) or {}
        project_name = (body.get("project_name") or "").strip()
        traffic_interface = (body.get("traffic_interface") or "").strip()
        web_ui_port = int(body.get("web_ui_port") or 5000)
        driver_entries = body.get("drivers")

        missing = []
        if not project_name:
            missing.append("project_name is required")
        if not traffic_interface:
            missing.append("traffic_interface is required")
        if not isinstance(driver_entries, list) or not driver_entries:
            missing.append("drivers must be a non-empty list")
        if missing:
            raise ValidationError(missing)

        zmod = _get_zenon_csv()
        file_bytes = tmp_path.read_bytes()
        all_devices, _, _dtc, _cols = zmod.parse_file(file_bytes)
        all_devices_map = {(d.driver_name, d.net_addr): d for d in all_devices}

        # Validate all driver entries before writing anything.
        driver_errors = []
        for entry in driver_entries:
            dn = entry.get("driver_name", "")
            na = entry.get("net_addr")
            uid = entry.get("unit_id")
            dev_id = (entry.get("id") or "").strip()
            ip = (entry.get("ip") or "").strip()
            if not ip or ip == "0.0.0.0":
                driver_errors.append(f"driver '{dn}' net_addr {na}: ip is required")
            if not dev_id:
                driver_errors.append(f"driver '{dn}' net_addr {na}: id is required")
            if uid is None or not (1 <= int(uid) <= 255):
                driver_errors.append(f"driver '{dn}' net_addr {na}: unit_id must be 1-255")
            key = (dn, int(na) if na is not None else None)
            if key not in all_devices_map:
                driver_errors.append(f"driver '{dn}' net_addr {na} not found in parsed file")
        if driver_errors:
            raise ValidationError(driver_errors)

        # Build device_params for config generation.
        selected_devices = []
        device_params: dict[tuple[str, int], dict] = {}
        for entry in driver_entries:
            key = (entry["driver_name"], int(entry["net_addr"]))
            device_params[key] = entry
            selected_devices.append(all_devices_map[key])

        config_yaml = zmod.generate_config_yaml(
            selected_devices,
            device_params,
            project_name=project_name,
            traffic_interface=traffic_interface,
            web_ui_port=web_ui_port,
        )

        # Pre-generate all signal CSVs so we can validate before touching state.
        signal_csvs: dict[str, tuple[str, bytes]] = {}
        for entry in driver_entries:
            dev = all_devices_map[(entry["driver_name"], int(entry["net_addr"]))]
            dev_id = (entry.get("id") or dev.suggested_id).strip()
            word_order = entry.get("word_order") or "little_endian"
            csv_text = zmod.generate_signal_csv(dev, word_order)
            signal_csvs[dev_id] = (csv_text, csv_text.encode("utf-8"))

        # Feed through the engine's normal validation + upload pipeline.
        engine.upload_config(config_yaml.encode("utf-8"))
        for dev_id, (_, csv_bytes) in signal_csvs.items():
            engine.upload_signals(dev_id, csv_bytes)

        # Clean up temp file after successful generate.
        tmp_path.unlink(missing_ok=True)

        return jsonify({
            "ok": True,
            "devices_generated": len(selected_devices),
            "total_signals": sum(d.signal_count for d in selected_devices),
            "ready_to_start": True,
        })

    # ------------------------------------------------- live Modbus request debug
    _req_log: collections.deque = collections.deque(maxlen=500)
    _req_log_lock = threading.Lock()
    _logging_enabled = [False]

    FC_NAMES = {1:"FC01(coil-R)",2:"FC02(di-R)",3:"FC03(hold-R)",4:"FC04(input-R)",
                5:"FC05(coil-W)",6:"FC06(hold-W)",15:"FC15(coil-W)",16:"FC16(hold-W)"}
    EXC_NAMES = {1:"ILLEGAL_FUNCTION",2:"ILLEGAL_ADDRESS",3:"ILLEGAL_VALUE",4:"DEVICE_FAILURE"}

    def _wrap_action(original_action):
        async def logged_action(func_code, start_address, address, count, registers, values):
            result = await original_action(func_code, start_address, address, count, registers, values)
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "fc": FC_NAMES.get(func_code, f"FC{func_code:02d}"),
                "addr": address,
                "count": count,
                "dir": "W" if values is not None else "R",
                "values_in": list(values) if values is not None else None,
                "registers_out": list(registers) if values is None else None,
                "error": EXC_NAMES.get(result, str(result)) if result is not None else None,
            }
            with _req_log_lock:
                _req_log.append(entry)
            return result
        return logged_action

    @app.post("/api/debug/enable_request_log")
    def debug_enable_log():
        if engine.state != "RUNNING":
            return jsonify({"ok": False, "error": "engine not running"}), 409
        if _logging_enabled[0]:
            return jsonify({"ok": True, "message": "already enabled", "log_size": len(_req_log)})
        patched = 0
        for server in engine._servers._servers.values():
            for unit_id, runtime in server.context.devices.items():
                runtime.action = _wrap_action(runtime.action)
                patched += 1
        _logging_enabled[0] = True
        with _req_log_lock:
            _req_log.clear()
        return jsonify({"ok": True, "message": f"logging enabled on {patched} device(s)"})

    @app.get("/api/debug/requests")
    def debug_requests():
        with _req_log_lock:
            entries = list(_req_log)
        return jsonify({"ok": True, "enabled": _logging_enabled[0], "count": len(entries), "requests": entries})

    @app.delete("/api/debug/requests")
    def debug_clear_requests():
        with _req_log_lock:
            _req_log.clear()
        return jsonify({"ok": True})

    return app
