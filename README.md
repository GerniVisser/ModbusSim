# Generic Modbus TCP Simulator

A project-agnostic **Modbus TCP device simulator**. It simulates any number of Modbus
TCP devices (inverters, meters, PLCs, controllers) from plain CSV signal definitions —
no real hardware required. It runs natively on a Linux VM, manages its own 802.1Q VLAN
network interfaces, and exposes a REST API plus a browser-based web UI.

All device/signal knowledge comes from user-supplied config and CSV files; the engine
has no built-in knowledge of any device. See [`REQUIREMENTS.md`](REQUIREMENTS.md) for the
full specification.

> **pymodbus 3.13+** — The Modbus servers are built on pymodbus's `SimData`/`SimDevice`
> model (the classic `ModbusSlaveContext` API was removed in 3.11). `requirements.txt`
> pins `pymodbus>=3.13.0` for this reason.

---

## Architecture

Two decoupled layers, with a REST API as the contract between them:

```
Web UI (browser, Bootstrap 5 + vanilla JS)   modbus_sim/webui/
        |  HTTP / JSON
Engine (Python)                               modbus_sim/
  main.py            entry point, CLI, lifecycle, banners
  api_server.py      Flask REST API (+ serves the UI unless --headless)
  state_machine.py   SETUP -> RUNNING (-> STOPPING); orchestration
  config_loader.py   parse/validate sim_config.yaml
  signal_loader.py   parse/validate device signal CSVs
  register_map.py    per-device, thread-safe register store (source of truth)
  modbus_server.py   pymodbus async TCP servers (one per ip:port)
  network_manager.py VLAN + IP setup via `ip` (Linux, root) — no teardown
```

The engine is the system of record; the UI is a stateless, swappable layer. The engine
runs Flask in the main thread and the Modbus asyncio servers in a daemon thread.

### Engine states

| State | What the user can do |
|---|---|
| **SETUP** | Upload config, upload signal CSVs, start the simulation |
| **RUNNING** | View/edit live values, simulate/clear, hot-reload signals, stop |

The transition is one-way. Network state is **never** torn down — the VM snapshot is the
cleanup mechanism (revert to a clean snapshot to load a different project).

---

## Requirements

- Linux (Ubuntu Server 22.04/24.04 recommended) with `iproute2`, for the real run
- Python 3.10+
- Root privileges when binding port < 1024 (e.g. 502) or using VLANs

The engine package itself is cross-platform; only `network_manager.py` requires Linux +
root. You can develop and run the rest (including the full UI and Modbus servers on
loopback) on any OS via `--no-network`.

---

## Install

Use a virtual environment (Ubuntu 23.04+ blocks system-wide `pip`):

```bash
cd modbus-sim
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # runtime + pytest (for tests)
```

---

## Running

```bash
# Full mode: REST API + web UI (this is the normal mode)
sudo .venv/bin/python -m modbus_sim.main --config ./project

# Headless: REST API only, no UI (automation / scripts)
sudo .venv/bin/python -m modbus_sim.main --headless --config ./project
```

> **`sudo` + venv:** `sudo` ignores an activated venv, so always call the venv's
> interpreter explicitly: `sudo .venv/bin/python -m modbus_sim.main ...`.

### CLI flags

| Flag | Effect |
|---|---|
| *(none)* | Full mode — Engine + Web UI on `http://<host>:5000` |
| `--headless` | Engine only; `GET /` returns 404 |
| `--config DIR` | Project directory (default `./project`) |
| `--port N` / `--host H` | API/UI bind port/address (default `0.0.0.0:5000`) |
| `--no-network` | Skip VLAN/IP setup; bind to already-present IPs (loopback/mgmt). **For testing without the USB-C NIC / without root.** |
| `--reset` | Clear a locked project directory and start fresh (dev only) |

Once a config is uploaded it is **locked** (`.config_locked` marker). Re-running refuses
to start unless you `--reset` or revert the VM snapshot.

### Quick local smoke test (no hardware, no root)

```bash
# 1. start on loopback, network management off
.venv/bin/python -m modbus_sim.main --no-network --config ./project --port 5000
```

Then open `http://localhost:5000` and use the wizard with a config whose device IPs are
`127.0.0.1` and ports are ≥ 1024 (e.g. 5020). Or drive it headless with curl — see below.

---

## Web UI

Open `http://<host>:5000` in a browser. The UI reads `GET /api/state` and shows:

- **Setup wizard** (SETUP state): 3 steps — upload `sim_config.yaml` → upload a signal
  CSV per device → **Start Simulation**. Validation errors are shown inline with row/column.
- **Runtime view** (RUNNING state): device list, signal table grouped by section, live
  value editing (numeric fields + bool toggles), per-device and global Simulate/Clear,
  a client-side filter, and a **2-second auto-refresh** that never overwrites the field
  you're editing.
- **Signal editor** (per device): a tabular modal to add/edit/delete signals with
  client-side validation, **Save & Hot Reload** (rebuilds and hot-swaps the device's
  register map with no engine restart and without dropping Modbus connections), plus
  CSV download/upload.

The UI transitions automatically between views as the engine state changes.

---

## Driving it headless (curl)

```bash
curl -F file=@sim_config.yaml      http://<host>:5000/api/setup/config
curl -F file=@devices/dev1.csv     http://<host>:5000/api/setup/signals/dev1
curl -X POST                       http://<host>:5000/api/setup/start
curl                               http://<host>:5000/api/devices/dev1/values
```

