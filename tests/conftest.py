"""Shared test helpers."""
from __future__ import annotations

import io

from modbus_sim.api_server import create_app
from modbus_sim.state_machine import StateMachine

SAMPLE_SIGNALS = """name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable
Grid Voltage L1,holding,1000,uint16,,big_endian,0.1,V,Grid,L1 voltage,4000,false
Active Power,holding,1004,int32,,big_endian,1,W,Grid,Total active power,2000000,false
DC Bus Voltage,holding,1030,float32,,little_endian,1,V,DC,DC bus,600.0,false
Running Status,holding,1040,bool,0,,,,Status,running bit,1,false
Fault Status,holding,1040,bool,1,,,,Status,fault bit,0,false
Input Voltage,input,2000,uint16,,big_endian,0.1,V,DC,string voltage,6000,false
Fan Enable,coil,0,bool,,,1,,Control,fan,1,false
Door Open,discrete_input,0,bool,,,1,,Safety,door,0,false
"""


def make_config_yaml(devices: list[dict], traffic_interface="lo", vlan_mode="auto") -> str:
    """Build a sim_config.yaml string from a list of device dicts."""
    lines = [
        "project:",
        "  name: Test Project",
        "network:",
        f"  traffic_interface: {traffic_interface}",
        f"  vlan_mode: {vlan_mode}",
        "devices:",
    ]
    for d in devices:
        lines.append(f"  - id: {d['id']}")
        lines.append(f"    name: {d.get('name', d['id'])}")
        lines.append(f"    ip: {d['ip']}")
        lines.append(f"    port: {d['port']}")
        lines.append(f"    unit_id: {d['unit_id']}")
        if d.get("vlan"):
            lines.append(f"    vlan: {d['vlan']}")
        lines.append(f"    signals_file: devices/{d['id']}.csv")
    return "\n".join(lines) + "\n"


def make_engine(tmp_path, **kwargs):
    """Engine + Flask test client over a temp project dir, network disabled."""
    engine = StateMachine(tmp_path, manage_network=False, **kwargs)
    app = create_app(engine, headless=False)
    app.testing = True
    return engine, app.test_client()


def upload_file(client, url, text, filename):
    return client.post(
        url,
        data={"file": (io.BytesIO(text.encode("utf-8")), filename)},
        content_type="multipart/form-data",
    )
