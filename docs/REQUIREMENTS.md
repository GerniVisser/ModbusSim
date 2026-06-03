# Generic Modbus TCP Simulator — Requirements Document

**Version:** 3.0  
**Date:** 2026-05-28  
**Purpose:** Complete specification for building a generic, project-agnostic Modbus TCP device simulator running natively on Linux inside a VMware virtual machine, with full 802.1Q VLAN injection capability via a USB-C to Ethernet adapter passed through directly to the VM. All user interaction occurs through a web-based interface — users do not interact with Linux directly.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [System Architecture](#3-system-architecture)
4. [Physical and VM Setup](#4-physical-and-vm-setup)
5. [File and Folder Structure](#5-file-and-folder-structure)
6. [Configuration — sim_config.yaml](#6-configuration--sim_configyaml)
7. [Signal Definition — Device CSV Format](#7-signal-definition--device-csv-format)
8. [Network Manager](#8-network-manager)
9. [Core Engine — RegisterMap](#9-core-engine--registermap)
10. [Modbus Server](#10-modbus-server)
11. [Engine REST API](#11-engine-rest-api)
12. [Web UI — Frontend](#12-web-ui--frontend)
13. [Operating Modes](#13-operating-modes)
14. [Import Utilities](#14-import-utilities)
15. [Installation and Running](#15-installation-and-running)
16. [Non-Functional Requirements](#16-non-functional-requirements)
17. [Implementation Constraints](#17-implementation-constraints)
18. [Acceptance Criteria](#18-acceptance-criteria)

---

## 1. Project Overview

This project is a **generic Modbus TCP device simulator** that runs natively as a Python application on a Linux operating system hosted inside a VMware virtual machine on a Windows PC.

The simulator can simultaneously simulate one or more Modbus TCP field devices — inverters, meters, PLCs, controllers, or any Modbus-capable device — without requiring physical hardware. It is completely project-agnostic. All device and signal knowledge comes from configuration files supplied by the user.

The simulator manages its own network configuration at startup. It creates VLAN subinterfaces and assigns IP addresses to a dedicated USB-C to Ethernet adapter that is passed through directly to the Linux VM. This allows the simulator to inject 802.1Q VLAN-tagged Modbus TCP traffic directly onto a managed switch, making simulated devices appear on the correct network segments exactly as real hardware would.

The VM is dedicated exclusively to this simulator. Network state is never cleaned up by the application. Instead, a **VMware snapshot** of the clean VM state is taken once during setup. Switching to a different project config is done by reverting to that snapshot — the VM itself is the cleanup mechanism. This removes an entire class of application complexity (teardown logic, state tracking, edge case handling) and guarantees a provably clean environment every time.

### Engine and Web UI Separation

The application is architected as two distinct, decoupled layers:

- The **Engine** is the core Python application. It manages the network, runs the Modbus servers, maintains register state, and exposes a REST API. It can run completely headlessly — no browser, no UI dependency.
- The **Web UI** is a browser-based application that consumes the Engine's REST API. It is the primary user interface but is not required for the Engine to function. Other clients (CLI tools, scripts, other UIs) can use the same REST API to drive the Engine.

This separation means the Web UI is a swappable layer. The Engine is the system of record.

### User Interaction Model

The target users are not Linux administrators. They never SSH into the VM, edit config files in a terminal, or run shell commands. All interaction happens through the web browser:

1. The Engine starts in **setup mode** when the VM boots — only the web server is listening, no simulation is running.
2. The user navigates to the web UI in a browser from the Windows host.
3. A **setup wizard** guides the user through uploading `sim_config.yaml`, uploading signal CSV files for each device, and starting the simulation.
4. Once the simulation is started, the UI transitions to **runtime mode** showing devices, signals, and live values.
5. The user can edit signal definitions through a browser-based tabular editor; saving triggers a hot reload of the affected device with no engine restart.
6. To switch to a different project, the user reverts the VM to the clean snapshot using VMware on the Windows host.

### Config Lock

Once `sim_config.yaml` is uploaded and accepted, it is **permanently locked** for the lifetime of that VM session. There is no UI to re-upload it. This enforces the snapshot workflow — changing the network topology of the simulation always requires reverting the VM, guaranteeing a clean starting state.

Signal CSV files are **not** locked. They can be re-uploaded, edited in-browser, and hot-reloaded as often as needed during a session.

---

## 2. Goals and Non-Goals

### Goals

- Simulate any number of Modbus TCP devices simultaneously, each on its own IP address and VLAN.
- Inject 802.1Q VLAN-tagged Modbus traffic onto a managed switch via a USB-C to Ethernet adapter passed through to a Linux VM.
- Accept signal definitions in a simple human-readable CSV format that an engineer fills in from a device Modbus protocol PDF.
- Support all common Modbus data types: `uint16`, `int16`, `uint32`, `int32`, `float32`, `bool`.
- Support all four Modbus register tables: holding registers, input registers, coils, discrete inputs.
- Support configurable 32-bit word order per signal (big-endian and little-endian).
- Manage network interfaces (VLAN creation, IP assignment) at startup only.
- Provide a browser-based web UI as the primary user interface — users never interact with Linux directly.
- Provide a guided setup wizard in the web UI for uploading config and signal files.
- Support hot reload of signal definitions when edited through the web UI.
- Architect the application as a decoupled Engine + UI, with a REST API as the contract between them.
- Support headless mode where the Engine runs without serving the web UI.
- Run natively on Linux — no Docker, no containers.
- Include an optional VLAN-less mode for testing without a managed switch (plain IP aliases only).
- Include an optional importer utility that converts Zenon SCADA `.txt` export files to the standard signal CSV format.

### Non-Goals

- This project does **not** use Docker or any container runtime.
- This project does **not** implement Modbus RTU (serial). TCP only.
- This project does **not** implement write simulation logic beyond updating the register value.
- This project does **not** require or depend on any specific SCADA platform.
- This project does **not** provide signal definitions for any device — that is always the user's responsibility.
- This project does **not** clean up network interfaces on shutdown. The dedicated VM and its snapshot mechanism are responsible for state management between sessions.
- This project does **not** track network state in files or perform startup reconciliation. A clean VM state is guaranteed by reverting to the VMware snapshot before each new session.
- This project does **not** allow re-uploading or editing `sim_config.yaml` after it has been accepted in the setup wizard. Changing the config requires a VM snapshot revert.
- This project does **not** require users to use a terminal, SSH, or edit files directly on the Linux VM. All workflows are browser-based.

---

## 3. System Architecture

### Layered Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  WEB UI LAYER (browser)                                             │
│  ─────────────────────────────────────────────                      │
│  - Setup wizard (config upload, signal upload)                      │
│  - Device list, signal viewer, live value editing                   │
│  - Tabular signal CSV editor with hot reload                        │
│  - Simulate/Clear controls, Start/Stop engine                       │
│  Consumes the Engine REST API. Stateless.                           │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │  HTTP / JSON
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ENGINE LAYER (Python process)                                      │
│                                                                     │
│  main.py  (orchestrator)                                            │
│  ├── api_server.py        Flask REST API (+ serves UI if not headless) │
│  ├── state_machine.py     setup → running state transitions         │
│  ├── config_loader.py     reads and validates sim_config.yaml       │
│  ├── signal_loader.py     reads and validates device CSV files      │
│  ├── network_manager.py   creates VLAN interfaces and assigns IPs   │
│  ├── register_map.py      per-device thread-safe register store     │
│  └── modbus_server.py     one pymodbus TCP server per device        │
│                                                                     │
│  Owns: filesystem, network, Modbus servers, register state.         │
│  Authoritative source of truth.                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### The Two Engine States

The Engine operates as a state machine with two main states:

| State | Active Components | What User Can Do |
|---|---|---|
| **SETUP** | api_server, state_machine | Upload config, upload signal files, validate, start simulation |
| **RUNNING** | api_server, state_machine, network_manager, register_maps, modbus_servers | View signals, edit values, simulate, clear, edit signal files (hot reload), stop simulation |

The transition from SETUP to RUNNING happens when:
1. Config has been uploaded and accepted (and is now locked).
2. Every device defined in the config has a valid signal CSV uploaded.
3. The user clicks "Start Simulation" in the UI (or calls `POST /api/setup/start`).

On a successful transition the engine:
1. Calls `NetworkManager.setup()` to create VLAN interfaces and assign IPs.
2. Builds all `RegisterMap` instances.
3. Starts all Modbus TCP server coroutines.

There is no transition back to SETUP without restarting the process and reverting the VM.

### Process Startup Sequence

When the Python process starts (typically launched by a systemd service when the VM boots):

```
1.  Parse command-line arguments (--headless, --setup-only, etc.)
2.  Check for an existing locked config in the project directory
    If found AND no --reset flag: refuse to start, print error
    explaining that the VM must be reverted to its clean snapshot
3.  Initialize the state machine in SETUP state
4.  Start the REST API server (Flask) on the configured port
    Bound to the management interface so the user can reach it from Windows
5.  Start the web UI static file server (unless --headless)
6.  Print banner: "Engine ready in SETUP state. Open http://<ip>:5000"
7.  Wait — Engine does nothing else until the user starts the setup wizard
```

### Setup Wizard Sequence (driven by user via Web UI)

```
1.  User uploads sim_config.yaml via POST /api/setup/config
    Engine validates → on success, writes file + lock marker to disk
2.  Engine returns list of devices needing signal files
3.  For each device, user uploads signal CSV via POST /api/setup/signals/{id}
    Engine validates each file → on success, writes to disk
4.  User clicks "Start" — Web UI calls POST /api/setup/start
5.  Engine validates that all devices have signal files
6.  Engine transitions to RUNNING state:
      a. NetworkManager.setup() — create VLAN interfaces, assign IPs
      b. Build one RegisterMap per device
      c. Start all Modbus TCP server coroutines (asyncio.gather)
7.  Engine returns success, UI transitions to runtime view
```

### Signal File Hot Reload

When a user edits a device's signal definitions in the Web UI editor and saves:

```
1.  Web UI sends updated signal data via POST /api/devices/{id}/signals
2.  Engine validates the new signal list (full schema check)
3.  If valid:
      a. Write updated CSV to disk (overwrite existing file)
      b. Build new RegisterMap for the device
      c. Atomically swap the new RegisterMap into the Modbus server
         context for that device's (ip, port)
      d. Other devices are not affected
4.  If invalid:
      a. Reject the change with detailed validation errors
      b. Disk file remains unchanged
      c. Live simulation continues with previous signal list
```

The hot reload happens without restarting the engine, the Modbus servers, or affecting any other device. The Modbus TCP connections from clients remain open.

### Shutdown Sequence (CTRL+C, SIGTERM, or POST /api/stop)

```
1.  State machine transitions to STOPPING
2.  asyncio event loop cancelled — Modbus servers stop accepting connections
3.  Flask shuts down
4.  Process exits

Network interfaces (VLAN subinterfaces and IP addresses) are intentionally
left in place. The VM is dedicated to this simulator. To switch to a
different project configuration, revert the VM to the clean snapshot.
```

### Engine/UI Decoupling

The Web UI communicates with the Engine exclusively through the REST API documented in Section 11. This means:

- The Web UI can be developed, replaced, or extended without modifying the Engine.
- Other clients (scripts, CLI tools, automated test suites) can drive the Engine using the same API.
- The Engine is fully functional with no UI present (headless mode).
- The Web UI is stateless — all state lives in the Engine.

### Network Architecture

The Linux VM has two network interfaces:

| Interface | Purpose | VMware Type |
|---|---|---|
| `eth0` (or `ens33`) | Management — web UI access, SSH | Bridged or Host-Only |
| USB-C NIC (e.g. `eth1`, `enp0s20f0u1`) | Modbus traffic + VLAN injection | USB Passthrough |

All Modbus servers and VLAN interfaces are created on the USB-C NIC only. The management interface is never touched by the simulator.

### VLAN Mode vs No-VLAN Mode

VLAN mode is automatically enabled if any device in `sim_config.yaml` has a non-zero `vlan` field. If no devices have a `vlan` value, the simulator assigns IP addresses directly to the traffic interface (plain IP aliases, no tagging).

```
VLAN mode:
  USB-C NIC (eth1)
  ├── eth1.100  → 10.4.1.10 (Inverter 1)
  │             → 10.4.1.11 (Inverter 2)
  └── eth1.200  → 10.4.2.50 (SCB Panel 1)
        │
   Managed switch (trunk port)
   ├── VLAN 100 access ports → zenon inverter network
   └── VLAN 200 access ports → zenon SCB network

No-VLAN mode:
  USB-C NIC (eth1)
  ├── 10.4.1.10 (Inverter 1)
  ├── 10.4.1.11 (Inverter 2)
  └── 10.4.2.50 (SCB Panel 1)
        │
   Unmanaged switch or direct connection
```

### Direct IP Binding

Unlike a Docker-based approach, there is no port remapping. Each Modbus server binds directly and exclusively to its device IP and port. This is possible because the Linux VM owns the USB-C NIC and can assign any IP to it.

```python
# Each server coroutine binds to the real device IP
await asyncio.gather(
    StartAsyncTcpServer(context=ctx_inv1, address=("10.4.1.10", 502)),
    StartAsyncTcpServer(context=ctx_inv2, address=("10.4.1.11", 502)),
    StartAsyncTcpServer(context=ctx_scb1, address=("10.4.2.50", 502)),
)
```

---

## 4. Physical and VM Setup

This section describes the physical hardware and VMware configuration required before the simulator application can run. This is a one-time setup.

### Hardware Required

| Item | Purpose | Notes |
|---|---|---|
| Windows PC | Runs VMware Workstation | Engineering laptop or workstation |
| USB-C to Gigabit Ethernet adapter | Dedicated Modbus traffic NIC | Passed through to Linux VM |
| Managed switch (optional) | VLAN enforcement and network distribution | Required for VLAN mode only |

### VMware VM Configuration

**Operating System:** Ubuntu Server 22.04 LTS or 24.04 LTS (recommended)  
**Resources:** 2 GB RAM minimum, 2 vCPUs, 20 GB disk  

**Network Adapters:**

| Adapter | VMware Type | Purpose |
|---|---|---|
| Network Adapter 1 | Bridged or Host-Only | Management — used to access the web UI and SSH from the Windows host |
| USB-C NIC | USB Passthrough (see below) | Modbus traffic — dedicated to VLAN injection |

**Do not add a second VMware virtual network adapter for Modbus traffic.** The USB-C NIC must be passed through directly.

### Configuring USB-C NIC Passthrough in VMware

1. Connect the USB-C to Ethernet adapter to the Windows PC.
2. In VMware Workstation, open **VM → Settings → USB Controller**.
3. Set USB compatibility to **USB 3.1**.
4. Start the Linux VM.
5. In the VMware toolbar, select **VM → Removable Devices → [USB-C NIC name] → Connect (Disconnect from Host)**.
6. Inside the Linux VM, run `ip link` to confirm the adapter is visible and note its interface name (e.g. `eth1`, `enp0s20f0u1`, `usb0`).
7. Record this interface name — it must be set as `traffic_interface` in `sim_config.yaml`.

> **Note:** The interface name assigned by Linux depends on the adapter model and udev rules. It may change if the adapter is connected to a different USB port. Always verify with `ip link` before running the simulator.

### Managed Switch Configuration (VLAN Mode)

Connect the USB-C NIC to a trunk port on the managed switch. Configure the trunk port to carry all VLANs used in the simulation. Configure access ports for the VLAN network segments that zenon or other clients will connect to.

Example for a TP-Link TL-SG108E or similar:

| Port | Type | VLAN(s) | Connected to |
|---|---|---|---|
| Port 1 | Trunk | 100, 200 | USB-C NIC (simulator) |
| Port 2 | Access | VLAN 100 | zenon PC (inverter network) |
| Port 3 | Access | VLAN 200 | zenon PC (SCB network) |
| Port 4 | Access | VLAN 100 | Network analyser / laptop |

### Network Interface Persistence

All network changes made by the simulator (`ip link add`, `ip addr add`) use the Linux kernel's in-memory network state. They are **never written to any configuration file**. The simulator does not clean up these interfaces on exit — they remain assigned until the VM is reverted to its clean snapshot or rebooted.

The VM is dedicated exclusively to this simulator. No other application runs on it that would be affected by the assigned IP addresses or VLAN interfaces between sessions.

### VMware Snapshot Workflow

This is the standard operating procedure for managing simulation sessions. The snapshot replaces all application-level cleanup logic.

**One-time setup (performed once when the VM is first created):**

```
1.  Install Ubuntu Server on the VM
2.  Configure management NIC (static IP for web UI and SSH access)
3.  Connect USB-C to Ethernet adapter via USB passthrough
4.  Install Python and simulator dependencies (run install.sh)
5.  Confirm USB-C NIC is visible (ip link) but has no IP assigned
6.  Confirm management NIC has its static IP and is reachable from Windows host
7.  Take VMware snapshot → name it exactly: "Simulator Clean State"
```

This snapshot is permanent and must not be deleted. It defines the known-good baseline state for every simulation session.

**Starting a simulation session:**

```
1.  In VMware: Snapshot → Revert to "Simulator Clean State"
    (takes approximately 5–15 seconds)
2.  Power on the VM. The Engine starts automatically (systemd service)
    in SETUP state. The web UI is reachable but no simulation is running.
3.  From the Windows host browser, navigate to http://<vm-ip>:5000
4.  Setup wizard appears:
      Step 1: Upload sim_config.yaml
      Step 2: Upload signal CSV for each device listed in the config
      Step 3: Click "Start Simulation"
5.  Engine transitions to RUNNING. Simulator is live.
6.  Connect zenon or other Modbus clients.
```

**Switching to a different project config:**

```
1.  In VMware: Snapshot → Revert to "Simulator Clean State"
2.  Power on the VM. Engine starts in SETUP state with no locked config.
3.  Browser → upload the new project's config + signal files → Start.
```

**The revert step is mandatory before loading a different config.** The Engine refuses to start if a locked config already exists in the project directory. This enforces the workflow at the Engine level — there is no way to load a different config without reverting.

### What the Clean Snapshot State Must Contain

| Item | State |
|---|---|
| Ubuntu Server OS | Installed and fully updated |
| Python 3.10+ | Installed |
| pymodbus, flask, pyyaml | Installed via requirements.txt |
| Simulator code | Installed at `/opt/modbus-sim` or equivalent |
| Management NIC | Configured with static IP, reachable from Windows host |
| USB-C NIC | Visible in `ip link` output, **no IP address assigned** |
| VLAN subinterfaces | **None** — `ip link` shows only physical interfaces |
| systemd simulator service | **Enabled and running** — Engine auto-starts in SETUP state on boot |
| Project directory | Empty — no sim_config.yaml, no signal CSVs, no lock file |
| sim_config.yaml | **Not present** — uploaded via web UI each session |
| devices/*.csv | **Not present** — uploaded via web UI each session |
| .config_locked | **Not present** — created when config is uploaded |

---

## 5. File and Folder Structure

```
modbus-sim/
│
├── REQUIREMENTS.md                  ← this document
│
├── project/                         ← runtime project directory (volatile)
│   ├── sim_config.yaml              ← uploaded by user via Web UI
│   ├── devices/                     ← uploaded signal CSVs
│   │   ├── inverter_1.csv
│   │   └── energy_meter.csv
│   └── .config_locked               ← marker file — exists once config accepted
│
├── modbus_sim/                      ← Python application package
│   ├── __init__.py
│   ├── main.py                      ← entry point, CLI args, lifecycle
│   ├── state_machine.py             ← SETUP → RUNNING state transitions
│   ├── api_server.py                ← Flask REST API + UI static files
│   ├── config_loader.py             ← parses and validates sim_config.yaml
│   ├── signal_loader.py             ← parses and validates device CSV files
│   ├── network_manager.py           ← VLAN interface and IP address setup
│   ├── register_map.py              ← per-device thread-safe register store
│   ├── modbus_server.py             ← pymodbus async TCP server (one per device)
│   └── webui/
│       ├── index.html               ← single-page web UI (Bootstrap 5, vanilla JS)
│       ├── setup.js                 ← setup wizard logic
│       ├── runtime.js               ← runtime view logic (signal table, editing)
│       └── editor.js                ← tabular signal CSV editor
│
├── importers/                       ← optional signal import utilities (CLI tools)
│   ├── zenon_txt.py                 ← converts Zenon .txt export → standard CSV
│   └── csv_validator.py             ← validates a signal CSV against the schema
│
├── systemd/
│   └── modbus-sim.service           ← systemd unit file, installed during VM setup
│
├── install.sh                       ← one-time installation script (VM setup)
└── requirements.txt                 ← pymodbus>=3.6.0, flask>=3.0.0, pyyaml>=6.0
```

---

## 6. Configuration — sim_config.yaml

### Purpose

The single file that defines the entire simulation. It is **uploaded via the Web UI setup wizard** during the SETUP phase. Once uploaded and accepted, it is locked for the lifetime of the VM session — a snapshot revert is required to load a different config.

The file format is identical whether the user edits it externally in a text editor before uploading or generates it from another tool. The Engine validates it on upload regardless of source.

### Full Schema

```yaml
project:
  name: string          # required — human-readable project name
  description: string   # optional
  version: string       # optional — e.g. "1.0"

network:
  traffic_interface: string       # required — Linux interface name of the USB-C NIC
                                  # e.g. "eth1" or "enp0s20f0u1"
                                  # find with: ip link
  management_interface: string    # optional — interface for web UI binding
                                  # default: web UI binds to 0.0.0.0
  web_ui_port: integer            # optional — default 5000
  vlan_mode: string               # optional — "auto" | "enabled" | "disabled"
                                  # auto (default): enabled if any device has vlan set

devices:
  - id: string          # required — unique identifier, no spaces
    name: string        # required — human-readable display name
    ip: string          # required — IPv4 address to assign to the traffic interface
    port: integer       # required — Modbus TCP port (502 standard, or 5020 if not root)
    unit_id: integer    # required — Modbus Unit ID / Slave ID (1–247)
    vlan: integer       # optional — VLAN ID for 802.1Q tagging (0 or absent = no VLAN)
    prefix_length: integer # optional — subnet prefix length, default 24
    signals_file: string   # required — path to signal CSV relative to this file
    description: string    # optional
```

### Validation Rules

- `id` must be unique across all devices.
- `traffic_interface` must name an existing Linux network interface at startup. If it does not exist, startup must fail with a clear error.
- `ip` must be a valid IPv4 address string.
- `port` must be 1–65535. Binding to ports below 1024 requires the process to run as root.
- `unit_id` must be 1–247.
- `signals_file` must resolve to an existing file at startup. If the file does not exist, startup must fail with a clear error naming the device and file.
- Duplicate `id` values must be rejected.
- Duplicate `(ip, port, unit_id)` combinations must be rejected.
- Two devices may share the same `(ip, port)` only if they have different `unit_id` values.
- Two devices may share the same `vlan` value — they will share a VLAN subinterface.
- If `vlan_mode` is `"disabled"`, all `vlan` values are ignored and IP aliases are assigned directly to `traffic_interface`.

### Example

```yaml
project:
  name: "Du Plessis PV — Facility SCADA"
  description: "100MW PV Plant Modbus Simulation"
  version: "1.0"

network:
  traffic_interface: eth1
  management_interface: eth0
  web_ui_port: 5000
  vlan_mode: auto

devices:
  - id: col1_inv1
    name: "COL1 — Inverter ITS-5-2"
    ip: 10.4.1.10
    port: 502
    unit_id: 1
    vlan: 100
    signals_file: devices/col1_inv1.csv
    description: "Sungrow SG8800-MV Collector 1 Inverter 1"

  - id: col1_inv2
    name: "COL1 — Inverter ITS-5-3"
    ip: 10.4.1.11
    port: 502
    unit_id: 2
    vlan: 100
    signals_file: devices/col1_inv2.csv

  - id: col1_scb1
    name: "COL1 — SCB Panel 1"
    ip: 10.4.2.50
    port: 502
    unit_id: 200
    vlan: 200
    signals_file: devices/col1_scb1.csv

  - id: energy_meter
    name: "Grid Energy Meter"
    ip: 10.4.3.10
    port: 502
    unit_id: 1
    vlan: 300
    signals_file: devices/energy_meter.csv
```

---

## 7. Signal Definition — Device CSV Format

### Purpose

Each device has one signal CSV file listing every Modbus register the simulator must respond to. The user prepares this from the device's Modbus protocol documentation (or by using the Zenon importer in Section 14) and uploads it via the Web UI during the SETUP phase. After the simulation is running, the user can also edit signals through the Web UI's tabular editor — saving triggers a hot reload of that device.

The Engine has no built-in knowledge of any device register map. All Modbus behaviour is driven entirely by the signal CSV contents.

### CSV Column Definitions

The first row must be a header row with exactly these column names (case-sensitive):

| Column | Required | Type | Description |
|---|---|---|---|
| `name` | Yes | string | Human-readable signal name. Unique within the device. |
| `register_type` | Yes | enum | `holding`, `input`, `coil`, or `discrete_input`. |
| `address` | Yes | integer | Modbus PDU address, 0-based. |
| `data_type` | Yes | enum | `uint16`, `int16`, `uint32`, `int32`, `float32`, or `bool`. |
| `bit_index` | Conditional | integer 0–15 | Required when `data_type` is `bool`. Which bit within the register. Must be empty for all other types. |
| `word_order` | Conditional | enum | Required when `data_type` is `uint32`, `int32`, or `float32`. Must be `big_endian` or `little_endian`. Ignored for 16-bit types and bool. |
| `scale` | No | float | Display scale factor. `engineering_value = raw_value × scale`. Default `1`. The simulator stores raw values. This is display-only. |
| `unit` | No | string | Engineering unit for display (e.g. `V`, `A`, `kW`, `Hz`, `°C`). |
| `section` | No | string | UI grouping label (e.g. `Grid`, `DC`, `Thermal`, `Status`). Defaults to `General`. |
| `description` | No | string | Human-readable description. |
| `default_value` | No | number | Raw register value loaded when Simulate is pressed. For `float32` this is the float value. For `bool` must be `0` or `1`. Default `0`. |
| `writable` | No | bool | Informational only — whether the real device accepts writes. The simulator accepts writes to all registers regardless. Default `false`. |

### Data Type Reference

| data_type | Registers | Signed | Notes |
|---|---|---|---|
| `uint16` | 1 | No | Unsigned 16-bit integer. Range 0–65535. |
| `int16` | 1 | Yes | Signed 16-bit integer. Two's complement. Range −32768–32767. |
| `uint32` | 2 | No | Unsigned 32-bit integer. Word order required. Range 0–4294967295. |
| `int32` | 2 | Yes | Signed 32-bit integer. Two's complement. Word order required. |
| `float32` | 2 | Yes | IEEE 754 single-precision float. Word order required. |
| `bool` | 1 (1 bit) | No | A single bit within a 16-bit register. Multiple bool signals may share the same `address` if they have different `bit_index` values. |

### Word Order Reference

| word_order | Word at lower address | Word at higher address | Common devices |
|---|---|---|---|
| `big_endian` | High 16 bits | Low 16 bits | Most PLCs, most meters, standard Modbus |
| `little_endian` | Low 16 bits | High 16 bits | Sungrow inverters, some ABB devices |

Byte order within each 16-bit word is always big-endian (Modbus standard). `word_order` controls only the order of the two 16-bit words for 32-bit and float values.

### Scale Factor and default_value Notes

- The simulator always stores and transmits **raw** register values.
- The `scale` field is used only by the web UI to display a scaled engineering value alongside the raw value.
- `default_value` is always the **raw** register value, except for `float32` where it is the float value (the loader converts it to a raw IEEE 754 bit pattern on import).

### CSV Example

```csv
name,register_type,address,data_type,bit_index,word_order,scale,unit,section,description,default_value,writable
Grid Voltage L1,holding,1000,uint16,,big_endian,0.1,V,Grid,Phase L1-N voltage,4000,false
Grid Voltage L2,holding,1001,uint16,,big_endian,0.1,V,Grid,Phase L2-N voltage,4000,false
Grid Voltage L3,holding,1002,uint16,,big_endian,0.1,V,Grid,Phase L3-N voltage,4000,false
Grid Frequency,holding,1003,uint16,,big_endian,0.01,Hz,Grid,Grid frequency,5000,false
Active Power,holding,1004,int32,,big_endian,1,W,Grid,Total active power,2000000,false
Reactive Power,holding,1006,int32,,big_endian,1,VAr,Grid,Total reactive power,0,false
Total Yield,holding,1010,uint32,,big_endian,1,kWh,Energy,Lifetime energy yield,10000,false
Cabinet Temp,holding,1020,int16,,big_endian,1,°C,Thermal,Cabinet temperature,35,false
DC Bus Voltage,holding,1030,float32,,little_endian,1,V,DC,DC bus voltage,600.0,false
Running Status,holding,1040,bool,0,,,,,Status,Device running (bit 0),1,false
Fault Status,holding,1040,bool,1,,,,,Status,Device fault (bit 1),0,false
Warning Status,holding,1040,bool,2,,,,,Status,Device warning (bit 2),0,false
Grid Connected,holding,1040,bool,3,,,,,Status,Grid connected (bit 3),1,false
Input Voltage,input,2000,uint16,,big_endian,0.1,V,DC,PV string input voltage,6000,false
Fan Enable,coil,0,bool,,,1,,Control,Cooling fan enable,1,false
Door Open,discrete_input,0,bool,,,1,,Safety,Cabinet door open,0,false
```

### Validation Rules

- `name` must be unique within the file.
- `address` must be a non-negative integer.
- `data_type` must be one of the six defined values.
- `bit_index` must be present and 0–15 when `data_type` is `bool`; must be empty otherwise.
- `word_order` must be `big_endian` or `little_endian` when `data_type` is `uint32`, `int32`, or `float32`; may be empty otherwise.
- No two non-bool signals may use the same `address` within a device if they overlap in register space.
- A `uint32`, `int32`, or `float32` signal at address `N` occupies registers `N` and `N+1`. No other non-bool signal may use address `N+1`.
- Multiple bool signals may share the same `address` but not the same `(address, bit_index)` pair.
- Validation errors must be reported with row number and column name. Files with any validation error cause startup to abort.

---

## 8. Network Manager

### Purpose

`network_manager.py` is responsible for Linux network interface setup at startup. It creates VLAN subinterfaces and assigns IP addresses to the USB-C NIC. It is the only module that makes system calls.

There is no teardown. The VM is dedicated to this simulator and network state is managed by reverting to the VMware snapshot between sessions. This keeps the module simple, with a single responsibility and no failure-prone cleanup paths.

### Class Interface

```python
class NetworkManager:
    def __init__(self, config: SimConfig): ...

    def setup(self) -> None:
        """
        Create VLAN interfaces and assign IPs.
        Called once at startup before Modbus servers start.
        Raises RuntimeError if any operation fails.
        """

    @property
    def is_vlan_mode(self) -> bool:
        """True if VLAN subinterfaces are being created."""
```

### VLAN Mode Logic

VLAN mode is active when:
- `network.vlan_mode` is `"enabled"` in the config, OR
- `network.vlan_mode` is `"auto"` (default) AND at least one device has a non-zero `vlan` value.

VLAN mode is disabled when:
- `network.vlan_mode` is `"disabled"`, OR
- `network.vlan_mode` is `"auto"` AND no device has a `vlan` value.

### setup() Implementation

```
1. Validate that traffic_interface exists:
   Run: ip link show <traffic_interface>
   If it fails: raise RuntimeError with a clear message

2. If VLAN mode:
   For each unique non-zero vlan_id across all devices (sorted):
     a. Derive subinterface name: <traffic_interface>.<vlan_id>
        Example: eth1.100
     b. Create VLAN subinterface:
        ip link add link <traffic_interface> name <subif> type vlan id <vlan_id>
     c. Bring it up:
        ip link set <subif> up

3. For each device:
   a. Determine target interface:
      - VLAN mode and device.vlan set: <traffic_interface>.<device.vlan>
      - Otherwise: <traffic_interface>
   b. Build CIDR: <device.ip>/<device.prefix_length>
   c. Assign IP:
      ip addr add <cidr> dev <target_interface>
```

### Permissions

All `ip link` and `ip addr` commands require root privileges. The simulator must be run with `sudo` or as root. If a network operation fails due to permissions, the error message must clearly state that root is required.

Port 502 also requires root. If the user configures a port below 1024 without root, startup must fail with a clear error before attempting to bind.

---

## 9. Core Engine — RegisterMap

### Purpose

`RegisterMap` is the central per-device data store. It holds raw 16-bit register values for one Modbus device and provides typed read/write access for all data types. Both the Modbus server and Flask web UI share the same instance. It must be fully thread-safe.

### Register Tables

Four independent data blocks, one per Modbus register type:

| register_type | Modbus table | Read FC | Write FC |
|---|---|---|---|
| `holding` | Holding Registers | FC3 | FC16 |
| `input` | Input Registers | FC4 | read-only |
| `coil` | Coils | FC1 | FC5 |
| `discrete_input` | Discrete Inputs | FC2 | read-only |

Each block spans from the lowest address used by that register type to the highest (inclusive of the second register for 32-bit types). If no signals use a given register type, that block is a minimal single-register block at address 0.

### Address Offset Correction

pymodbus 3.x adds 1 to PDU addresses internally when accessing data blocks. This must be corrected by subclassing `ModbusSlaveContext` and overriding `getValues` and `setValues` to remove the +1 offset. This correction must be applied to all four register type stores so that the PDU address from the Modbus request maps 1:1 to the address in the signal CSV.

### Data Type Read Logic

**uint16:** Read 1 register. Return raw value as unsigned integer (0–65535).

**int16:** Read 1 register. If `raw >= 32768`, return `raw - 65536`. Else return `raw`.

**uint32 big_endian:** Read 2 registers. `value = (reg[address] << 16) | reg[address+1]`.

**uint32 little_endian:** Read 2 registers. `value = (reg[address+1] << 16) | reg[address]`.

**int32:** Apply uint32 logic first. If `value >= 2^31`, return `value - 2^32`. Else return `value`.

**float32:** Apply uint32 logic to get a 32-bit unsigned integer. Unpack as IEEE 754:
```python
import struct
float_value = struct.unpack('>f', struct.pack('>I', uint32_value))[0]
```

**bool:** Read the 16-bit register at `address`. Return `bool((register_value >> bit_index) & 1)`.

### Data Type Write Logic

**uint16:** `value & 0xFFFF`. Write to register.

**int16:** If `value < 0`, add 65536. Mask to 16 bits. Write to register.

**uint32 big_endian:** `high = (value >> 16) & 0xFFFF`, `low = value & 0xFFFF`. Write `[high, low]` to `[address, address+1]`.

**uint32 little_endian:** `low = value & 0xFFFF`, `high = (value >> 16) & 0xFFFF`. Write `[low, high]` to `[address, address+1]`.

**int32:** If `value < 0`, add `2^32`. Apply uint32 write logic.

**float32:** Pack float as IEEE 754: `uint32_val = struct.unpack('>I', struct.pack('>f', value))[0]`. Apply uint32 write logic with configured word order.

**bool:** Atomic read-modify-write under lock. Read register. Set or clear `bit_index`. Write back.

### Thread Safety

A `threading.Lock` protects all multi-register operations (uint32, int32, float32) and all bool read-modify-write operations. Single-register reads and writes rely on the CPython GIL.

### Simulate and Clear

**`set_defaults(signals)`:** For each signal, write its `default_value` into the register(s). Signals with `default_value = 0` are written as zero (not skipped). Bool signals write the bit. float32 default values are already converted to raw IEEE 754 integers by the signal loader.

**`clear_all()`:** Zero every register in all four blocks under the lock.

---

## 10. Modbus Server

### Technology

Use `pymodbus >= 3.6.0` async TCP server API (`StartAsyncTcpServer` from `pymodbus.server`).

### Per-Device Direct Binding

Each device in the config gets its own `ModbusServerContext` and its own `StartAsyncTcpServer` coroutine. Each server binds **directly** to the device IP and port assigned by `NetworkManager`. There is no internal port remapping.

```python
async def run_all_servers(device_servers: list):
    """
    device_servers: list of (ModbusServerContext, ip, port)
    """
    tasks = [
        StartAsyncTcpServer(context=ctx, address=(ip, port))
        for ctx, ip, port in device_servers
    ]
    await asyncio.gather(*tasks)
```

The asyncio event loop runs in a dedicated daemon thread. Flask runs in the main thread.

### Device Grouping

Devices that share the same `(ip, port)` are served by a single `ModbusServerContext` containing multiple slave contexts keyed by unit ID. This avoids port binding conflicts.

### Supported Function Codes

| FC | Operation |
|---|---|
| FC1 | Read Coils |
| FC2 | Read Discrete Inputs |
| FC3 | Read Holding Registers |
| FC4 | Read Input Registers |
| FC5 | Write Single Coil |
| FC16 | Write Multiple Registers |

Requests for addresses outside the defined range return Modbus exception code 02 (Illegal Data Address).

### Startup Banner

```
╔══════════════════════════════════════════════════════════════════════╗
║  Generic Modbus TCP Simulator — Ready                                ║
╠══════════════════════════════════════════════════════════════════════╣
║  Project    : Du Plessis PV — Facility SCADA                         ║
║  VLAN mode  : enabled (eth1)                                         ║
╠══════════════════════════════════════════════════════════════════════╣
║  Device              IP              Port  Unit  VLAN  Signals       ║
║  col1_inv1           10.4.1.10        502     1   100      321       ║
║  col1_inv2           10.4.1.11        502     2   100      321       ║
║  col1_scb1           10.4.2.50        502   200   200      147       ║
║  energy_meter        10.4.3.10        502     1   300       42       ║
╠══════════════════════════════════════════════════════════════════════╣
║  Web UI     : http://192.168.1.x:5000                                ║
║  Stop       : CTRL+C                                                 ║
║  New config : Revert VM to "Simulator Clean State" snapshot first    ║
╚══════════════════════════════════════════════════════════════════════╝
```

The web UI URL in the banner should show the management interface IP (if known) rather than `0.0.0.0`.

---

## 11. Engine REST API

This is the formal contract between the Engine and any client (Web UI or otherwise). All endpoints return JSON. All POST bodies are JSON unless multipart upload is explicitly noted. HTTP 200 on success, appropriate 4xx for client errors, 409 for state-machine violations.

### State-Awareness

Every endpoint declares which Engine state(s) it is valid in. Calling an endpoint in the wrong state returns HTTP 409 Conflict with a body like:

```json
{ "error": "endpoint not available in SETUP state", "current_state": "SETUP" }
```

---

### System Endpoints (any state)

#### `GET /`
Serves `index.html` (the web UI entry point). Disabled in `--headless` mode.

#### `GET /api/state`
Returns the current engine state.
```json
{ "state": "SETUP", "config_locked": false, "signal_files_loaded": 0, "signal_files_required": 0 }
```
or
```json
{ "state": "RUNNING", "config_locked": true, "started_at": "2026-05-28T09:00:00Z" }
```

#### `GET /api/health`
Returns `{ "ok": true }`. Used by external monitoring or scripts.

---

### Setup Endpoints (SETUP state only)

#### `POST /api/setup/config`
Upload `sim_config.yaml`. Multipart form upload with a single field `file`.

Engine validates the file. On success:
- Writes the file to the project directory.
- Creates the `.config_locked` marker file.
- Returns the parsed device list and signal files needed.
- The config is now permanently locked for this session.

```json
{
  "ok": true,
  "project_name": "Du Plessis PV — Facility SCADA",
  "device_count": 4,
  "devices": [
    { "id": "col1_inv1", "name": "COL1 — Inverter ITS-5-2", "signals_file": "devices/col1_inv1.csv", "signals_uploaded": false },
    { "id": "col1_inv2", "name": "COL1 — Inverter ITS-5-3", "signals_file": "devices/col1_inv2.csv", "signals_uploaded": false }
  ],
  "locked": true
}
```

On validation failure: HTTP 400 with details.

```json
{
  "ok": false,
  "errors": ["device 'col1_inv1' has invalid IP address '10.4.1.999'"]
}
```

#### `POST /api/setup/signals/{device_id}`
Upload a signal CSV for a specific device. Multipart form upload with field `file`.

Engine validates the CSV against the schema in Section 7. On success, writes the file to disk and marks the device as having signals uploaded.

```json
{
  "ok": true,
  "device_id": "col1_inv1",
  "signal_count": 321,
  "all_devices_ready": false,
  "remaining": ["col1_inv2", "col1_scb1", "energy_meter"]
}
```

On validation failure: HTTP 400 with row-level errors.

```json
{
  "ok": false,
  "errors": [
    { "row": 15, "column": "data_type", "message": "uint64 is not a valid data type" },
    { "row": 22, "column": "bit_index", "message": "missing for bool signal 'Running Status'" }
  ]
}
```

#### `GET /api/setup/status`
Returns the current setup progress.

```json
{
  "config_uploaded": true,
  "config_locked": true,
  "devices_total": 4,
  "devices_ready": 2,
  "devices_pending": ["col1_scb1", "energy_meter"],
  "can_start": false
}
```

#### `POST /api/setup/start`
Transition from SETUP to RUNNING. Engine performs final validation, calls `NetworkManager.setup()`, builds RegisterMaps, and starts Modbus servers.

```json
{ "ok": true, "state": "RUNNING", "devices_started": 4 }
```

Returns HTTP 409 if not all devices have signal files. Returns HTTP 500 with detail if network setup fails.

---

### Runtime Endpoints (RUNNING state only)

#### `GET /api/devices`
Returns all configured devices with status.

```json
[
  {
    "id": "col1_inv1",
    "name": "COL1 — Inverter ITS-5-2",
    "ip": "10.4.1.10",
    "port": 502,
    "unit_id": 1,
    "vlan": 100,
    "signal_count": 321,
    "description": "Sungrow SG8800-MV Collector 1 Inverter 1",
    "status": "running"
  }
]
```

---

#### `GET /api/devices/{device_id}/signals`
Returns the full signal list for a device as a JSON array. Each object matches the CSV schema. Returns 404 if unknown.

---

#### `GET /api/devices/{device_id}/values`
Returns current raw register values keyed by signal name.

```json
{
  "Grid Voltage L1": 4000,
  "Active Power": 2000000,
  "DC Bus Voltage": 600.0,
  "Running Status": true
}
```

Returns 404 if unknown.

---

#### `POST /api/devices/{device_id}/set`
Set a single signal value by name.

```json
{ "name": "Active Power", "value": 1500000 }
```

- Bool signals: `value` must be `true`, `false`, `1`, or `0`.
- float32 signals: `value` must be a number.
- Integer types: `value` must be an integer.

```json
{ "ok": true, "name": "Active Power", "raw_value": 1500000 }
```

Returns 404 for unknown device or signal. Returns 400 for type mismatch.

---

#### `POST /api/devices/{device_id}/simulate`
Load `default_value` for every signal in the device.

```json
{ "ok": true, "device_id": "col1_inv1", "signals_set": 321 }
```

---

#### `POST /api/devices/{device_id}/clear`
Zero all registers for the device.

```json
{ "ok": true, "device_id": "col1_inv1" }
```

---

#### `POST /api/simulate`
Load simulation defaults for all devices.

```json
{ "ok": true, "devices_updated": 4 }
```

---

#### `POST /api/clear`
Zero all registers for all devices.

```json
{ "ok": true, "devices_updated": 4 }
```

---

#### `POST /api/devices/{device_id}/signals`
**Hot reload** — replace a device's signal definitions. Body is the full new signal list as JSON (the Web UI sends this from its tabular editor).

```json
{
  "signals": [
    { "name": "Grid Voltage L1", "register_type": "holding", "address": 1000,
      "data_type": "uint16", "bit_index": null, "word_order": "big_endian",
      "scale": 0.1, "unit": "V", "section": "Grid",
      "description": "Phase L1-N voltage", "default_value": 4000, "writable": false },
    ...
  ]
}
```

Engine validates the new list (full schema check). On success:
- Writes the updated CSV to disk.
- Builds a new RegisterMap.
- Atomically swaps it into the Modbus server context for the device.
- Other devices and other (ip, port) servers are not affected.
- Existing Modbus client connections remain open.

```json
{ "ok": true, "device_id": "col1_inv1", "signal_count": 325, "reloaded": true }
```

On validation failure: HTTP 400 with row-level errors. No changes made.

#### `POST /api/devices/{device_id}/signals/upload`
Same as above but accepts a CSV file upload (multipart). For users who edited the CSV externally and want to upload the file directly.

#### `GET /api/devices/{device_id}/signals/download`
Returns the current signal CSV file as a download. Useful for backing up or editing externally.

---

#### `POST /api/stop`
Gracefully stop the simulation. Engine transitions to STOPPING then exits the process. The systemd service restarts it automatically in SETUP state.

> **Note:** Network interfaces are not torn down. The next process startup with no `--reset` flag will refuse to start because `.config_locked` still exists. The user must revert the VM snapshot to proceed.

```json
{ "ok": true, "message": "Engine shutting down" }
```

---

#### `GET /api/config`
Returns the project summary.

```json
{
  "project_name": "Du Plessis PV — Facility SCADA",
  "vlan_mode": true,
  "traffic_interface": "eth1",
  "device_count": 4,
  "total_signals": 831,
  "web_ui_port": 5000
}
```

---

#### `GET /api/network`
Returns the current network state (interfaces and IPs created by NetworkManager).

```json
{
  "traffic_interface": "eth1",
  "vlan_interfaces": ["eth1.100", "eth1.200", "eth1.300"],
  "assigned_ips": [
    { "ip": "10.4.1.10", "interface": "eth1.100" },
    { "ip": "10.4.1.11", "interface": "eth1.100" },
    { "ip": "10.4.2.50", "interface": "eth1.200" }
  ]
}
```

---

## 12. Web UI — Frontend

### Technology

- HTML/JavaScript single-page application served by the Engine
- Bootstrap 5 from CDN — no local build step
- Vanilla JavaScript only — no frameworks, no npm
- All API calls via `fetch()`
- Stateless — all state lives in the Engine

### Two Views Driven by Engine State

On page load, the UI calls `GET /api/state` and renders one of two views:

| Engine state | UI view |
|---|---|
| `SETUP` | Setup wizard (step-by-step file upload) |
| `RUNNING` | Runtime view (device list, signal table, value editing) |

The UI polls `/api/state` every 2 seconds so it can transition automatically when the engine state changes.

---

### Setup Wizard View

A 3-step wizard. The user cannot skip steps. Each step waits for the engine to confirm success before unlocking the next.

```
┌──────────────────────────────────────────────────────────────────┐
│  Modbus Simulator — Setup                                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│   [ Step 1 ✓ Config ] ─ [ Step 2 ● Signals ] ─ [ Step 3 ○ Start ]│
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  STEP 2 — Upload Signal Files                                    │
│                                                                  │
│  The following devices need signal CSV files:                    │
│                                                                  │
│  ┌────────────────────────────────────────────────┐              │
│  │ ✓ col1_inv1   COL1 - Inverter ITS-5-2          │ 321 signals  │
│  │ ✓ col1_inv2   COL1 - Inverter ITS-5-3          │ 321 signals  │
│  │ ○ col1_scb1   COL1 - SCB Panel 1   [Upload...] │              │
│  │ ○ energy_meter Grid Energy Meter   [Upload...] │              │
│  └────────────────────────────────────────────────┘              │
│                                                                  │
│  [ Continue to Start ]   (disabled until all uploads ✓)         │
└──────────────────────────────────────────────────────────────────┘
```

**Step 1 — Upload Config**
- Single file upload field for `sim_config.yaml`.
- On upload: calls `POST /api/setup/config`.
- On success: shows project name, device count, and a non-dismissable warning that the config is now locked.
- Validation errors shown inline.

**Step 2 — Upload Signal Files**
- List of devices from the config.
- Each row shows the device ID, display name, and an Upload button.
- Uploaded devices show a checkmark and the signal count.
- On upload: calls `POST /api/setup/signals/{device_id}`.
- Validation errors shown inline with row numbers.
- "Continue" button enabled only when all devices have signals uploaded.

**Step 3 — Start**
- Summary of what will happen: how many VLAN interfaces will be created, how many IPs will be assigned, how many Modbus servers will start.
- Single "Start Simulation" button.
- On click: calls `POST /api/setup/start`.
- Shows progress while engine initialises (typically 2-5 seconds).
- On success: UI transitions to runtime view automatically.
- On failure: shows the error and remains on Step 3 — user cannot go back to fix config (it is locked), so the only recovery is a VM snapshot revert.

---

### Runtime View Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  NAVBAR                                                              │
│  [Project Name]  [VLAN: enabled / eth1]  [▶ Simulate All] [✕ Clear] │
├───────────────────┬──────────────────────────────────────────────────┤
│  DEVICE LIST      │  SIGNAL TABLE                                    │
│  (left panel)     │  (right panel)                                   │
│                   │                                                  │
│  ● col1_inv1      │  COL1 — Inverter ITS-5-2                         │
│    10.4.1.10      │  10.4.1.10:502  Unit 1  VLAN 100  eth1.100      │
│    VLAN 100       │  [▶ Simulate]  [✕ Clear]  [🔍 Filter...]        │
│                   │                                                  │
│  ● col1_inv2      │  ── Grid ───────────────────────────────────     │
│    10.4.1.11      │  Name           Raw     Scaled  Unit  Type       │
│    VLAN 100       │  Grid Voltage   [4000]  400.0   V     uint16    │
│                   │  Active Power   [2000000] 2.0 MW W    int32     │
│  ● col1_scb1      │  Running Status [● ON ]               bool      │
│    10.4.2.50      │                                                  │
│    VLAN 200       │  ── Thermal ────────────────────────────────     │
│                   │  Cabinet Temp   [35]    35      °C    int16     │
│  ● energy_meter   │                                                  │
│    10.4.3.10      │                                                  │
│    VLAN 300       │                                                  │
└───────────────────┴──────────────────────────────────────────────────┘
```

### Device List Panel

- Lists all devices from `/api/devices`
- Each entry shows: status dot, device name, IP address, VLAN ID
- Clicking a device loads its signals into the signal table
- First device selected by default on load

### Signal Table Panel

- Header: device name, IP:port, unit ID, VLAN, interface name
- Per-device Simulate and Clear buttons
- Filter input that narrows signal rows by name or description (client-side)
- Signals grouped by `section` in collapsible headers — all expanded by default

### Signal Row Behaviour

- **Numeric signals:** `<input type="number">` showing the raw register value. On blur or Enter, calls `POST /api/devices/{id}/set`. The adjacent column shows the scaled engineering value (recomputed client-side as `raw × scale`) and the unit.
- **Bool signals:** Toggle switch (`<input type="checkbox">`). Toggling immediately calls `POST /api/devices/{id}/set`.
- Invalid values (non-numeric in a number field, out-of-range) show the field in red and do not submit.
- A field the user is actively editing (has focus) is never overwritten by the auto-refresh cycle.

### Auto-Refresh

- Polls `/api/devices/{selected_device_id}/values` every 2 seconds.
- Updates all display fields except the one currently focused.
- If an API call fails, shows a non-blocking navbar warning ("⚠ Connection lost") and retries.

---

### Signal Editor (Hot Reload)

Each device in the runtime view has an "Edit Signals" button that opens a modal tabular editor.

```
┌──────────────────────────────────────────────────────────────────┐
│  Edit Signals — col1_inv1                              [Close X] │
├──────────────────────────────────────────────────────────────────┤
│  [+ Add Row]  [↓ Download CSV]  [↑ Upload CSV]                   │
│                                                                  │
│  Name           Reg Type  Addr  Type    Bit  WO   Scale  Default │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Grid Volt L1  holding  1000  uint16  -    be   0.1    4000  │ │
│  │ Grid Volt L2  holding  1001  uint16  -    be   0.1    4000  │ │
│  │ Active Power  holding  1004  int32   -    be   1      2e6   │ │
│  │ ...                                                          │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Validation: ✓ 321 signals, no errors                            │
│                                                                  │
│  [Cancel]                              [Save & Hot Reload]       │
└──────────────────────────────────────────────────────────────────┘
```

**Editor behaviour:**

- All fields editable inline.
- Dropdowns for `register_type`, `data_type`, `word_order`.
- Client-side validation as the user types (e.g. address must be a number, bit_index required for bool).
- Validation summary at bottom of modal: count of valid signals and any errors.
- "Save" disabled while errors exist.
- "Save & Hot Reload": calls `POST /api/devices/{id}/signals` with the full new signal list.
- On success: modal closes, success toast shown, signal table refreshes.
- On engine validation error: errors shown inline next to the offending rows, modal stays open.
- "Download CSV" exports the current signal list as a CSV file.
- "Upload CSV" replaces the entire signal list with an uploaded CSV (after validation).

**Why this matters:**

The user does not need to leave the browser, edit files in a Linux terminal, or restart anything. The full signal definition lifecycle — view, edit, validate, save, reload — happens in the UI. The engine handles hot reload transparently.

---

## 13. Operating Modes

The Engine supports three operating modes selected by command-line flags. In all modes the engine starts in SETUP state and transitions to RUNNING when triggered.

### Mode 1 — Full Mode (Default)

The Engine starts both the REST API and serves the Web UI static files. Users interact via a browser.

```bash
sudo python3 -m modbus_sim.main
```

This is the standard operating mode and what the systemd service runs.

### Mode 2 — Headless Mode

The Engine starts the REST API but does not serve the Web UI. Useful for automated test environments, CLI-driven workflows, or when the simulation is being driven by another tool.

```bash
sudo python3 -m modbus_sim.main --headless
```

Behaviour:
- `GET /` returns 404 (no UI served).
- All `/api/*` endpoints are fully functional.
- Setup wizard logic still applies — clients (typically scripts) must call the setup endpoints in order: config → signals → start.
- An external orchestrator (test harness, deployment script) can drive the entire lifecycle via the REST API.

Example automated setup using curl:
```bash
# Upload config
curl -X POST -F file=@sim_config.yaml http://vm:5000/api/setup/config

# Upload signal files
curl -X POST -F file=@devices/inv1.csv http://vm:5000/api/setup/signals/col1_inv1
curl -X POST -F file=@devices/inv2.csv http://vm:5000/api/setup/signals/col1_inv2

# Start
curl -X POST http://vm:5000/api/setup/start
```

### Common Flags

| Flag | Effect |
|---|---|
| (none) | Full mode — Engine + Web UI |
| `--headless` | Engine only, no Web UI served |
| `--config <path>` | Override project directory location (default: `./project/`) |

### State Transitions Summary

```
                     ┌──────────────────────────────────┐
                     │  Process starts                  │
                     │  (systemd, manual, or test)      │
                     └──────────────┬───────────────────┘
                                    ▼
                     ┌──────────────────────────────────┐
                     │  Check .config_locked            │
                     │  ├── exists + no --reset → ABORT │
                     │  └── absent or --reset → SETUP   │
                     └──────────────┬───────────────────┘
                                    ▼
                       ┌────────────────────────┐
                       │      SETUP state       │
                       │  - REST API listening  │
                       │  - UI shows wizard     │
                       │  - No simulation       │
                       └────────────┬───────────┘
                                    │ POST /api/setup/start
                                    │ (after config + all signals uploaded)
                                    ▼
                       ┌────────────────────────┐
                       │     RUNNING state      │
                       │  - Network configured  │
                       │  - Modbus servers up   │
                       │  - UI shows runtime    │
                       │  - Hot reload enabled  │
                       └────────────┬───────────┘
                                    │ POST /api/stop or CTRL+C
                                    ▼
                       ┌────────────────────────┐
                       │    Process exits       │
                       │  systemd restarts it   │
                       │  Network state remains │
                       └────────────────────────┘
```

---

## 14. Import Utilities

Import utilities are standalone CLI scripts in `importers/`. They convert external formats to the standard signal CSV. They are not part of the running simulator.

### 14.1 Zenon TXT Importer — `importers/zenon_txt.py`

Converts a Zenon SCADA tab-delimited export file (`.txt`) to a standard signal CSV.

**Usage:**
```bash
python3 importers/zenon_txt.py \
  --input "path/to/Zenon Import File.txt" \
  --output "devices/col1_inv1.csv"
```

**Column mapping:**

| Zenon column | CSV column | Notes |
|---|---|---|
| `VariableName` | `name` | |
| `TypeName` | `data_type` | UINT→uint16, INT→int16, UDINT→uint32, DINT→int32, BOOL→bool |
| `Offset` | `address` | |
| `BitAddr` | `bit_index` | BOOL signals only |
| `Unit` | `unit` | `"0"` → empty |
| `ReadWrite` | `writable` | |

**Defaults applied:**
- `register_type`: `holding` (Zenon Open Modbus uses FC3 exclusively)
- `word_order`: `little_endian` (Sungrow convention — user must review for other manufacturers)
- `scale`: `1` (user must add scale factors from device protocol PDF)
- `section`: inferred from address range using a built-in Sungrow section range table
- `default_value`: `0` (user must fill in)

The output file is a valid signal CSV that passes `csv_validator.py`.

---

### 14.2 CSV Validator — `importers/csv_validator.py`

Validates a signal CSV against the schema defined in section 7.

**Usage:**
```bash
python3 importers/csv_validator.py --input "devices/col1_inv1.csv"
```

**Output on success:** `✓ 321 signals — no errors`  
**Output on failure:** List of errors with row numbers and column names.

---

## 15. Installation and Running

### Prerequisites

- VMware Workstation (any recent version) running on Windows
- Linux VM: Ubuntu Server 22.04 LTS or 24.04 LTS
- Python 3.10 or newer
- USB-C to Ethernet adapter connected and passed through to the VM
- The Engine process must run as root (handled by systemd)

### install.sh

One-time setup script run inside the VM during initial provisioning (before taking the clean snapshot).

```bash
#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3 python3-pip iproute2 git
sudo pip3 install -r requirements.txt

# Install the systemd service
sudo cp systemd/modbus-sim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable modbus-sim

# Create the project directory
sudo mkdir -p /opt/modbus-sim/project/devices

echo "Installation complete."
echo "Find your USB-C NIC interface name with: ip link"
echo "Note: This name (e.g. eth1) must match traffic_interface in any sim_config.yaml you upload."
echo ""
echo "Next steps:"
echo "  1. Reboot the VM"
echo "  2. Verify the Engine started: systemctl status modbus-sim"
echo "  3. Take the 'Simulator Clean State' VMware snapshot"
```

### systemd Service

`systemd/modbus-sim.service`:

```ini
[Unit]
Description=Generic Modbus TCP Simulator Engine
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/modbus-sim
ExecStart=/usr/bin/python3 -m modbus_sim.main --config /opt/modbus-sim/project
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

The service is enabled by `install.sh` and starts automatically on VM boot. It does not need to be touched during normal operation.

### Normal User Workflow

After the VM is provisioned and the clean snapshot is taken, the user never opens a terminal on the VM. The workflow is entirely browser-based:

```
1. Revert VM to "Simulator Clean State" snapshot (VMware on Windows host)
2. Power on the VM
3. Wait ~30 seconds for boot — Engine starts automatically via systemd
4. Open browser on Windows: http://<vm-management-ip>:5000
5. Setup wizard guides through config upload, signal upload, start
6. Use simulator
7. When done: close browser, power off VM (or leave running)
```

To switch projects, the user just reverts the VM and repeats. They never SSH, never use `sudo`, never edit files in Linux.

### Engineer / Developer Workflow

For developers maintaining the simulator code itself, manual control is available via SSH:

```bash
# Check engine status
sudo systemctl status modbus-sim

# View logs
sudo journalctl -u modbus-sim -f

# Manual run with custom flags (stop the service first)
sudo systemctl stop modbus-sim
sudo python3 -m modbus_sim.main --headless
```

### Accessing the Web UI

From the Windows host browser:
```
http://<VM management IP>:5000
```

The management IP is the IP address of the VM's first network adapter (bridged or host-only). Set as a static IP during VM provisioning.

### Stopping the Engine

The user does not normally stop the engine — they just close the browser when done with the session. If a full stop is needed:

- **Via Web UI:** click Stop in the runtime view (calls `POST /api/stop`).
- **Via systemd:** `sudo systemctl stop modbus-sim`.
- **Power off the VM:** clean shutdown also stops the engine.

In all cases the network interfaces remain assigned. They only clear when the VM is reverted or rebooted.

---

## 16. Non-Functional Requirements

### Performance

- Startup must complete within 10 seconds for up to 20 devices with 500 signals each.
- Modbus TCP response time must be under 100ms for all read requests under normal load.
- Web UI auto-refresh must complete within 500ms for a device with up to 1000 signals.
- NetworkManager setup and teardown must complete within 5 seconds for up to 50 VLAN interfaces.

### Reliability

- The Modbus server must remain operational if the Flask web UI encounters an exception.
- Flask exceptions must be caught and returned as JSON error responses — never crash the process.
- The simulator must handle concurrent Modbus polling from multiple clients simultaneously without data corruption.
- If `NetworkManager.teardown()` is called while servers are still running, it must not deadlock.

### Network Cleanliness

- The simulator must never write to `/etc/network/interfaces`, `/etc/netplan/`, or any other persistent network configuration file. All `ip` commands operate on in-memory kernel state only.
- Network state between sessions is managed entirely by the VMware snapshot mechanism, not by application-level teardown logic.
- Reverting the VM to the "Simulator Clean State" snapshot must always result in zero VLAN subinterfaces and zero simulator-assigned IPs on the USB-C NIC.

### Capacity

- Support up to 80 simultaneous device simulations.
- Support up to 2000 signals per device.
- Support up to 30,000 total signals across all devices.
- Support up to 50 VLAN subinterfaces on a single physical NIC.

### Portability

- The application must run on any Linux distribution with Python 3.10+ and `iproute2`.
- No dependency on any external network service, database, or cloud API.
- The same `sim_config.yaml` and device CSV files must work without modification on any compliant host.

---

## 17. Implementation Constraints

### pymodbus Version

Use `pymodbus >= 3.6.0`. Do not use the pymodbus 2.x API. The async server is `StartAsyncTcpServer` from `pymodbus.server`.

### Address Offset Correction

pymodbus 3.x adds 1 to FC3/FC4/FC1/FC2 addresses internally. Subclass `ModbusSlaveContext` and override `getValues` and `setValues` to remove the +1 offset. Apply to all four register type stores. Without this, all register addresses will be off by one compared to the signal CSV.

### float32 default_value Conversion

The `default_value` for `float32` signals in the CSV is the float engineering value (e.g. `600.0`). The signal loader must convert this to a raw 32-bit unsigned integer using `struct.unpack('>I', struct.pack('>f', float_val))[0]` when building the signal object. The `RegisterMap.set_defaults()` method then writes this raw value as two 16-bit words.

### subprocess Calls in NetworkManager

All `ip` commands must be called via `subprocess.run()` with `check=True`. A failure during setup aborts startup with a clear error message including the stdout and stderr output from the failed command.

### Root Requirement

The simulator must check at startup whether it is running as root (`os.geteuid() == 0`). If not, and if any device uses port < 1024 or VLAN mode is enabled, it must print a clear error and exit before attempting any network operations.

### VLAN Interface Naming

VLAN subinterface names follow the Linux convention: `<parent_interface>.<vlan_id>`. For example, if `traffic_interface` is `enp0s20f0u1` and VLAN is 100, the subinterface name is `enp0s20f0u1.100`. This name is used in all `ip` commands and displayed in the web UI and banner.

### Multiple Devices on Same VLAN

Multiple devices with the same `vlan` value share one VLAN subinterface. `NetworkManager.setup()` must create each VLAN subinterface only once even if multiple devices reference the same VLAN.

### Multiple Devices on Same IP:Port

If two devices share `(ip, port)`, they must be served by one `ModbusServerContext` with two slave contexts (one per unit ID). The application must not attempt to start two servers on the same `(ip, port)`.

---

## 18. Acceptance Criteria

### State Machine

- [ ] On fresh process start with no `.config_locked` present, Engine enters SETUP state.
- [ ] On process start with `.config_locked` present (no `--reset`), Engine refuses to start with a clear error.
- [ ] `--reset` flag bypasses the lock check and clears the project directory.
- [ ] `GET /api/state` returns `"SETUP"` before setup is complete.
- [ ] `GET /api/state` returns `"RUNNING"` after `POST /api/setup/start` succeeds.
- [ ] Runtime endpoints called in SETUP state return HTTP 409.
- [ ] Setup endpoints called in RUNNING state return HTTP 409.

### Setup Wizard

- [ ] `POST /api/setup/config` with valid YAML returns parsed device list and creates `.config_locked`.
- [ ] `POST /api/setup/config` with invalid YAML returns 400 with row-level errors and does not create lock.
- [ ] Second `POST /api/setup/config` while in SETUP state with lock present returns 409.
- [ ] `POST /api/setup/signals/{device_id}` validates the CSV and rejects invalid files with detailed errors.
- [ ] `POST /api/setup/start` succeeds only when all devices have signal files uploaded.
- [ ] `POST /api/setup/start` returns 409 if any device is missing its signal file.
- [ ] Browser UI shows setup wizard while in SETUP state.
- [ ] Browser UI shows runtime view after successful start.
- [ ] UI polls `/api/state` and transitions automatically when engine state changes.

### Hot Reload

- [ ] `POST /api/devices/{id}/signals` with a valid new signal list rebuilds the RegisterMap.
- [ ] After hot reload, a Modbus client poll returns values consistent with the new signal definitions.
- [ ] Hot reload does not affect any other device.
- [ ] Existing Modbus TCP connections from clients remain open through hot reload.
- [ ] Hot reload with invalid data returns 400 and leaves the on-disk file unchanged.
- [ ] Browser UI signal editor saves changes via the hot-reload endpoint and refreshes the signal table.

### Headless Mode

- [ ] `--headless` flag prevents the UI from being served (`GET /` returns 404).
- [ ] All API endpoints function normally in headless mode.
- [ ] A complete simulation lifecycle can be driven end-to-end via curl in headless mode.

### Configuration and Startup

- [ ] Uploaded `sim_config.yaml` with four devices on two VLANs loads without error.
- [ ] Missing `traffic_interface` (interface does not exist) produces a clear startup error during `POST /api/setup/start`.
- [ ] Duplicate device `id` produces a config upload error.
- [ ] Engine running without root when VLAN mode is required fails the start transition with a clear error before touching the network.

### Network Manager — VLAN Mode

- [ ] Running `ip link` inside the VM after startup shows VLAN subinterfaces (e.g. `eth1.100`, `eth1.200`).
- [ ] Running `ip addr` shows each device IP assigned to its correct VLAN subinterface.
- [ ] After CTRL+C, running `ip link` still shows the VLAN subinterfaces (intentional — no cleanup).
- [ ] After CTRL+C, running `ip addr` still shows the assigned IPs (intentional — no cleanup).
- [ ] Two devices on the same VLAN share one subinterface (not two).
- [ ] Reverting the VM to the "Simulator Clean State" snapshot removes all VLAN subinterfaces and assigned IPs.

### Network Manager — No-VLAN Mode

- [ ] With `vlan_mode: disabled`, no VLAN subinterfaces are created.
- [ ] IPs are assigned directly to `traffic_interface`.

### Snapshot Workflow

- [ ] After taking the "Simulator Clean State" snapshot, `ip link` shows no VLAN subinterfaces on the USB-C NIC.
- [ ] After taking the "Simulator Clean State" snapshot, `ip addr` shows no IP addresses on the USB-C NIC.
- [ ] Reverting to the snapshot after a session restores the clean network state.
- [ ] Restarting the simulator with the same config on the same VM session (without snapshot revert) works correctly — IPs are already assigned and servers rebind successfully.

### Modbus Server

- [ ] A Modbus client can read holding registers from `10.4.1.10:502` Unit ID 1 and receive correct values.
- [ ] A Modbus client can read from `10.4.2.50:502` Unit ID 200 simultaneously without interference.
- [ ] `uint32` big-endian: high word at lower address, low word at higher address.
- [ ] `uint32` little-endian: low word at lower address, high word at higher address.
- [ ] `float32` returns the correct IEEE 754 bit pattern.
- [ ] Two `bool` signals sharing a register with different `bit_index` values return independently correct values.
- [ ] Writing FC16 to a holding register updates the value returned by subsequent FC3 reads.
- [ ] Reading an undefined address returns Modbus exception code 02.
- [ ] A managed switch with VLAN 100 configured on its access ports allows a device on that port to reach `10.4.1.10:502`.

### Web UI

- [ ] All configured devices appear in the device list with correct IP, port, and VLAN.
- [ ] Selecting a device loads its signals grouped by section.
- [ ] Changing a numeric value in the UI is reflected in the next Modbus client poll.
- [ ] Toggling a bool updates only the targeted bit; other bits in the same register are unchanged.
- [ ] Simulate loads all `default_value` entries correctly.
- [ ] Clear zeros all registers.
- [ ] Auto-refresh updates values every 2 seconds.
- [ ] Filter narrows the signal list client-side without a page reload.
- [ ] `/api/network` returns the correct list of created interfaces and IPs.

### Signal Loading

- [ ] CSV with all six data types loads without error.
- [ ] `csv_validator.py` rejects a file with missing `bit_index` for a bool signal.
- [ ] `csv_validator.py` rejects a file with missing `word_order` for a uint32 signal.
- [ ] `csv_validator.py` rejects duplicate signal names.

### Importers

- [ ] `zenon_txt.py` converts a Zenon export file to a valid signal CSV.
- [ ] The produced CSV passes `csv_validator.py` without errors.

---

*End of requirements document.*
