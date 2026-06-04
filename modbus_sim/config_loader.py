"""Parse and validate ``sim_config.yaml`` (REQUIREMENTS.md section 6).

The config defines the whole simulation: project metadata, network settings, and
the list of devices. This module is pure (no I/O beyond the YAML string passed in)
so it is trivially testable. File-existence checks for ``traffic_interface`` and
each ``signals_file`` are intentionally NOT done here — those are validated at the
``start`` transition, because signal files arrive separately via the setup wizard.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Optional

import yaml

VALID_VLAN_MODES = ("auto", "enabled", "disabled")


@dataclass
class DeviceConfig:
    id: str
    name: str
    ip: str
    port: int
    unit_id: int
    signals_file: str
    vlan: int = 0
    prefix_length: int = 24
    description: str = ""


@dataclass
class SimConfig:
    project_name: str
    traffic_interface: str
    devices: list[DeviceConfig]
    project_description: str = ""
    project_version: str = ""
    management_interface: Optional[str] = None
    web_ui_port: int = 5000
    vlan_mode: str = "auto"
    raw_yaml: str = field(default="", repr=False)

    @property
    def is_vlan_mode(self) -> bool:
        """Resolve the effective VLAN mode (section 8 logic).

        - ``enabled``  -> always on
        - ``disabled`` -> always off
        - ``auto``     -> on iff at least one device has a non-zero vlan
        """
        if self.vlan_mode == "enabled":
            return True
        if self.vlan_mode == "disabled":
            return False
        return any(d.vlan for d in self.devices)

    def device_by_id(self, device_id: str) -> Optional[DeviceConfig]:
        for d in self.devices:
            if d.id == device_id:
                return d
        return None


def _as_int(value, label: str, errors: list[str]) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be an integer (got {value!r})")
        return None


def load_and_validate(yaml_text: str) -> tuple[Optional[SimConfig], list[str]]:
    """Parse YAML text and validate it against the section 6 schema.

    Returns ``(SimConfig, [])`` on success or ``(None, [errors...])`` on failure.
    Errors are human-readable strings suitable for the ``/api/setup/config`` 400 body.
    """
    errors: list[str] = []

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return None, [f"invalid YAML: {exc}"]

    if not isinstance(data, dict):
        return None, ["config root must be a mapping"]

    project = data.get("project") or {}
    network = data.get("network") or {}
    raw_devices = data.get("devices")

    if not isinstance(project, dict):
        errors.append("'project' must be a mapping")
        project = {}
    if not isinstance(network, dict):
        errors.append("'network' must be a mapping")
        network = {}

    project_name = project.get("name")
    if not project_name:
        errors.append("project.name is required")

    traffic_interface = network.get("traffic_interface")
    if not traffic_interface:
        errors.append("network.traffic_interface is required")

    vlan_mode = network.get("vlan_mode", "auto")
    if vlan_mode not in VALID_VLAN_MODES:
        errors.append(
            f"network.vlan_mode must be one of {VALID_VLAN_MODES} (got {vlan_mode!r})"
        )
        vlan_mode = "auto"

    web_ui_port = network.get("web_ui_port", 5000)
    port_val = _as_int(web_ui_port, "network.web_ui_port", errors)
    web_ui_port = port_val if port_val is not None else 5000

    if not isinstance(raw_devices, list) or not raw_devices:
        errors.append("'devices' must be a non-empty list")
        raw_devices = []

    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()
    seen_ip_port_unit: set[tuple[str, int, int]] = set()

    for idx, raw in enumerate(raw_devices):
        label = f"device[{idx}]"
        if not isinstance(raw, dict):
            errors.append(f"{label} must be a mapping")
            continue

        dev_id = raw.get("id")
        if not dev_id:
            errors.append(f"{label}.id is required")
            dev_id = f"<device {idx}>"
        else:
            label = f"device '{dev_id}'"
            if dev_id in seen_ids:
                errors.append(f"duplicate device id '{dev_id}'")
            seen_ids.add(dev_id)

        name = raw.get("name")
        if not name:
            errors.append(f"{label}.name is required")

        ip = raw.get("ip")
        if not ip:
            errors.append(f"{label}.ip is required")
        else:
            try:
                ipaddress.IPv4Address(str(ip))
            except ipaddress.AddressValueError:
                errors.append(f"{label} has invalid IP address '{ip}'")

        port = _as_int(raw.get("port"), f"{label}.port", errors)
        if port is not None and not (1 <= port <= 65535):
            errors.append(f"{label}.port must be 1-65535 (got {port})")

        unit_id = _as_int(raw.get("unit_id"), f"{label}.unit_id", errors)
        if unit_id is not None and not (1 <= unit_id <= 255):
            errors.append(f"{label}.unit_id must be 1-255 (got {unit_id})")

        signals_file = raw.get("signals_file")
        if not signals_file:
            errors.append(f"{label}.signals_file is required")

        vlan = raw.get("vlan", 0) or 0
        vlan_int = _as_int(vlan, f"{label}.vlan", errors)
        vlan = vlan_int if vlan_int is not None else 0

        prefix_length = raw.get("prefix_length", 24)
        prefix_int = _as_int(prefix_length, f"{label}.prefix_length", errors)
        prefix_length = prefix_int if prefix_int is not None else 24
        if not (0 <= prefix_length <= 32):
            errors.append(f"{label}.prefix_length must be 0-32 (got {prefix_length})")

        # Track (ip, port, unit_id) uniqueness once all three parsed cleanly.
        if ip and port is not None and unit_id is not None:
            key = (str(ip), port, unit_id)
            if key in seen_ip_port_unit:
                errors.append(
                    f"duplicate (ip, port, unit_id) combination "
                    f"{ip}:{port} unit {unit_id}"
                )
            seen_ip_port_unit.add(key)

        # Only build the dataclass if the required fields parsed; otherwise the
        # errors above already explain the rejection.
        if dev_id and name and ip and port is not None and unit_id is not None and signals_file:
            devices.append(
                DeviceConfig(
                    id=dev_id,
                    name=name,
                    ip=str(ip),
                    port=port,
                    unit_id=unit_id,
                    signals_file=signals_file,
                    vlan=vlan,
                    prefix_length=prefix_length,
                    description=raw.get("description", "") or "",
                )
            )

    if errors:
        return None, errors

    config = SimConfig(
        project_name=project_name,
        project_description=project.get("description", "") or "",
        project_version=str(project.get("version", "") or ""),
        traffic_interface=traffic_interface,
        management_interface=network.get("management_interface"),
        web_ui_port=web_ui_port,
        vlan_mode=vlan_mode,
        devices=devices,
        raw_yaml=yaml_text,
    )
    return config, []
