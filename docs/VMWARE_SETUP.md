# VMware Setup Guide

This guide covers the one-time host configuration needed before distributing the VM snapshot.

## Network Layout

| What | How | Result |
|---|---|---|
| Management (GUI access) | VMware Host-Only NIC | `192.168.99.2` on `ens37` |
| Internet (for Ubuntu updates) | VMware NAT NIC | DHCP on `ens33` |
| Modbus traffic | USB-to-Ethernet adapter | Assigned by engine at runtime |

The Windows host can reach the simulator UI at **http://192.168.99.2/** once the VM is running.

---

## 1. Configure the Host-Only Network (do this once on the Windows host)

1. Open **VMware Workstation** → **Edit** → **Virtual Network Editor**.
2. Click **Change Settings** (requires admin on Windows).
3. Select or add a Host-Only network (e.g. `VMnet1`).
4. Set the subnet to **192.168.99.0** with mask **255.255.255.0**.
5. Ensure "Use local DHCP service to distribute IP addresses" is **unchecked** (we use a static IP).
6. Click **Apply** → **OK**.

---

## 2. Assign the Host-Only NIC to the VM

1. In VMware Workstation, select the VM → **VM** → **Settings** → **Network Adapter**.
2. Set Network Connection to **Custom: Specific virtual network** → choose the VMnet you configured (e.g. VMnet1 — 192.168.99.x).
3. Click **OK**.

> The VM already has a second NIC (`ens37`) with static IP `192.168.99.2` configured. If you add the Host-Only NIC as a second adapter in the VM, Ubuntu will detect it as `ens37` automatically.

---

## 3. USB-to-Ethernet Adapter Auto-Connection (USB Device Filters)

By default, when you plug a USB device into the Windows host, you must manually decide whether to send it to the VM. USB Device Filters make this automatic for a specific device.

### Find the USB Vendor/Product ID (on Windows)

1. Plug in the USB-to-Ethernet adapter.
2. Open **Device Manager** (Win + X → Device Manager).
3. Expand **Network Adapters** → right-click the adapter → **Properties**.
4. Go to the **Details** tab → select **Hardware IDs** from the dropdown.
5. Note the `VID_XXXX&PID_YYYY` values (e.g. `VID_0B95&PID_1790` for an ASIX adapter).

### Add a USB Filter in VMware

1. VM Settings → **USB Controller** tab.
2. Click **Add Filter** → find the adapter in the list and click **OK**.
   - If the adapter isn't listed (not currently plugged in), click **Add Filter From List** and enter the VID/PID manually.
3. Set the filter action to **Connect to this virtual machine**.
4. Click **OK**.

From now on, plugging in that USB adapter will automatically pass it through to the VM.

### After Plugging In

1. The adapter appears in Ubuntu as `enxXXXXXX` (based on its MAC address).
2. Open the simulator UI at **http://192.168.99.2/**.
3. Click the **Change** button next to the interface badge in the top bar.
4. Select the new adapter from the dropdown.
5. Click **Apply** — the Modbus servers restart on the new NIC within a few seconds.

---

## 4. One-Time VM Setup (run once before taking the snapshot)

SSH into the VM or open a terminal and run:

```bash
cd /home/png/ModbusSim
sudo ./install.sh
```

This installs the systemd service (`modbus-sim-gui`) so the simulator starts automatically on boot.

To verify the service is running:
```bash
systemctl status modbus-sim-gui
journalctl -u modbus-sim-gui -f
```

---

## 5. Taking the Base Snapshot

After running `install.sh` and verifying the UI loads at `http://192.168.99.2/`:

1. Stop the engine: click **Stop** in the UI (or `systemctl stop modbus-sim-gui`).
2. In VMware: **VM** → **Snapshot** → **Take Snapshot**.
3. Name it **"Simulator Clean State"** (this is the snapshot users revert to for a fresh config).

> **Important:** Take the snapshot with the config **not loaded** (SETUP state) or **with a locked
> project** depending on your distribution intent. If you include a locked project, users boot
> directly into the RUNNING state with that configuration.

---

## 6. Distributing the VM

Export the VM as an OVF or share the VMware folder. Recipients need:

- VMware Workstation (Player is sufficient for running; Workstation needed to change settings).
- The Host-Only network configured with the **192.168.99.x** subnet (Step 1 above).
- The USB Device Filter set up for their specific adapter (Step 3 above — VID/PID may differ).

Once configured, the workflow is:
1. Start the VM.
2. Wait ~15 seconds.
3. Open **http://192.168.99.2/** in a Windows browser.
4. The simulator is ready.
