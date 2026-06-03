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

    return app