### REST API summary

| Method & path | State | Purpose |
|---|---|---|
| `GET /api/state` | any | Current engine state |
| `GET /api/health` | any | `{ "ok": true }` |
| `POST /api/setup/config` | SETUP | Upload + lock `sim_config.yaml` (multipart) |
| `POST /api/setup/signals/{id}` | SETUP | Upload a device signal CSV (multipart) |
| `GET /api/setup/status` | SETUP | Wizard progress + device list |
| `POST /api/setup/start` | SETUP | Network setup + start servers → RUNNING |
| `GET /api/devices` | RUNNING | All devices + status |
| `GET /api/devices/{id}/signals` | RUNNING | Device signal definitions |
| `GET /api/devices/{id}/values` | RUNNING | Current values by signal name |
| `POST /api/devices/{id}/set` | RUNNING | Set one value `{name, value}` |
| `POST /api/devices/{id}/simulate`\|`/clear` | RUNNING | Load defaults / zero one device |
| `POST /api/simulate`\|`/clear` | RUNNING | Load defaults / zero all devices |
| `POST /api/devices/{id}/signals` | RUNNING | **Hot reload** from JSON `{signals:[...]}` |
| `POST /api/devices/{id}/signals/upload` | RUNNING | Hot reload from CSV (multipart) |
| `GET /api/devices/{id}/signals/download` | RUNNING | Download device CSV |
| `GET /api/config` | RUNNING | Project summary |
| `GET /api/network` | RUNNING | VLAN interfaces + assigned IPs |
| `POST /api/stop` | RUNNING | Graceful shutdown |

Status codes: `400` validation (with row/column detail), `404` unknown device/signal,
`409` wrong engine state, `500` network/startup failure.

---

## Configuration & signal formats

`sim_config.yaml` (full schema in [`REQUIREMENTS.md` §6](REQUIREMENTS.md)):

```yaml
project:
  name: Du Plessis PV
network:
  traffic_interface: enp0s20f0u1   # USB-C NIC name from `ip link`
  vlan_mode: auto                  # auto | enabled | disabled
devices:
  - id: col1_inv1
    name: COL1 Inverter 1
    ip: 10.4.1.10
    port: 502
    unit_id: 1
    vlan: 100
    signals_file: devices/col1_inv1.csv
```

Signal CSV header (full reference in [`REQUIREMENTS.md` §7](REQUIREMENTS.md)):

```csv
name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable
Grid Voltage,holding,1000,uint16,,big_endian,0.1,V,Grid,L1 voltage,4000,false
Active Power,holding,1004,int32,,big_endian,1,W,Grid,Total power,2000000,false
DC Bus Voltage,holding,1030,float32,,little_endian,1,V,DC,DC bus,600.0,false
Running,holding,1040,bool,0,,,,Status,running bit,1,false
Fan Enable,coil,0,bool,,,1,,Control,fan,1,false
```

- Types: `uint16`, `int16`, `uint32`, `int32`, `float32`, `bool`; tables: `holding`,
  `input`, `coil`, `discrete_input`.
- `word_order` (`big_endian`/`little_endian`) is required for 32-bit/float types.
- `bit_index` is required for **`holding`/`input`** bools (a bit within a word) and must
  be **empty** for `coil`/`discrete_input` bools (each coil is its own address).
- Values are stored/served **raw**; `scale` is display-only (`engineering = raw × scale`).

---

## Production workflow on the VM (VLAN injection)

1. Pass the USB-C → Ethernet adapter through to the Linux VM (VMware USB passthrough).
2. Find its interface name: `ip link` (e.g. `enp0s20f0u1`) → set as `traffic_interface`.
3. Connect that NIC to a **trunk** port on a managed switch carrying your VLANs.
4. Start the engine as root, upload config + signals (browser or curl), click Start.
5. Verify: `ip link` shows `enp0s20f0u1.100` etc.; `ip addr` shows the device IPs;
   `GET /api/network` lists them.
6. To switch projects, **revert the VM to the "Simulator Clean State" snapshot** — the
   engine intentionally never tears down network state.

See [`REQUIREMENTS.md` §4 & §15](REQUIREMENTS.md) for the VMware/snapshot procedure.

---

## Testing

```bash
.venv/bin/python -m pytest -q
```

Covers register encoding (all six types, word orders, bit-packed bools), CSV/config
validation, the setup state machine and API, web-UI serving, and a full end-to-end
runtime test that reads the simulated registers with a real pymodbus client over loopback
and exercises hot reload. Network-only behaviour (`ip` commands) is verified on the VM
per the acceptance criteria in `REQUIREMENTS.md §18`.

---

## Project layout

```
modbus_sim/            engine package (see Architecture)
  webui/               index.html + app.js / setup.js / runtime.js / editor.js
project/               runtime project dir (volatile): sim_config.yaml, devices/*.csv, .config_locked
tests/                 pytest suite
requirements.txt       runtime dependencies
requirements-dev.txt   + pytest
REQUIREMENTS.md        full specification
```

## Not yet implemented

These items from the spec are planned but not built:

- **Importers** (`importers/zenon_txt.py`, `importers/csv_validator.py`, §14) — the
  validator would reuse `signal_loader.parse_and_validate`.
- **Packaging** (`install.sh`, `systemd/modbus-sim.service`, §15) — for auto-start on VM
  boot. For now the engine is launched manually.
```
