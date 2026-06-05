# Generic Modbus TCP Simulator — User Manual

A project-agnostic **Modbus TCP device simulator**. It simultaneously simulates any number of Modbus TCP field devices (inverters, meters, PLCs, controllers) from plain CSV signal definitions — no real hardware required. It runs on a Linux VM, manages its own 802.1Q VLAN network interfaces, and is controlled entirely through a browser-based web UI.

---

## Contents

1. [How it works](#1-how-it-works)
2. [First-time VM setup](#2-first-time-vm-setup)
3. [Starting a simulation session](#3-starting-a-simulation-session)
4. [Importing from Zenon 15](#4-importing-from-zenon-15)
5. [Runtime — viewing and editing values](#5-runtime--viewing-and-editing-values)
6. [Editing signal definitions (hot reload)](#6-editing-signal-definitions-hot-reload)
7. [Preparing files manually](#7-preparing-files-manually)
8. [Crash recovery (--restore)](#8-crash-recovery---restore)
9. [Headless operation (curl / scripts)](#9-headless-operation-curl--scripts)
10. [Developer reference](#10-developer-reference)

---

## 1. How it works

```
Windows PC (browser)
        │ http://<vm-ip>:5000
        ▼
Linux VM ─── Management NIC (eth0)    ← web UI + API
         └── USB-C NIC (eth1)  ───── Managed switch (trunk)
                   │                          │
               eth1.100 ─ 10.4.1.10          VLAN 100 → Zenon inverter network
               eth1.100 ─ 10.4.1.11          VLAN 100
               eth1.200 ─ 10.4.2.50          VLAN 200 → Zenon SCB network
```

The simulator creates VLAN subinterfaces on the USB-C NIC and assigns each simulated device its own IP address. Modbus clients on the switch see the simulated devices exactly as they would real hardware.

**The VM snapshot is the reset mechanism.** The simulator never tears down network state. To switch to a different project, revert the VM to the *Simulator Clean State* snapshot. This is intentional — it guarantees a provably clean environment every time.

---

## 2. First-time VM setup

This is a one-time procedure. Once done, take the *Simulator Clean State* snapshot and you never need to repeat it.

### Hardware needed

| Item | Purpose |
|---|---|
| Windows PC | Runs VMware Workstation and the browser |
| USB-C to Gigabit Ethernet adapter | Dedicated Modbus / VLAN traffic NIC |
| Managed switch (VLAN mode only) | Carries VLAN-tagged traffic to Zenon |

### Create the VM

- **OS:** Ubuntu Server 22.04 LTS or 24.04 LTS
- **Resources:** 2 GB RAM, 2 vCPUs, 20 GB disk
- **Network adapters:**
  - Adapter 1: Bridged or Host-Only → management (web UI access from Windows)
  - USB-C NIC: USB Passthrough (not a second VMware virtual adapter)

To connect the USB-C NIC: VMware toolbar → **VM → Removable Devices → [adapter name] → Connect (Disconnect from Host)**.

### Install the simulator

```bash
# Inside the Linux VM
git clone <repo> /opt/modbus-sim
cd /opt/modbus-sim
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install the systemd service so the engine starts automatically on boot:

```bash
sudo cp systemd/modbus-sim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable modbus-sim
```

### Find the USB-C NIC interface name

```bash
ip link
```

Look for the USB-C adapter (e.g. `enp0s20f0u1`, `eth1`, `usb0`). Note the name — it goes into `traffic_interface` in every `sim_config.yaml` you create.

### Take the clean snapshot

```
1. Confirm: ip link shows NO VLAN subinterfaces on the USB-C NIC
2. Confirm: ip addr shows NO simulator-assigned IPs on the USB-C NIC
3. Confirm: /opt/modbus-sim/project/ is empty
4. VMware: Snapshot → Take Snapshot → Name it exactly: Simulator Clean State
```

Do not delete this snapshot. It is the reset baseline for all sessions.

---

## 3. Starting a simulation session

This is the normal operating procedure every time you want to run a simulation.

### Step 1 — Revert the VM

In VMware on the Windows host: **Snapshot → Revert to "Simulator Clean State"**.  
Power on the VM. Wait ~30 seconds for boot. The engine starts automatically.

### Step 2 — Open the web UI

In a browser on the Windows host:
```
http://<vm-management-ip>:5000
```

You will see the **Setup Wizard**.

### Step 3 — Upload your config

In **Step 1** of the wizard, upload your `sim_config.yaml`. The engine validates it and locks it immediately. You cannot change the config after this point without reverting the VM.

If you have a Zenon 15 variable export, skip this and use **Import from Zenon CSV** instead — see [Section 4](#4-importing-from-zenon-15).

### Step 4 — Upload signal CSVs

In **Step 2** of the wizard, upload one signal CSV per device listed in your config. Each row in the CSV defines one Modbus register the simulator will respond to. See [Section 7](#7-preparing-files-manually) for the CSV format.

Validation errors are shown inline with row and column numbers.

### Step 5 — Start

Click **Start Simulation** in Step 3. The engine:
1. Creates VLAN subinterfaces on the USB-C NIC
2. Assigns each device IP to its VLAN interface
3. Starts a Modbus TCP server for each device

The UI transitions automatically to the **Runtime View**.

---

## 4. Importing from Zenon 15

If your signal definitions come from Zenon 15 Engineering Studio, you can generate the config and signal CSVs automatically from a Zenon variable export.

### Step 1 — Export from Zenon 15

In Zenon Engineering Studio: export your project variables to a CSV file (tab, semicolon, or comma delimited). The export must include at minimum these columns: `VariableName`, `DriverName`, `DriverType`, `NetAddr`, `TypeName`, `Offset`, `ReadWrite`.

Clean the export before importing — remove any duplicate-address variables (e.g. Zenon `Trend_` shadow variables point to the same registers as the originals and will cause address conflicts).

### Step 2 — Open the import wizard

In the Setup Wizard, click **Import from Zenon CSV** (below the config upload section). A modal opens.

### Step 3 — Upload the variable export (Step 1 of 3)

Select your Zenon CSV and click **Parse File**. The engine filters for Modbus drivers (any `DriverType` or `DriverName` containing "MODBUS") and groups signals by `(DriverName, NetAddr)` — one simulator device per unique pair.

If no devices are found, the error message shows the detected delimiter, driver types found, and the first few column names to help diagnose format issues.

### Step 4 — Upload a device mapping (Step 2 of 3, optional)

Upload a mapping CSV that pre-populates IP addresses, Modbus unit IDs, VLANs, and display names. This saves you filling in the table manually for large projects.

**Mapping CSV format** (comma or semicolon delimited, header required):

```
DriverName,NetAddr,IPAddress,UnitID,VLAN,DisplayName
Modbus Energy Driver,1,10.4.26.2,1,1,Inverter Col1 ITS1
Modbus Energy Driver,2,10.4.26.3,1,1,Inverter Col1 ITS2
Modbus Energy Driver,3,10.4.26.8,1,1,Inverter Col2 ITS1
```

Rows are matched by `DriverName` + `NetAddr`. Unmatched devices appear with blank IP fields in the next step. `DisplayName` is optional — blank keeps the driver name.

Click **Skip** to go straight to the configure table without a mapping file.

### Step 5 — Configure devices (Step 3 of 3)

Review and fill in the device table. Each row is one simulated device:

| Column | Required | Description |
|---|---|---|
| Device ID | Yes | Short slug used internally (no spaces) |
| Display Name | Yes | Human-readable name shown in the UI |
| IP Address | Yes | IP to assign to the traffic interface |
| Modbus Unit | Yes | Modbus slave/unit ID (1–255) |
| Port | No | Modbus TCP port (default 502) |
| VLAN | No | VLAN ID for 802.1Q tagging (0 = no VLAN) |
| Prefix | No | Subnet prefix length (default /24) |
| Word Order | No | 32-bit word order for this device (little-endian / big-endian) |

Use **Set all word orders** to apply one endianness to every device at once.

Set your **Project Name** and **Traffic Interface** (the USB-C NIC name from `ip link`) at the top, then click **Generate Config & Signals**.

The engine generates `sim_config.yaml` and a signal CSV for every device and feeds them through the normal validation pipeline. The wizard advances to Step 3 (Start).

---

## 5. Runtime — viewing and editing values

Once the simulation is running the UI shows the **Runtime View**.

### Device list (left panel)

Lists all simulated devices. Click any device to load its signals in the right panel. The selected device auto-refreshes every 2 seconds.

### Signal table (right panel)

Signals are grouped by section. Each row shows:
- **Signal name** and description
- **Raw value** — the actual register value (editable inline)
- **Scaled value** — `raw × scale`, display-only
- **Unit** and data type

**Editing a value:** Click the raw value field, type a new value, press Enter or click away. The value is written to the register immediately. The field you are editing is never overwritten by the auto-refresh.

**Bool signals** show as a toggle switch. Toggling writes only the targeted bit — other bits in the same register are not affected.

### Simulate and Clear

| Button | Effect |
|---|---|
| **Simulate** (per device) | Loads the `default_value` for every signal in the device |
| **Clear** (per device) | Zeros all registers for the device |
| **Simulate All** | Simulate for every device simultaneously |
| **Clear All** | Zero every device simultaneously |

Use Simulate to bring a device to a realistic resting state before your client connects.

---

## 6. Editing signal definitions (hot reload)

Click **Edit Signals** on any device (runtime view) to open the signal editor. You can:

- Add, edit, or delete signal rows
- Change register types, addresses, data types, scale factors, sections, defaults
- Upload a new CSV to replace all signals
- Download the current signal list as a CSV

Click **Save & Hot Reload** to apply changes. The engine:
1. Validates the new signal list
2. Writes the updated CSV to disk
3. Rebuilds the device's register map
4. Hot-swaps it into the running Modbus server — no restart, no connection drop

If validation fails, errors are shown inline next to the offending rows and no changes are made.

---

## 7. Preparing files manually

### sim_config.yaml

```yaml
project:
  name: My Plant

network:
  traffic_interface: eth1        # USB-C NIC name from `ip link`
  vlan_mode: auto                # auto | enabled | disabled

devices:
  - id: col1_inv1                # unique slug, no spaces
    name: COL1 Inverter 1        # display name
    ip: 10.4.1.10
    port: 502
    unit_id: 1
    vlan: 100                    # 0 or omit for no VLAN
    prefix_length: 24
    signals_file: devices/col1_inv1.csv
```

`vlan_mode: auto` enables VLAN mode if any device has a non-zero `vlan`. Two devices may share the same `(ip, port)` only if they have different `unit_id` values.

### Signal CSV

```csv
name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable
Grid Voltage,holding,1000,uint16,,big_endian,0.1,V,Grid,L1 voltage,4000,false
Active Power,holding,1004,int32,,big_endian,1,W,Grid,Total power,2000000,false
DC Bus Voltage,holding,1030,float32,,little_endian,1,V,DC,DC bus,600.0,false
Running,holding,1040,bool,0,,,,,Status,Running bit,1,false
Fan Enable,coil,0,bool,,,1,,Control,Fan relay,1,false
```

**Column reference:**

| Column | Required | Notes |
|---|---|---|
| `name` | Yes | Unique within the device |
| `register_type` | Yes | `holding`, `input`, `coil`, `discrete_input` |
| `address` | Yes | 0-based PDU address |
| `data_type` | Yes | `uint16`, `int16`, `uint32`, `int32`, `float32`, `bool` |
| `bit_index` | For bool | Which bit (0–15) within the holding/input register |
| `word_order` | For 32-bit | `big_endian` or `little_endian` |
| `scale` | No | Display-only: `engineering = raw × scale` |
| `unit` | No | Display-only: `V`, `A`, `kW`, `°C`, etc. |
| `section` | No | Groups signals in the UI (default: `General`) |
| `description` | No | Free text |
| `default_value` | No | Raw value loaded by Simulate (float for float32) |
| `writable` | No | Informational; simulator accepts writes regardless |

**Word order** controls the order of the two 16-bit words for 32-bit and float values. Byte order within each word is always big-endian (Modbus standard).

- `big_endian` — high word at lower address (most PLCs, meters, standard Modbus)
- `little_endian` — low word at lower address (Sungrow inverters, some ABB devices)

**Bool signals in holding/input registers** share the 16-bit register — multiple bool signals at the same address with different `bit_index` values are allowed. **Bool signals in coils/discrete inputs** each get their own address; `bit_index` must be empty.

---

## 8. Crash recovery (--restore)

If the VM process crashes or is restarted without reverting the snapshot, the project files are still on disk but the simulation is not running. Use `--restore` to bring it back to RUNNING without going through the wizard again:

```bash
sudo .venv/bin/python -m modbus_sim.main --restore --config ./project
```

The engine re-reads the locked config and signal files and starts the Modbus servers directly. Network interfaces from the previous run are still present (the kernel retains them), so startup is typically instant.

This is also useful after a `systemd` service restart when the VM was not reverted. Add `--restore` to the `ExecStart` line in the service file if you want automatic recovery:

```ini
ExecStart=/usr/bin/python3 -m modbus_sim.main --restore --config /opt/modbus-sim/project
```

> **Note:** `--restore` skips the config-lock check. Only use it when you know the project directory contains a valid, intended config.

---

## 9. Headless operation (curl / scripts)

Start with `--headless` to serve only the API (no web UI):

```bash
sudo .venv/bin/python -m modbus_sim.main --headless --config ./project
```

Drive the full lifecycle with curl:

```bash
# Upload config
curl -F file=@sim_config.yaml http://<host>:5000/api/setup/config

# Upload signal files (one per device)
curl -F file=@devices/inv1.csv http://<host>:5000/api/setup/signals/col1_inv1

# Start simulation
curl -X POST http://<host>:5000/api/setup/start

# Read values
curl http://<host>:5000/api/devices/col1_inv1/values

# Set a value
curl -X POST -H "Content-Type: application/json" \
  -d '{"name":"Active Power","value":1500000}' \
  http://<host>:5000/api/devices/col1_inv1/set
```

### Full API reference

| Method & path | State | Description |
|---|---|---|
| `GET /api/state` | any | Engine state + lock status |
| `GET /api/health` | any | `{"ok": true}` |
| `POST /api/setup/config` | SETUP | Upload + lock `sim_config.yaml` (multipart `file`) |
| `POST /api/setup/signals/{id}` | SETUP | Upload a device signal CSV (multipart `file`) |
| `GET /api/setup/status` | SETUP | Wizard progress — which devices are ready |
| `POST /api/setup/start` | SETUP | Start network + Modbus servers → RUNNING |
| `POST /api/import/zenon/parse` | SETUP | Parse a Zenon export (multipart `file`) |
| `POST /api/import/zenon/generate` | SETUP | Generate config + signals from parsed Zenon data |
| `GET /api/devices` | RUNNING | All devices and status |
| `GET /api/devices/{id}/signals` | RUNNING | Signal definitions for a device |
| `GET /api/devices/{id}/values` | RUNNING | Current register values by signal name |
| `POST /api/devices/{id}/set` | RUNNING | Set one signal `{"name": …, "value": …}` |
| `POST /api/devices/{id}/simulate` | RUNNING | Load defaults for one device |
| `POST /api/devices/{id}/clear` | RUNNING | Zero registers for one device |
| `POST /api/simulate` | RUNNING | Load defaults for all devices |
| `POST /api/clear` | RUNNING | Zero all devices |
| `POST /api/devices/{id}/signals` | RUNNING | Hot reload from JSON `{"signals": […]}` |
| `POST /api/devices/{id}/signals/upload` | RUNNING | Hot reload from CSV (multipart `file`) |
| `GET /api/devices/{id}/signals/download` | RUNNING | Download device CSV |
| `GET /api/config` | RUNNING | Project summary |
| `GET /api/network` | RUNNING | VLAN interfaces and assigned IPs |
| `POST /api/stop` | RUNNING | Graceful shutdown |

Status codes: `400` validation error (with row/column detail), `404` unknown device or signal, `409` wrong engine state, `500` network or startup failure.

---

## 10. Developer reference

### Architecture

Two decoupled layers with a REST API as the contract:

```
Web UI (browser — Bootstrap 5, vanilla JS)    modbus_sim/webui/
  app.js         global state polling, fetch helpers
  setup.js       setup wizard logic
  runtime.js     signal table, value editing, auto-refresh
  editor.js      tabular signal editor modal
  zenon_import.js  Zenon CSV import modal

Engine (Python)                               modbus_sim/
  main.py            entry point, CLI, lifecycle, banners
  api_server.py      Flask REST API (+ serves UI unless --headless)
  state_machine.py   SETUP → RUNNING; orchestration
  config_loader.py   parse/validate sim_config.yaml
  signal_loader.py   parse/validate device signal CSVs
  register_map.py    per-device thread-safe register store
  modbus_server.py   pymodbus async TCP servers (one per ip:port)
  network_manager.py VLAN + IP setup via iproute2 (Linux, root)

Zenon importer                                import/
  zenon_csv.py       standalone parser: Zenon 15 CSV → config + signal files
```

### Engine states

| State | Active |
|---|---|
| **SETUP** | REST API, config validation, file uploads |
| **RUNNING** | All of the above + network interfaces, register maps, Modbus servers |

The transition is one-way. There is no path back to SETUP without restarting the process and reverting the VM snapshot.

### CLI flags

| Flag | Effect |
|---|---|
| *(none)* | Full mode — Engine + Web UI |
| `--headless` | Engine only; `GET /` returns 404 |
| `--config DIR` | Project directory (default `./project`) |
| `--port N` / `--host H` | API/UI bind port and address (default `0.0.0.0:5000`) |
| `--no-network` | Skip VLAN/IP setup; bind to already-present IPs. For development without root or without the USB-C NIC. |
| `--restore` | Re-load a locked project from disk → RUNNING directly (crash recovery) |
| `--reset` | Clear a locked project directory and start fresh (dev only) |

### Running tests

```bash
bash run-tests.sh
# or
.venv/bin/python -m pytest -q
```

Covers: register encoding (all six types, both word orders, bit-packed bools), CSV/YAML validation, setup state machine, API endpoints, web UI serving, Zenon CSV parsing and generation, and a full end-to-end runtime test that reads simulated registers over loopback with a real pymodbus client.

### Local development (no hardware, no root)

```bash
.venv/bin/python -m modbus_sim.main --no-network --config ./project --port 5000
```

Open `http://localhost:5000`. Use a config with device IPs `127.0.0.1` and ports ≥ 1024 (e.g. 5020).

### Project layout

```
modbus_sim/            engine package
  webui/               index.html, app.js, setup.js, runtime.js, editor.js, zenon_import.js
import/
  zenon_csv.py         Zenon 15 CSV → sim_config.yaml + device signal CSVs
project/               runtime project dir (volatile): sim_config.yaml, devices/*.csv, .config_locked
tests/                 pytest suite
docs/
  REQUIREMENTS.md      full specification
requirements.txt       pymodbus>=3.13.0, flask>=3.0.0, pyyaml>=6.0
requirements-dev.txt   + pytest
start.sh               production (sudo, VLAN, web UI + API)
start-headless.sh      headless (sudo, VLAN, API only)
start-dev.sh           dev (--no-network, loopback, web UI + API)
run-tests.sh           pytest runner
```

> **pymodbus 3.13+** — The Modbus servers use pymodbus's `SimData`/`SimDevice` model
> (the classic `ModbusSlaveContext` API was removed in 3.11). `requirements.txt` pins
> `pymodbus>=3.13.0`.
