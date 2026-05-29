"""NetworkManager VLAN-mode logic (REQUIREMENTS.md sections 8, 18).

Pure logic (interface naming, VLAN-mode resolution, dedup) is tested here without
issuing ``ip`` commands. Actually executing ``ip link``/``ip addr`` requires Linux +
root and is verified on the VM per the acceptance criteria.
"""
from modbus_sim.config_loader import load_and_validate
from modbus_sim.network_manager import NetworkManager

from .conftest import make_config_yaml


def _nm(devices, vlan_mode="auto"):
    config, errors = load_and_validate(
        make_config_yaml(devices, traffic_interface="eth1", vlan_mode=vlan_mode)
    )
    assert not errors, errors
    return NetworkManager(config), config


def test_vlan_mode_auto_enabled_with_vlans():
    nm, _ = _nm([{"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1, "vlan": 100}])
    assert nm.is_vlan_mode is True


def test_vlan_mode_auto_disabled_without_vlans():
    nm, _ = _nm([{"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1}])
    assert nm.is_vlan_mode is False


def test_shared_vlan_is_deduplicated():
    nm, _ = _nm([
        {"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1, "vlan": 100},
        {"id": "b", "ip": "10.4.1.11", "port": 502, "unit_id": 2, "vlan": 100},
        {"id": "c", "ip": "10.4.2.50", "port": 502, "unit_id": 3, "vlan": 200},
    ])
    assert nm._unique_vlans() == [100, 200]


def test_target_interface_naming():
    nm, config = _nm([
        {"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1, "vlan": 100},
    ])
    assert nm._target_interface(config.devices[0]) == "eth1.100"


def test_target_interface_no_vlan_mode():
    nm, config = _nm([{"id": "a", "ip": "10.4.1.10", "port": 502, "unit_id": 1}])
    assert nm._target_interface(config.devices[0]) == "eth1"
