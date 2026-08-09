"""Infrared sensing.

Two genuinely different detections share this band:

  Coded IR      A remote-control protocol (NEC, RC5, Sony SIRC...). Tells you a
                remote or an IR blaster is in use. Mostly benign, but an IR
                blaster is also how a covert device gets commanded without
                touching the network.

  IR flood      A steady, un-modulated 850/940 nm level with no protocol. This
                is a night-vision illuminator, and it is one of the most
                reliable hidden-camera indicators there is: the human eye cannot
                see it, it is on whenever the room is dark, and nothing else in
                a normal room emits it continuously.

Backends: LIRC (`mode2`/`irw`), Linux rc-core, or an external probe over serial.
Only the external probe can report the flood level, because a LIRC receiver
demodulates at 38 kHz and is blind to un-modulated light by design — that is a
hardware fact worth stating rather than papering over.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.models import Band, Observation
from . import serialbridge
from .base import Sensor

IR_PROTOCOLS = {
    "nec": "NEC (most consumer remotes)",
    "necext": "NEC extended",
    "rc5": "Philips RC-5",
    "rc6": "Philips RC-6",
    "sony": "Sony SIRC",
    "samsung": "Samsung",
    "jvc": "JVC",
    "sharp": "Sharp",
    "panasonic": "Panasonic / Kaseikyo",
    "rca": "RCA",
    "kaseikyo": "Kaseikyo",
}


class IrSensor(Sensor):
    name = "ir"
    band = Band.IR
    hint = (
        "attach an IR probe (see firmware/ir_probe) and pass --ir-port, "
        "or install lirc for coded-IR only"
    )

    def __init__(
        self,
        port: str | None = None,
        baud: int = 115200,
        fifo: str | None = None,
        flood_threshold: int = 600,
        flood_hold_s: float = 4.0,
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.port = port
        self.baud = baud
        self.fifo = fifo
        # ADC counts (0-1023 on a 10-bit probe) above which we call it a flood.
        self.flood_threshold = flood_threshold
        # A flood has to persist; a TV remote burst must not trip it.
        self.flood_hold_s = flood_hold_s
        self._mode: str | None = None
        self._flood_since: float | None = None

    async def available(self) -> tuple[bool, str]:
        if self.port:
            try:
                import serial  # noqa: F401
            except ImportError:
                return False, "pyserial not installed (pip install pyserial)"
            self._mode = "serial"
            return True, f"IR probe on {self.port} (coded IR + flood level)"
        if self.fifo:
            self._mode = "fifo"
            return True, f"IR records from {self.fifo}"
        if self.which("mode2"):
            self._mode = "lirc"
            return True, "lirc mode2 — coded IR only, cannot see IR illuminators"
        return False, "no IR probe configured and lirc not installed"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        if self._mode is None:
            await self.probe()
        if self._mode in ("serial", "fifo"):
            async for obs in self._run_probe(stop):
                yield obs
        elif self._mode == "lirc":
            async for obs in self._run_lirc(stop):
                yield obs

    # -- external probe --------------------------------------------------

    async def _run_probe(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
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

        if kind == "ir":
            proto = str(rec.get("proto") or "unknown").lower()
            addr = rec.get("addr")
            cmd = rec.get("cmd")
            address = f"ir:{proto}:{addr}"
            attrs: dict[str, Any] = {
                "name": f"IR remote ({IR_PROTOCOLS.get(proto, proto)})",
                "ir_protocol": IR_PROTOCOLS.get(proto, proto),
                "ir_address": addr,
                "ir_command": cmd,
                "ir_repeat": bool(rec.get("repeat")),
                "device_class": "ir_emitter",
                "note": (
                    "coded infrared command. Benign for a TV remote; an IR blaster "
                    "in a room you did not equip can be a control channel."
                ),
            }
            if rec.get("bits"):
                attrs["ir_bits"] = rec["bits"]
            if rec.get("raw"):
                attrs["ir_raw"] = str(rec["raw"])[:512]
            return Observation(
                band=Band.IR, sensor=self.name, address=address,
                name=attrs["name"], attrs=attrs, raw=rec,
                # Amplitude is a proxy for proximity: an IR receiver saturates
                # close up, so the probe reports peak level when it can.
                rssi=_as_float(rec.get("level")),
            )

        if kind in ("irlevel", "ir_level", "flood"):
            level = rec.get("adc")
            if level is None:
                level = rec.get("level")
            level_f = _as_float(level)
            if level_f is None:
                return None
            now = time.time()
            if level_f >= self.flood_threshold:
                self._flood_since = self._flood_since or now
                held = now - self._flood_since
            else:
                self._flood_since = None
                held = 0.0

            sustained = held >= self.flood_hold_s
            attrs = {
                "name": "Infrared flood" if sustained else "Infrared ambient level",
                "ir_level_adc": level_f,
                "ir_level_mv": _as_float(rec.get("mv")),
                "ir_sustained_s": round(held, 1),
                "device_class": "camera" if sustained else "unknown",
                "ir_flood": sustained,
                "note": (
                    "steady un-modulated IR — consistent with a night-vision "
                    "illuminator on a camera. Confirm by covering the suspected "
                    "lens: the level should drop."
                    if sustained
                    else "ambient infrared, below flood threshold"
                ),
            }
            # Map ADC counts onto a dBm-like scale so the ranging UI can use it:
            # the Ranger only cares about a monotonic level in dB-ish units.
            pseudo_dbm = -100.0 + (level_f / 1023.0) * 70.0
            return Observation(
                band=Band.IR, sensor=self.name, address="ir:flood",
                rssi=round(pseudo_dbm, 1), name=attrs["name"],
                attrs={k: v for k, v in attrs.items() if v is not None}, raw=rec,
            )

        return None

    # -- LIRC ------------------------------------------------------------

    _MODE2 = re.compile(r"^(pulse|space)\s+(\d+)$", re.I)

    async def _run_lirc(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        """Group mode2 pulse/space output into bursts.

        We do not decode the protocol here — that is what the probe firmware is
        for. What we can say from raw timing is that *something* transmitted, at
        what carrier-ish rate, and roughly how long the burst was.
        """
        burst: list[tuple[str, int]] = []
        last = time.time()

        async for line in self.stream_cmd(["mode2", "-d", "/dev/lirc0"], stop):
            m = self._MODE2.match(line.strip())
            now = time.time()
            if m:
                burst.append((m.group(1).lower(), int(m.group(2))))
                last = now
                continue
            if burst and now - last > 0.2:
                obs = self._burst_observation(burst)
                burst = []
                if obs:
                    self._count()
                    yield obs

    def _burst_observation(self, burst: list[tuple[str, int]]) -> Observation | None:
        if len(burst) < 4:
            return None
        pulses = [d for kind, d in burst if kind == "pulse"]
        total_us = sum(d for _, d in burst)
        header = pulses[0] if pulses else 0

        guess = "unknown"
        if 8500 <= header <= 9500:
            guess = "NEC (most consumer remotes)"
        elif 2200 <= header <= 2500:
            guess = "Sony SIRC"
        elif 3000 <= header <= 3600:
            guess = "Panasonic / Kaseikyo"
        elif 800 <= header <= 1000:
            guess = "Philips RC-5/RC-6"

        attrs = {
            "name": f"IR burst ({guess})",
            "ir_protocol": guess,
            "ir_edges": len(burst),
            "ir_burst_us": total_us,
            "ir_header_us": header,
            "device_class": "ir_emitter",
            "backend": "lirc mode2",
            "note": "lirc cannot see un-modulated IR illuminators — use an IR probe for those",
        }
        return Observation(
            band=Band.IR, sensor=self.name, address=f"ir:lirc:{guess}",
            name=attrs["name"], attrs=attrs,
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
