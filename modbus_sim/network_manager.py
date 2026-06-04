"""Linux network interface setup (REQUIREMENTS.md sections 8, 16, 17).

Creates 802.1Q VLAN subinterfaces and assigns device IPs to the USB-C traffic NIC
using ``ip`` commands. This is the ONLY module that makes system calls. There is no
teardown — the VM snapshot is the cleanup mechanism (§8). All ``ip`` commands operate
on in-memory kernel state; nothing is written to persistent network config (§16).

Linux-only by design: it shells out to ``iproute2``. On non-Linux hosts the import
still works, but ``setup()`` will fail when the commands are unavailable — these code
paths are intended to run on the Ubuntu VM as root.
"""

from __future__ import annotations

import subprocess

from .config_loader import SimConfig


class NetworkManager:
    def __init__(self, config: SimConfig):
        self._config = config
        self._vlan_interfaces: list[str] = []
        self._assigned_ips: list[dict[str, str]] = []

    @property
    def is_vlan_mode(self) -> bool:
        return self._config.is_vlan_mode

    @property
    def vlan_interfaces(self) -> list[str]:
        return list(self._vlan_interfaces)

    @property
    def assigned_ips(self) -> list[dict[str, str]]:
        return list(self._assigned_ips)

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        """Create VLAN interfaces and assign IPs. Raises RuntimeError on failure."""
        iface = self._config.traffic_interface
        self._verify_interface_exists(iface)

        if self.is_vlan_mode:
            for vlan_id in self._unique_vlans():
                subif = self._vlan_subif_name(iface, vlan_id)
                self._create_vlan(iface, subif, vlan_id)
                self._vlan_interfaces.append(subif)

        for dev in self._config.devices:
            target = self._target_interface(dev)
            cidr = f"{dev.ip}/{dev.prefix_length}"
            self._assign_ip(cidr, target)
            self._assigned_ips.append({"ip": dev.ip, "interface": target, "prefix_length": dev.prefix_length})

    # ------------------------------------------------------------- internals
    def _unique_vlans(self) -> list[int]:
        return sorted({d.vlan for d in self._config.devices if d.vlan})

    def _vlan_subif_name(self, parent: str, vlan_id: int) -> str:
        """Build a VLAN subinterface name that fits within Linux's 15-char limit.

        Standard form is ``<parent>.<vlan_id>``.  When the parent name is long
        (e.g. ``enxc8a3622c0b97``) the suffix is appended to a truncated copy of
        the parent so the result is always ≤ 15 characters.
        """
        suffix = f".{vlan_id}"
        max_parent = 15 - len(suffix)
        return f"{parent[:max_parent]}{suffix}"

    def _target_interface(self, dev) -> str:
        iface = self._config.traffic_interface
        if self.is_vlan_mode and dev.vlan:
            return self._vlan_subif_name(iface, dev.vlan)
        return iface

    def _verify_interface_exists(self, iface: str) -> None:
        try:
            result = subprocess.run(
                ["ip", "link", "show", iface],
                capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "the 'ip' command (iproute2) was not found; the simulator must run "
                "on Linux with iproute2 installed."
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"traffic_interface '{iface}' does not exist. Find the USB-C NIC "
                f"name with 'ip link'.\n{result.stderr.strip()}"
            )

    def _create_vlan(self, parent: str, subif: str, vlan_id: int) -> None:
        # Tolerate an already-existing subinterface so restarting without a snapshot
        # revert works (§18). Only create when it is not already present.
        if subprocess.run(["ip", "link", "show", subif],
                          capture_output=True, text=True).returncode != 0:
            self._run(["ip", "link", "add", "link", parent,
                       "name", subif, "type", "vlan", "id", str(vlan_id)])
        self._run(["ip", "link", "set", subif, "up"])

    def _assign_ip(self, cidr: str, interface: str) -> None:
        result = subprocess.run(
            ["ip", "addr", "add", cidr, "dev", interface],
            capture_output=True, text=True,
        )
        # The address may already be assigned from a previous run in the same VM
        # session — that is fine, treat it as a no-op. iproute2 < 5.x reports
        # "File exists"; newer versions say "Address already assigned.".
        already_assigned = (
            "File exists" in result.stderr
            or "already assigned" in result.stderr
        )
        if result.returncode != 0 and not already_assigned:
            raise RuntimeError(self._error("assign IP", result))

    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(self._error(" ".join(cmd), result))

    def teardown(self) -> None:
        """Remove IPs and VLAN subinterfaces created by setup(). Best-effort:
        errors are suppressed so a partial failure never aborts a reset."""
        for entry in reversed(self._assigned_ips):
            try:
                subprocess.run(
                    ["ip", "addr", "del", f"{entry['ip']}/{entry['prefix_length']}", "dev", entry["interface"]],
                    capture_output=True, text=True,
                )
            except Exception:
                pass
        self._assigned_ips.clear()

        for subif in reversed(self._vlan_interfaces):
            try:
                subprocess.run(["ip", "link", "set", subif, "down"], capture_output=True, text=True)
            except Exception:
                pass
            try:
                subprocess.run(["ip", "link", "delete", subif], capture_output=True, text=True)
            except Exception:
                pass
        self._vlan_interfaces.clear()

    @staticmethod
    def _error(action: str, result: subprocess.CompletedProcess) -> str:
        msg = (
            f"network setup failed ({action}); "
            f"exit={result.returncode}.\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
        if "Operation not permitted" in result.stderr or result.returncode == 1:
            msg += "\nThis usually means the simulator is not running as root."
        return msg
