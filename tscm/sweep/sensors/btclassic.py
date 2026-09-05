"""Bluetooth Classic (BR/EDR) inquiry.

Classic BT matters for a sweep because it is where the audio and imaging devices
live — wireless microphones, body cameras, dashcams and car kits are frequently
BR/EDR-only and completely invisible to a BLE-only scanner.

Discovery is inherently active here: BR/EDR devices only answer an inquiry, they
do not advertise. Non-discoverable devices stay silent no matter what, which is
a real and unavoidable blind spot — the RF-power and SDR sensors exist partly to
cover it.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from ..intel import oui
from ..intel.sig import decode_cod
from .base import Sensor


class BtClassicSensor(Sensor):
    name = "bt-classic"
    band = Band.BT_CLASSIC
    hint = "Linux: install bluez (bluetoothctl / hcitool). macOS: built in."

    def __init__(self, interval: float = 12.0, **options: Any) -> None:
        super().__init__(**options)
        self.interval = interval
        self._mode: str | None = None

    async def available(self) -> tuple[bool, str]:
        if sys.platform == "darwin":
            if self.which("system_profiler"):
                self._mode = "macos"
                return True, "system_profiler SPBluetoothDataType"
            return False, "system_profiler not found"

        if sys.platform.startswith("win"):
            if self.which("powershell") or self.which("pwsh"):
                self._mode = "windows"
                return True, (
                    "PnP device enumeration — paired and previously-seen devices "
                    "only, not a live inquiry"
                )
            return False, "powershell not found"

        if self.which("bluetoothctl"):
            self._mode = "bluetoothctl"
            return True, "bluetoothctl inquiry"
        if self.which("hcitool"):
            self._mode = "hcitool"
            return True, "hcitool scan (deprecated but widely present)"
        return False, "no bluez tooling found"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._mode is None:
            await self.probe()
        while not stop.is_set():
            try:
                if self._mode == "macos":
                    results = await self._scan_macos()
                elif self._mode == "windows":
                    results = await self._scan_windows()
                elif self._mode == "bluetoothctl":
                    results = await self._scan_bluetoothctl()
                elif self._mode == "hcitool":
                    results = await self._scan_hcitool()
                else:
                    return
            except Exception as exc:
                self._fail(exc)
                results = []

            for obs in results:
                self._count()
                yield obs

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    # -- backends --------------------------------------------------------

    _HCI_LINE = re.compile(r"([0-9A-F:]{17})\s+(.*)", re.I)

    async def _scan_hcitool(self) -> list[Observation]:
        rc, out, err = await self.run_cmd(["hcitool", "scan", "--flush"], timeout=20)
        if rc != 0:
            self._fail(err or f"hcitool rc={rc}")
            return []
        obs = []
        for line in out.splitlines()[1:]:
            m = self._HCI_LINE.search(line.strip())
            if m:
                mac, name = m.group(1).upper(), m.group(2).strip()
                obs.append(self._make(mac, name, {}))
        # A second pass with `hcitool info` would give the LMP version and
        # feature mask, but it opens a connection to the target. That is
        # detectable, so it stays behind an explicit opt-in.
        if self.options.get("deep_probe"):
            for o in list(obs):
                o.attrs.update(await self._hci_info(o.address))
        return obs

    async def _hci_info(self, mac: str) -> dict[str, Any]:
        rc, out, _ = await self.run_cmd(["hcitool", "info", mac], timeout=12)
        if rc != 0:
            return {}
        attrs: dict[str, Any] = {"deep_probed": True}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("LMP Version:"):
                attrs["lmp_version"] = line.split(":", 1)[1].strip()
            elif line.startswith("Manufacturer:"):
                attrs["chipset_vendor"] = line.split(":", 1)[1].strip()
            elif line.startswith("Device Name:"):
                attrs["name"] = line.split(":", 1)[1].strip()
            elif line.startswith("Features:"):
                attrs["lmp_features"] = line.split(":", 1)[1].strip()
        return attrs

    _BCTL_DEV = re.compile(r"Device\s+([0-9A-F:]{17})\s+(.*)", re.I)

    async def _scan_bluetoothctl(self) -> list[Observation]:
        # `--timeout` makes bluetoothctl exit on its own, which keeps this a
        # simple request/response instead of a stream we have to babysit.
        rc, out, _ = await self.run_cmd(
            ["bluetoothctl", "--timeout", "10", "scan", "on"], timeout=20
        )
        rc2, devices, _ = await self.run_cmd(["bluetoothctl", "devices"], timeout=10)
        text = out + "\n" + devices
        seen: dict[str, str] = {}
        for line in text.splitlines():
            m = self._BCTL_DEV.search(line)
            if m:
                seen[m.group(1).upper()] = m.group(2).strip()

        results = []
        for mac, name in seen.items():
            attrs = await self._bctl_info(mac)
            results.append(self._make(mac, attrs.pop("name", name), attrs))
        return results

    async def _bctl_info(self, mac: str) -> dict[str, Any]:
        rc, out, _ = await self.run_cmd(["bluetoothctl", "info", mac], timeout=8)
        if rc != 0:
            return {}
        attrs: dict[str, Any] = {}
        uuids: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "name":
                attrs["name"] = value
            elif key == "alias":
                attrs["alias"] = value
            elif key == "class":
                try:
                    cod = int(value, 0)
                    attrs["cod"] = cod
                    attrs.update(decode_cod(cod))
                except ValueError:
                    pass
            elif key == "rssi":
                try:
                    attrs["rssi"] = float(value.split()[0])
                except ValueError:
                    pass
            elif key == "txpower":
                try:
                    attrs["tx_power"] = float(value.split()[0])
                except ValueError:
                    pass
            elif key in ("paired", "trusted", "blocked", "connected"):
                attrs[key] = value.lower() == "yes"
            elif key == "modalias":
                attrs["modalias"] = value
            elif key == "icon":
                attrs["bluez_icon"] = value
            elif key == "uuid":
                uuids.append(value)
        if uuids:
            attrs["profiles"] = uuids
        return attrs

    # Windows encodes the peer address in the PnP instance id, e.g.
    # BTHENUM\DEV_AC1203F1229B\7&1F2A3B4C&0&BLUETOOTHDEVICE_AC1203F1229B
    _WIN_ADDR = re.compile(r"DEV_([0-9A-F]{12})", re.I)

    async def _scan_windows(self) -> list[Observation]:
        """Enumerate Bluetooth devices Windows knows about.

        An honest caveat, surfaced in the sensor's status line rather than
        buried here: this is *not* an inquiry. Windows exposes no unprivileged
        API for an active BR/EDR scan, so what comes back is the set of paired
        and previously-connected devices, plus whatever the stack currently
        sees. It will not discover an unpaired recorder sitting in the room the
        way `hcitool scan` does on Linux — which is a real reason to prefer a
        Linux host or a Raspberry Pi for a serious sweep.
        """
        shell = self.which("pwsh") or self.which("powershell") or "powershell"
        rc, out, err = await self.run_cmd([
            shell, "-NoProfile", "-NonInteractive", "-Command",
            "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Select-Object FriendlyName,InstanceId,Status | ConvertTo-Json -Depth 3",
        ], timeout=30)
        if rc != 0 and not out.strip():
            self._fail(err or f"powershell rc={rc}")
            return []

        import json

        try:
            data = json.loads(out) if out.strip() else []
        except json.JSONDecodeError:
            self._fail("could not parse PowerShell JSON")
            return []
        if isinstance(data, dict):     # a single device is not wrapped in a list
            data = [data]

        results: list[Observation] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            instance = str(entry.get("InstanceId") or "")
            m = self._WIN_ADDR.search(instance)
            if not m:
                # Radios and enumerators carry no peer address; skip them.
                continue
            raw = m.group(1).upper()
            mac = ":".join(raw[i:i + 2] for i in range(0, 12, 2))
            name = str(entry.get("FriendlyName") or "").strip()
            attrs: dict[str, Any] = {
                "backend": "windows-pnp",
                "windows_status": entry.get("Status"),
                "connected": str(entry.get("Status") or "").upper() == "OK",
                "note": (
                    "Windows reports paired and previously-seen devices, not a "
                    "live inquiry — an unpaired device in the room may not appear."
                ),
            }
            results.append(self._make(mac, name, attrs))
        return results

    _MAC_ANY = re.compile(r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})", re.I)

    async def _scan_macos(self) -> list[Observation]:
        rc, out, err = await self.run_cmd(
            ["system_profiler", "-json", "SPBluetoothDataType"], timeout=25
        )
        if rc != 0:
            self._fail(err or f"system_profiler rc={rc}")
            return []
        import json

        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            self._fail(exc)
            return []

        results: list[Observation] = []
        for block in data.get("SPBluetoothDataType", []):
            for group_key in ("device_connected", "device_not_connected"):
                for entry in block.get(group_key, []) or []:
                    for name, info in entry.items():
                        mac = str(info.get("device_address", "")).upper()
                        if not mac:
                            continue
                        attrs: dict[str, Any] = {"connected": group_key.endswith("connected")}
                        for src, dst in (
                            ("device_minorType", "cod_minor"),
                            ("device_majorType", "cod_major"),
                            ("device_vendorID", "vendor_id"),
                            ("device_productID", "product_id"),
                            ("device_firmwareVersion", "firmware"),
                            ("device_batteryLevelMain", "battery_pct"),
                            ("device_rssi", "rssi"),
                        ):
                            if src in info:
                                attrs[dst] = info[src]
                        rssi = attrs.pop("rssi", None)
                        obs = self._make(mac, name, attrs)
                        if rssi is not None:
                            try:
                                obs.rssi = float(str(rssi).split()[0])
                            except ValueError:
                                pass
                        results.append(obs)
        return results

    # -- shared ----------------------------------------------------------

    def _make(self, mac: str, name: str | None, attrs: dict[str, Any]) -> Observation:
        merged: dict[str, Any] = dict(attrs)
        if name and name != mac:
            merged.setdefault("name", name)
        merged.update(oui.describe(mac))
        rssi = merged.pop("rssi", None)
        return Observation(
            band=Band.BT_CLASSIC,
            sensor=self.name,
            address=mac,
            rssi=float(rssi) if rssi is not None else None,
            tx_power=merged.get("tx_power"),
            name=merged.get("name"),
            attrs=merged,
        )
