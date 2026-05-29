"""sim_config.yaml validation tests (REQUIREMENTS.md sections 6, 18)."""
from modbus_sim.config_loader import load_and_validate

from .conftest import make_config_yaml


def test_four_devices_two_vlans_ok():
    yaml = make_config_yaml([
        {"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1, "vlan": 100},
        {"id": "b", "ip": "10.4.1.11", "port": 502, "unit_id": 2, "vlan": 100},
        {"id": "c", "ip": "10.4.2.50", "port": 502, "unit_id": 200, "vlan": 200},
        {"id": "d", "ip": "10.4.3.10", "port": 502, "unit_id": 1, "vlan": 300},
    ])
    config, errors = load_and_validate(yaml)
    assert not errors
    assert len(config.devices) == 4
    assert config.is_vlan_mode is True


def test_duplicate_device_id_rejected():
    yaml = make_config_yaml([
        {"id": "same", "ip": "10.0.0.1", "port": 502, "unit_id": 1},
        {"id": "same", "ip": "10.0.0.2", "port": 502, "unit_id": 2},
    ])
    config, errors = load_and_validate(yaml)
    assert config is None
    assert any("duplicate device id" in e for e in errors)


def test_duplicate_ip_port_unit_rejected():
    yaml = make_config_yaml([
        {"id": "a", "ip": "10.0.0.1", "port": 502, "unit_id": 1},
        {"id": "b", "ip": "10.0.0.1", "port": 502, "unit_id": 1},
    ])
    _, errors = load_and_validate(yaml)
    assert any("ip, port, unit_id" in e for e in errors)


def test_same_ip_port_different_unit_allowed():
    yaml = make_config_yaml([
        {"id": "a", "ip": "10.0.0.1", "port": 502, "unit_id": 1},
        {"id": "b", "ip": "10.0.0.1", "port": 502, "unit_id": 2},
    ])
    _, errors = load_and_validate(yaml)
    assert not errors


def test_invalid_ip_rejected():
    yaml = make_config_yaml([
        {"id": "a", "ip": "10.4.1.999", "port": 502, "unit_id": 1},
    ])
    _, errors = load_and_validate(yaml)
    assert any("invalid IP" in e for e in errors)


def test_vlan_mode_auto_off_when_no_vlans():
    yaml = make_config_yaml([
        {"id": "a", "ip": "10.0.0.1", "port": 502, "unit_id": 1},
    ])
    config, errors = load_and_validate(yaml)
    assert not errors
    assert config.is_vlan_mode is False


def test_vlan_mode_disabled_overrides_device_vlan():
    yaml = make_config_yaml(
        [{"id": "a", "ip": "10.0.0.1", "port": 502, "unit_id": 1, "vlan": 100}],
        vlan_mode="disabled",
    )
    config, errors = load_and_validate(yaml)
    assert not errors
    assert config.is_vlan_mode is False
