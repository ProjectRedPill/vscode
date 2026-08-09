"""Wi-Fi access point discovery.

Almost every hidden camera worth worrying about is a Wi-Fi camera: it needs
bandwidth to stream and power to run, and the cheap ones fall back to hosting
their own SSID when they cannot reach a network. So the Wi-Fi sensor is doing
two jobs at once — inventorying the RF environment, and catching the single most
common covert-camera failure mode.

Uses ordinary OS scan APIs (nmcli / iw / airport / netsh). No monitor mode, no
injection, no deauth. Monitor-mode client capture would find more, but it needs
root and a compatible chipset; that is a documented optional path rather than a
requirement.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from ..intel import oui
from .base import Sensor

# Channel → frequency, for the bands consumer gear uses.
def _freq_from_channel(ch: int) -> float | None:
    if 1 <= ch <= 13:
        return (2407 + 5 * ch) * 1e6
    if ch == 14:
        return 2484e6
    if 32 <= ch <= 177:
        return (5000 + 5 * ch) * 1e6
    return None


class WifiSensor(Sensor):
    name = "wifi"
    band = Band.WIFI
    hint = "Linux: nmcli (NetworkManager) or iw. macOS: built in. Windows: netsh."

    def __init__(self, interval: float = 15.0, **options: Any) -> None:
        super().__init__(**options)
        self.interval = interval
        self._mode: str | None = None
        self._iface: str | None = options.get("interface")

    async def available(self) -> tuple[bool, str]:
        if sys.platform == "darwin":
            airport = (
                "/System/Library/PrivateFrameworks/Apple80211.framework/"
                "Versions/Current/Resources/airport"
            )
            import os

            if os.path.exists(airport):
                self._mode = "airport"
                return True, "airport"
            if self.which("wdutil"):
                self._mode = "wdutil"
                return True, "wdutil (limited detail)"
            return False, "no macOS Wi-Fi scan tool found"

        if sys.platform.startswith("win"):
            if self.which("netsh"):
                self._mode = "netsh"
                return True, "netsh wlan"
            return False, "netsh not found"

        if self.which("nmcli"):
            self._mode = "nmcli"
            return True, "nmcli"
        if self.which("iw"):
            self._mode = "iw"
            return True, "iw scan (needs root on most systems)"
        return False, "install NetworkManager (nmcli) or iw"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._mode is None:
            await self.probe()
        while not stop.is_set():
            try:
                results = await self._scan()
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

    async def _scan(self) -> list[Observation]:
        return {
            "nmcli": self._scan_nmcli,
            "iw": self._scan_iw,
            "airport": self._scan_airport,
            "netsh": self._scan_netsh,
            "wdutil": self._scan_wdutil,
        }.get(self._mode or "", self._none)()  # type: ignore[return-value]

    async def _none(self) -> list[Observation]:
        return []

    # -- Linux -----------------------------------------------------------

    async def _scan_nmcli(self) -> list[Observation]:
        fields = "BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,MODE,RATE,WPA-FLAGS,RSN-FLAGS"
        rc, out, err = await self.run_cmd(
            ["nmcli", "-t", "-f", fields, "device", "wifi", "list", "--rescan", "yes"],
            timeout=30,
        )
        if rc != 0:
            self._fail(err or f"nmcli rc={rc}")
            return []

        results = []
        for line in out.splitlines():
            # nmcli escapes the colons inside a BSSID as '\:'.
            parts = re.split(r"(?<!\\):", line)
            parts = [p.replace("\\:", ":") for p in parts]
            if len(parts) < 6 or not parts[0]:
                continue
            bssid, ssid, chan, freq, signal, security = parts[:6]
            attrs: dict[str, Any] = {
                "ssid": ssid or "<hidden>",
                "hidden_ssid": not ssid,
                "security": security or "open",
                "signal_pct": _int(signal),
            }
            if len(parts) > 6 and parts[6]:
                attrs["wifi_mode"] = parts[6]
            if len(parts) > 7 and parts[7]:
                attrs["max_rate"] = parts[7]
            results.append(
                self._make(bssid, chan, freq, signal_pct=_int(signal), attrs=attrs)
            )
        return results

    async def _scan_iw(self) -> list[Observation]:
        iface = self._iface or await self._first_wifi_iface()
        if not iface:
            self._fail("no wireless interface found")
            return []
        rc, out, err = await self.run_cmd(["iw", "dev", iface, "scan"], timeout=40)
        if rc != 0:
            self._fail(err or f"iw rc={rc} (usually means root is required)")
            return []

        results: list[Observation] = []
        bssid: str | None = None
        attrs: dict[str, Any] = {}
        rssi: float | None = None
        freq: float | None = None

        def flush() -> None:
            nonlocal bssid, attrs, rssi, freq
            if bssid:
                obs = Observation(
                    band=Band.WIFI, sensor=self.name, address=bssid,
                    rssi=rssi, frequency_hz=freq,
                    name=attrs.get("ssid"), attrs={**attrs, **oui.describe(bssid)},
                )
                results.append(obs)
            bssid, attrs, rssi, freq = None, {}, None, None

        for line in out.splitlines():
            s = line.strip()
            if s.startswith("BSS "):
                flush()
                bssid = s.split()[1].split("(")[0].upper()
            elif s.startswith("SSID:"):
                ssid = s.split(":", 1)[1].strip()
                attrs["ssid"] = ssid or "<hidden>"
                attrs["hidden_ssid"] = not ssid
            elif s.startswith("signal:"):
                rssi = _float(s.split(":", 1)[1].replace("dBm", ""))
            elif s.startswith("freq:"):
                f = _float(s.split(":", 1)[1])
                freq = f * 1e6 if f else None
            elif s.startswith("DS Parameter set: channel"):
                attrs["channel"] = _int(s.split("channel")[-1])
            elif "capability:" in s:
                attrs["capabilities"] = s.split(":", 1)[1].strip()
            elif s.startswith("RSN:") or s.startswith("WPA:"):
                attrs.setdefault("security", []).append(s.split(":")[0])
            elif s.startswith("WPS:"):
                attrs["wps"] = True
        flush()
        return results

    async def _first_wifi_iface(self) -> str | None:
        rc, out, _ = await self.run_cmd(["iw", "dev"], timeout=8)
        if rc != 0:
            return None
        m = re.search(r"Interface\s+(\S+)", out)
        return m.group(1) if m else None

    # -- macOS -----------------------------------------------------------

    async def _scan_airport(self) -> list[Observation]:
        airport = (
            "/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport"
        )
        rc, out, err = await self.run_cmd([airport, "-s"], timeout=30)
        if rc != 0:
            self._fail(err or f"airport rc={rc}")
            return []
        results = []
        for line in out.splitlines()[1:]:
            m = re.match(
                r"\s*(.+?)\s+([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s+(-?\d+)\s+(\S+)\s+"
                r"(\S+)\s+(\S+)\s+(.*)",
                line,
                re.I,
            )
            if not m:
                continue
            ssid, bssid, rssi, channel, _ht, cc, security = m.groups()
            attrs = {
                "ssid": ssid.strip() or "<hidden>",
                "hidden_ssid": not ssid.strip(),
                "security": security.strip(),
                "country": cc,
            }
            ch = _int(channel.split(",")[0])
            results.append(
                self._make(bssid.upper(), str(ch or ""), None, rssi=_float(rssi), attrs=attrs)
            )
        return results

    async def _scan_wdutil(self) -> list[Observation]:
        rc, out, _ = await self.run_cmd(["wdutil", "info"], timeout=20)
        if rc != 0:
            return []
        # wdutil only reports the joined network; better than nothing on
        # recent macOS where airport was removed.
        m = re.search(r"BSSID\s*:\s*([0-9a-f:]{17})", out, re.I)
        s = re.search(r"SSID\s*:\s*(.+)", out)
        r = re.search(r"RSSI\s*:\s*(-?\d+)", out)
        if not m:
            return []
        return [
            self._make(
                m.group(1).upper(), "", None, rssi=_float(r.group(1)) if r else None,
                attrs={"ssid": s.group(1).strip() if s else "?", "note": "joined network only"},
            )
        ]

    # -- Windows ---------------------------------------------------------

    async def _scan_netsh(self) -> list[Observation]:
        rc, out, err = await self.run_cmd(
            ["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=25
        )
        if rc != 0:
            self._fail(err or f"netsh rc={rc}")
            return []
        results: list[Observation] = []
        ssid = ""
        auth = ""
        for raw in out.splitlines():
            s = raw.strip()
            if s.lower().startswith("ssid ") and ":" in s:
                ssid = s.split(":", 1)[1].strip()
            elif s.lower().startswith("authentication"):
                auth = s.split(":", 1)[1].strip()
            elif s.lower().startswith("bssid"):
                bssid = s.split(":", 1)[1].strip().upper()
                results.append(
                    self._make(bssid, "", None, attrs={"ssid": ssid or "<hidden>",
                                                       "hidden_ssid": not ssid,
                                                       "security": auth})
                )
            elif s.lower().startswith("signal") and results:
                results[-1].attrs["signal_pct"] = _int(s.split(":", 1)[1])
                pct = results[-1].attrs["signal_pct"]
                if pct is not None:
                    results[-1].rssi = pct / 2.0 - 100.0
            elif s.lower().startswith("channel") and results:
                ch = _int(s.split(":", 1)[1])
                results[-1].channel = ch
                results[-1].frequency_hz = _freq_from_channel(ch) if ch else None
        return results

    # -- shared ----------------------------------------------------------

    def _make(
        self,
        bssid: str,
        chan: str,
        freq: str | None,
        *,
        rssi: float | None = None,
        signal_pct: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> Observation:
        attrs = dict(attrs or {})
        bssid = bssid.upper()
        ch = _int(chan)
        f = _float(freq) if freq else None
        frequency = (f * 1e6 if f and f < 10000 else f) or (
            _freq_from_channel(ch) if ch else None
        )
        if rssi is None and signal_pct is not None:
            # nmcli reports a 0-100 quality; the conventional inverse is
            # dBm = pct/2 - 100. Approximate, and labelled as such.
            rssi = signal_pct / 2.0 - 100.0
            attrs["rssi_estimated_from_quality"] = True
        attrs.update(oui.describe(bssid))
        if frequency:
            attrs["band_ghz"] = round(frequency / 1e9, 1)
        return Observation(
            band=Band.WIFI,
            sensor=self.name,
            address=bssid,
            rssi=rssi,
            channel=ch,
            frequency_hz=frequency,
            name=attrs.get("ssid"),
            attrs=attrs,
            address_is_random=bool(attrs.get("rotating")),
        )


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError, TypeError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError, TypeError):
        return None
