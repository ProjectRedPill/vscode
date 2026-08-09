"""Broadband RF power probe (RFHunter-class hardware).

A logarithmic detector such as the AD8317 outputs a voltage proportional to
total RF power in dB across roughly 1 MHz - 10 GHz. It cannot tell you *what* is
transmitting — no demodulation happens at all — but that is exactly why it is
worth having: it responds to encrypted links, analogue video transmitters,
burst-mode GSM bugs and protocols nobody has written a decoder for, all of which
are invisible to every other sensor here.

Used the way it is meant to be used, it is a proximity instrument: you sweep the
probe over surfaces and watch the level, and the ranging view turns that into
"warmer/colder" instead of a number you have to interpret.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from . import serialbridge
from .base import Sensor
from .sdr import name_frequency


class RfPowerSensor(Sensor):
    name = "rf-power"
    band = Band.RF_BROADBAND
    hint = "attach an AD8317/AD8318 probe (see firmware/rf_probe) and pass --rf-port"

    def __init__(
        self,
        port: str | None = None,
        baud: int = 115200,
        fifo: str | None = None,
        alert_dbm: float = -35.0,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.port = port
        self.baud = baud
        self.fifo = fifo
        # Above this, something is transmitting within arm's reach. A phone at
        # 30 cm reads around -30 dBm on an AD8317 with a short whip.
        self.alert_dbm = alert_dbm
        self._mode: str | None = None
        self._baseline: float | None = None

    async def available(self) -> tuple[bool, str]:
        if self.port:
            try:
                import serial  # noqa: F401
            except ImportError:
                return False, "pyserial not installed (pip install pyserial)"
            self._mode = "serial"
            return True, f"RF power probe on {self.port}"
        if self.fifo:
            self._mode = "fifo"
            return True, f"RF power records from {self.fifo}"
        return False, "no RF power probe configured (--rf-port / --rf-fifo)"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._mode is None:
            await self.probe()
        source = (
            serialbridge.read_serial(self.port or "", self.baud, stop)
            if self._mode == "serial"
            else serialbridge.read_file(self.fifo or "", stop)
        )
        try:
            async for record in source:
                obs = self._from_record(record)
                if obs:
                    self._count()
                    yield obs
        except Exception as exc:
            self._fail(exc)

    def _from_record(self, rec: dict[str, Any]) -> Observation | None:
        kind = str(rec.get("t") or rec.get("type") or "").lower()
        if kind not in ("rf", "rfpower", "power"):
            return None

        dbm = _as_float(rec.get("dbm"))
        if dbm is None:
            # Fall back to raw ADC with the AD8317 transfer function:
            # roughly -22 mV/dB with a +/- 0 dBm intercept near 0.5 V.
            adc = _as_float(rec.get("adc"))
            vref = _as_float(rec.get("vref")) or 3.3
            bits = _as_float(rec.get("bits")) or 12.0
            if adc is None:
                return None
            volts = adc * vref / (2**bits - 1)
            dbm = (0.5 - volts) / 0.022 - 40.0

        freq = _as_float(rec.get("freq"))
        # Slowly-tracked floor: this is a hand-held instrument and the ambient
        # level changes as you walk, so the floor has to follow you.
        self._baseline = dbm if self._baseline is None else min(self._baseline * 0.98 + dbm * 0.02, dbm)
        excess = dbm - self._baseline

        attrs: dict[str, Any] = {
            "name": "Broadband RF field",
            "rf_dbm": round(dbm, 1),
            "rf_baseline_dbm": round(self._baseline, 1),
            "rf_excess_db": round(excess, 1),
            "detector": rec.get("detector") or "log-amp",
            "device_class": "covert" if dbm >= self.alert_dbm else "unknown",
            "note": (
                "strong near-field RF — sweep slowly over surfaces; the peak is "
                "where the emitter is. This detector does not demodulate, so it "
                "cannot identify the device, only locate it."
            ),
        }
        if freq:
            attrs["band_label"] = name_frequency(freq)
        if rec.get("antenna"):
            attrs["antenna"] = rec["antenna"]

        return Observation(
            band=Band.RF_BROADBAND,
            sensor=self.name,
            # One logical emitter: the probe has no way to separate sources, so
            # fusing every reading into a single track is the honest model.
            address="rf-probe:field",
            rssi=round(dbm, 1),
            frequency_hz=freq,
            snr_db=round(excess, 1),
            name=attrs["name"],
            attrs=attrs,
            raw=rec,
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
