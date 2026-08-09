"""BLE scanner.

Primary backend is `bleak`, which gives one API across Linux/BlueZ, macOS
CoreBluetooth and Windows WinRT. Where bleak is unavailable we fall back to
BlueZ `bluetoothctl`, which is present on essentially every Linux host with a
Bluetooth adapter and needs no Python dependency at all.

Scanning is passive: we listen to advertisements. We never connect, never pair,
never write. A connect would be detectable by the target and is not needed for
anything this tool reports.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from ..intel import ble as ble_intel
from ..intel import oui
from .base import Sensor


class BleSensor(Sensor):
    name = "ble"
    band = Band.BLE
    hint = "pip install bleak  (or install bluez for the bluetoothctl fallback)"

    def __init__(self, active_scan: bool = True, **options: Any) -> None:
        super().__init__(**options)
        # Active scanning asks for scan responses, which roughly doubles the
        # attribute yield (names, extra service UUIDs). It emits SCAN_REQ
        # packets, so a sufficiently paranoid target could notice; passive mode
        # is available for a genuinely silent sweep.
        self.active_scan = active_scan
        self._backend: str | None = None

    async def available(self) -> tuple[bool, str]:
        try:
            import bleak  # noqa: F401
        except ImportError:
            if self.which("bluetoothctl"):
                self._backend = "bluetoothctl"
                return True, "using bluetoothctl fallback (bleak not installed)"
            return False, "neither bleak nor bluetoothctl is available"

        try:
            from bleak import BleakScanner

            scanner = BleakScanner()
            del scanner
        except Exception as exc:
            if self.which("bluetoothctl"):
                self._backend = "bluetoothctl"
                return True, f"bleak init failed ({exc}); using bluetoothctl"
            return False, f"bleak present but no usable adapter: {exc}"

        self._backend = "bleak"
        return True, "bleak"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._backend is None:
            await self.probe()
        if self._backend == "bleak":
            async for obs in self._run_bleak(stop):
                yield obs
        elif self._backend == "bluetoothctl":
            async for obs in self._run_bluetoothctl(stop):
                yield obs

    # -- bleak ----------------------------------------------------------

    async def _run_bleak(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        from bleak import BleakScanner

        queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize=4096)

        def on_detect(device: Any, adv: Any) -> None:
            try:
                queue.put_nowait(self._from_bleak(device, adv))
            except asyncio.QueueFull:
                self._fail("observation queue full — scanning faster than fusion")
            except Exception as exc:
                self._fail(exc)

        kwargs: dict[str, Any] = {"detection_callback": on_detect}
        # BlueZ is the only backend that exposes the passive/active switch.
        try:
            scanner = BleakScanner(
                scanning_mode="active" if self.active_scan else "passive", **kwargs
            )
        except (TypeError, ValueError):
            scanner = BleakScanner(**kwargs)

        await scanner.start()
        try:
            while not stop.is_set():
                try:
                    obs = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                self._count()
                yield obs
        finally:
            try:
                await scanner.stop()
            except Exception as exc:
                self._fail(exc)

    def _from_bleak(self, device: Any, adv: Any) -> Observation:
        mfr = dict(getattr(adv, "manufacturer_data", {}) or {})
        svc_data = dict(getattr(adv, "service_data", {}) or {})
        svc_uuids = list(getattr(adv, "service_uuids", []) or [])

        extras = self._platform_extras(device)
        attrs = ble_intel.parse_advertisement(
            local_name=getattr(adv, "local_name", None) or getattr(device, "name", None),
            manufacturer_data=mfr,
            service_data=svc_data,
            service_uuids=svc_uuids,
            tx_power=getattr(adv, "tx_power", None),
            platform_data=extras,
        )
        addr = str(device.address)
        # BlueZ reports public-vs-random directly; without it the address type
        # can only be guessed, so pass it through when we have it.
        attrs.update(oui.describe(addr, extras.get("address_type")))

        return Observation(
            band=Band.BLE,
            sensor=self.name,
            address=addr,
            rssi=float(getattr(adv, "rssi", None) or getattr(device, "rssi", -127)),
            tx_power=getattr(adv, "tx_power", None),
            name=attrs.get("name"),
            attrs=attrs,
            address_is_random=bool(attrs.get("rotating")),
            raw={
                "manufacturer_data": {f"{k:04x}": v.hex() for k, v in mfr.items()},
                "service_data": {k: v.hex() for k, v in svc_data.items()},
                "service_uuids": svc_uuids,
            },
        )

    @staticmethod
    def _platform_extras(device: Any) -> dict[str, Any]:
        """BlueZ exposes several fields bleak does not surface directly."""
        details = getattr(device, "details", None)
        out: dict[str, Any] = {}
        props = None
        if isinstance(details, dict):
            props = details.get("props") or details
        elif hasattr(details, "get"):
            props = details
        if isinstance(props, dict):
            for src, dst in (
                ("AddressType", "address_type"),
                ("Connected", "connected"),
                ("Paired", "paired"),
                ("Trusted", "trusted"),
                ("Blocked", "blocked"),
                ("Alias", "alias"),
                ("Class", "cod"),
                ("Modalias", "modalias"),
                ("Icon", "icon"),
            ):
                if src in props:
                    out[dst] = props[src]
        return out

    # -- bluetoothctl fallback ------------------------------------------

    _BCTL_DEV = re.compile(r"\[?(NEW|CHG|DEL)\]?\s+Device\s+([0-9A-F:]{17})\s*(.*)", re.I)
    _BCTL_KV = re.compile(r"([0-9A-F:]{17})\s+(\w+):\s*(.+)", re.I)

    async def _run_bluetoothctl(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        """Parse `bluetoothctl` event output.

        Far less detail than bleak (no raw manufacturer data), but it needs no
        Python dependency and works on a stock Debian/Ubuntu/Raspbian box.
        """
        argv = ["bluetoothctl", "--timeout", "0", "scan", "on"]
        pending: dict[str, dict[str, Any]] = {}

        async for line in self.stream_cmd(argv, stop):
            line = line.strip()
            m = self._BCTL_DEV.search(line)
            if m:
                _, mac, rest = m.groups()
                mac = mac.upper()
                entry = pending.setdefault(mac, {})
                if rest and not rest.startswith("RSSI"):
                    entry["name"] = rest.strip()
                self._count()
                yield self._from_bctl(mac, entry)
                continue

            m = self._BCTL_KV.search(line)
            if m:
                mac, key, value = m.groups()
                mac = mac.upper()
                entry = pending.setdefault(mac, {})
                key = key.lower()
                if key == "rssi":
                    try:
                        entry["rssi"] = float(value.split()[0].replace("x", ""), 0) \
                            if value.strip().startswith("0x") else float(value.split()[0])
                    except ValueError:
                        pass
                elif key in ("name", "alias"):
                    entry["name"] = value.strip()
                elif key == "class":
                    try:
                        entry["cod"] = int(value.strip(), 0)
                    except ValueError:
                        pass
                self._count()
                yield self._from_bctl(mac, entry)

    def _from_bctl(self, mac: str, entry: dict[str, Any]) -> Observation:
        from ..intel.sig import decode_cod

        attrs: dict[str, Any] = {"backend": "bluetoothctl"}
        if entry.get("name"):
            attrs["name"] = entry["name"]
        attrs.update(decode_cod(entry.get("cod")))
        attrs.update(oui.describe(mac))
        return Observation(
            band=Band.BLE,
            sensor=self.name,
            address=mac,
            rssi=entry.get("rssi"),
            name=entry.get("name"),
            attrs=attrs,
            address_is_random=bool(attrs.get("rotating")),
            ts=time.time(),
        )
