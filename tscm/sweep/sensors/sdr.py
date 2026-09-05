"""Sub-GHz ISM and wideband spectrum sensing via SDR.

Two sensors, two very different questions:

  Rtl433Sensor    "what devices are talking on 315/433/868/915 MHz?"
                  Demodulates and decodes ~250 protocols via rtl_433. This is
                  how you find door sensors, PIR motion detectors, TPMS, remote
                  key fobs, weather stations, and the cheap analogue-ish bugs
                  that use FSK on 433.

  SpectrumSensor  "is anything transmitting where nothing should be?"
                  Power-only sweep via rtl_power/hackrf_sweep. Cannot say what a
                  signal is, but sees *everything*, including analogue video
                  transmitters (1.2/2.4/5.8 GHz) and continuous-carrier bugs
                  that no decoder knows about. This is the sensor that catches
                  the device nobody has written a parser for.

Both are receive-only.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from .base import Sensor

# Frequencies worth naming when a signal turns up there.
BAND_PLAN: tuple[tuple[float, float, str], ...] = (
    (300e6, 322e6, "315 MHz ISM (garage/keyfob/sensors, US)"),
    (330e6, 350e6, "TETRA / land mobile"),
    (420e6, 450e6, "433 MHz ISM (sensors, keyfobs, cheap bugs)"),
    (460e6, 470e6, "UHF business radio / covert body-wire band"),
    (860e6, 880e6, "868 MHz ISM (EU sensors, LoRa)"),
    (900e6, 930e6, "915 MHz ISM (US sensors, LoRa)"),
    (1150e6, 1300e6, "1.2 GHz analogue video (common covert camera band)"),
    (2400e6, 2484e6, "2.4 GHz (Wi-Fi/BLE/Zigbee and analogue video)"),
    (5150e6, 5350e6, "5 GHz Wi-Fi (lower)"),
    (5470e6, 5730e6, "5 GHz Wi-Fi (upper)"),
    (5730e6, 5900e6, "5.8 GHz analogue video (FPV and covert cameras)"),
)


def name_frequency(hz: float) -> str:
    for lo, hi, label in BAND_PLAN:
        if lo <= hz <= hi:
            return label
    return f"{hz / 1e6:.3f} MHz"


class Rtl433Sensor(Sensor):
    """Decoded ISM-band device traffic via `rtl_433 -F json`."""

    name = "rtl433"
    band = Band.ISM_SUB_GHZ
    hint = "install rtl_433 and plug in an RTL-SDR dongle"

    def __init__(
        self,
        frequencies: list[str] | None = None,
        hop_interval: int = 30,
        device: str | None = None,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        # Hopping costs duty cycle but covers both regions' ISM allocations,
        # which matters when you do not know what region the bug was bought in.
        self.frequencies = frequencies or ["433.92M", "868.3M", "915M", "315M"]
        self.hop_interval = hop_interval
        self.device = device

    async def available(self) -> tuple[bool, str]:
        if not self.which("rtl_433"):
            return False, "rtl_433 not on PATH"
        rc, out, err = await self.run_cmd(["rtl_433", "-h"], timeout=8)
        # rtl_433 -h exits non-zero on some builds; presence of usage text is
        # the reliable signal.
        if "rtl_433" not in (out + err).lower():
            return False, "rtl_433 present but did not respond to -h"
        return True, f"rtl_433, hopping {', '.join(self.frequencies)}"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        argv = ["rtl_433", "-F", "json", "-M", "level", "-M", "time:unix"]
        for f in self.frequencies:
            argv += ["-f", f]
        if len(self.frequencies) > 1:
            argv += ["-H", str(self.hop_interval)]
        if self.device:
            argv += ["-d", self.device]

        async for line in self.stream_cmd(argv, stop):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            obs = self._from_record(record)
            if obs:
                self._count()
                yield obs

    def _from_record(self, rec: dict[str, Any]) -> Observation | None:
        model = rec.get("model")
        if not model:
            return None

        ident = str(rec.get("id") or rec.get("serial") or rec.get("channel") or "?")
        address = f"{model}:{ident}"
        freq_mhz = rec.get("freq") or rec.get("freq1")
        freq_hz = float(freq_mhz) * 1e6 if freq_mhz else None

        attrs: dict[str, Any] = {
            "rtl433_model": model,
            "rtl433_id": ident,
            "name": f"{model} #{ident}",
            "protocol": rec.get("protocol"),
            "modulation": rec.get("mod"),
        }
        if freq_hz:
            attrs["band_label"] = name_frequency(freq_hz)
        # Copy every decoded physical measurement through; rtl_433 emits a wide
        # and protocol-specific set and all of it is useful detail.
        for key, value in rec.items():
            if key in ("model", "id", "time", "mic", "freq", "rssi", "snr", "noise"):
                continue
            if isinstance(value, (str, int, float, bool)):
                attrs[f"ism_{key}"] = value

        low = model.lower()
        if any(k in low for k in ("tpms", "tire")):
            attrs["device_class"] = "sensor"
            attrs["note"] = "tyre pressure sensor — moves with a vehicle, useful for spotting a following car"
        elif any(k in low for k in ("door", "contact", "pir", "motion", "security")):
            attrs["device_class"] = "sensor"
            attrs["note"] = "security sensor — part of an alarm or monitoring system"
        elif any(k in low for k in ("remote", "fob", "keeloq")):
            attrs["device_class"] = "peripheral"
        else:
            attrs["device_class"] = "sensor"

        return Observation(
            band=Band.ISM_SUB_GHZ,
            sensor=self.name,
            address=address,
            rssi=_num(rec.get("rssi")),
            snr_db=_num(rec.get("snr")),
            frequency_hz=freq_hz,
            name=attrs["name"],
            attrs={k: v for k, v in attrs.items() if v is not None},
            raw=rec,
        )


class SpectrumSensor(Sensor):
    """Wideband power sweep — finds transmitters no decoder recognises.

    Emits one Observation per bin that exceeds the rolling noise floor by
    `threshold_db`. The address is the rounded centre frequency, so repeated
    hits on the same emitter fuse into one device with a real RSSI history —
    which means the ranging UI works on an unknown analogue bug exactly as it
    does on a BLE tag.
    """

    name = "spectrum"
    band = Band.RF_BROADBAND
    hint = "install rtl_power (rtl-sdr) or hackrf_sweep (hackrf)"

    def __init__(
        self,
        start_hz: float = 300e6,
        stop_hz: float = 1700e6,
        bin_hz: float = 250e3,
        threshold_db: float = 12.0,
        interval: float = 20.0,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.start_hz = start_hz
        self.stop_hz = stop_hz
        self.bin_hz = bin_hz
        self.threshold_db = threshold_db
        self.interval = interval
        self._tool: str | None = None
        self._floor: dict[int, float] = {}

    async def available(self) -> tuple[bool, str]:
        if self.which("rtl_power"):
            self._tool = "rtl_power"
            # An RTL dongle tops out around 1.7 GHz, so 2.4/5.8 GHz video
            # transmitters are out of reach without a HackRF.
            return True, "rtl_power (coverage limited to ~24 MHz - 1.7 GHz)"
        if self.which("hackrf_sweep"):
            self._tool = "hackrf_sweep"
            return True, "hackrf_sweep (1 MHz - 6 GHz)"
        return False, "neither rtl_power nor hackrf_sweep found"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._tool is None:
            await self.probe()
        while not stop.is_set():
            try:
                rows = await self._sweep()
            except Exception as exc:
                self._fail(exc)
                rows = []
            for obs in self._peaks(rows):
                self._count()
                yield obs
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def _sweep(self) -> list[tuple[float, float]]:
        """Return [(centre_hz, dB)] for one full pass."""
        if self._tool == "rtl_power":
            argv = [
                "rtl_power",
                "-f", f"{int(self.start_hz)}:{int(self.stop_hz)}:{int(self.bin_hz)}",
                "-i", "1", "-1", "-",
            ]
        elif self._tool == "hackrf_sweep":
            argv = [
                "hackrf_sweep",
                "-f", f"{int(self.start_hz / 1e6)}:{int(self.stop_hz / 1e6)}",
                "-w", str(int(self.bin_hz)), "-1",
            ]
        else:
            return []

        rc, out, err = await self.run_cmd(argv, timeout=max(60.0, self.interval * 2))
        if rc not in (0, None) and not out:
            self._fail(err or f"{self._tool} rc={rc}")
            return []

        rows: list[tuple[float, float]] = []
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            try:
                # Both tools share the layout: date, time, low, high, step, n, samples...
                low = float(parts[2])
                step = float(parts[4])
                for i, sample in enumerate(parts[6:]):
                    rows.append((low + step * i, float(sample)))
            except ValueError:
                continue
        return rows

    def _peaks(self, rows: list[tuple[float, float]]) -> list[Observation]:
        if not rows:
            return []

        # Rolling per-bin noise floor. A bug that is *always* on would be
        # learned as floor if we used a plain average, so the floor only tracks
        # downward quickly and upward slowly.
        out: list[Observation] = []
        for hz, db in rows:
            key = int(hz // self.bin_hz)
            prev = self._floor.get(key)
            if prev is None:
                self._floor[key] = db
                continue
            self._floor[key] = min(prev + 0.05, db) if db > prev else db * 0.3 + prev * 0.7

            excess = db - self._floor[key]
            if excess < self.threshold_db:
                continue

            centre = round(hz / 1e6, 3)
            attrs: dict[str, Any] = {
                "name": f"RF carrier @ {centre:.3f} MHz",
                "band_label": name_frequency(hz),
                "power_dbfs": round(db, 1),
                "noise_floor_dbfs": round(self._floor[key], 1),
                "excess_db": round(excess, 1),
                "device_class": "covert" if excess > 25 else "unknown",
                "note": (
                    "unidentified carrier — no protocol decoder matched. Verify by "
                    "moving: a real emitter's level tracks your position."
                ),
            }
            out.append(
                Observation(
                    band=Band.RF_BROADBAND,
                    sensor=self.name,
                    address=f"rf:{centre:.3f}MHz",
                    rssi=round(db, 1),
                    frequency_hz=hz,
                    snr_db=round(excess, 1),
                    name=attrs["name"],
                    attrs=attrs,
                    ts=time.time(),
                )
            )
        return out


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
