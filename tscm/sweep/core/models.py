"""Core data model.

Everything the system knows flows through three types:

    Observation   one sensor, one moment, one emitter        (immutable fact)
    Device        many observations fused into one identity  (mutable belief)
    Finding       a threat rule firing on a device           (judgement)

Sensors only ever emit Observations. They never mutate Devices. Fusion owns
Device state, the rule engine owns Findings. Keeping that one-way is what makes
it possible to add a sensor without touching anything else.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Band(str, Enum):
    """Physical layer an observation arrived on."""

    BLE = "ble"
    BT_CLASSIC = "bt_classic"
    WIFI = "wifi"
    ISM_SUB_GHZ = "ism_sub_ghz"   # 315 / 433 / 868 / 915 MHz
    IR = "ir"                      # 850-950 nm infrared
    RF_BROADBAND = "rf_broadband"  # log-amp power probe, no demodulation
    CELLULAR = "cellular"
    NFC = "nfc"
    ULTRASOUND = "ultrasound"


class DeviceClass(str, Enum):
    """What we think the thing *is*. Ordered roughly by how much it matters."""

    UNKNOWN = "unknown"
    PHONE = "phone"
    COMPUTER = "computer"
    WEARABLE = "wearable"
    AUDIO = "audio"
    PERIPHERAL = "peripheral"
    BEACON = "beacon"
    TRACKER = "tracker"           # AirTag, SmartTag, Tile, Chipolo...
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SENSOR = "sensor"             # door/PIR/TPMS/weather
    VEHICLE = "vehicle"
    NETWORK = "network"           # AP, router, repeater
    MEDICAL = "medical"
    APPLIANCE = "appliance"
    IR_EMITTER = "ir_emitter"
    JAMMER = "jammer"
    COVERT = "covert"             # matched a known surveillance signature


class Trust(str, Enum):
    """User-assigned disposition. Drives alerting, never drives detection."""

    UNSET = "unset"
    MINE = "mine"        # my own device; still tracked, never alerted on
    KNOWN = "known"      # recognised, not mine (neighbour's TV, office AP)
    SUSPECT = "suspect"  # user flagged it
    BLOCKED = "blocked"  # hide entirely


@dataclass(slots=True)
class Observation:
    """One sensor reading. Immutable once emitted."""

    band: Band
    sensor: str
    # Stable-ish handle within the band: MAC, BSSID, rtl_433 id, IR protocol/addr.
    address: str
    ts: float = field(default_factory=time.time)

    rssi: float | None = None            # dBm
    tx_power: float | None = None        # dBm at 1m, if the emitter advertises it
    frequency_hz: float | None = None
    channel: int | None = None
    snr_db: float | None = None

    name: str | None = None
    # Everything band-specific lands here; the intel layer turns it into facts.
    raw: dict[str, Any] = field(default_factory=dict)
    # Decoded, human-meaningful key/values (vendor, model, service UUIDs, ...).
    attrs: dict[str, Any] = field(default_factory=dict)

    # True when `address` is known to be a rotating/resolvable private identifier.
    address_is_random: bool = False

    def key(self) -> tuple[str, str]:
        return (self.band.value, self.address.lower())


@dataclass(slots=True)
class Track:
    """Per-band RSSI history for one device. Feeds ranging and the follow rules."""

    band: Band
    address: str
    first_seen: float
    last_seen: float
    count: int = 0
    rssi_raw: float | None = None
    rssi_smoothed: float | None = None
    rssi_min: float | None = None
    rssi_max: float | None = None
    # Bounded ring of (ts, rssi) used by rules that need recent shape.
    history: list[tuple[float, float]] = field(default_factory=list)

    HISTORY_LIMIT = 512

    def push(self, ts: float, rssi: float | None) -> None:
        self.last_seen = ts
        self.count += 1
        if rssi is None:
            return
        self.rssi_raw = rssi
        self.rssi_min = rssi if self.rssi_min is None else min(self.rssi_min, rssi)
        self.rssi_max = rssi if self.rssi_max is None else max(self.rssi_max, rssi)
        self.history.append((ts, rssi))
        if len(self.history) > self.HISTORY_LIMIT:
            del self.history[: len(self.history) - self.HISTORY_LIMIT]


@dataclass(slots=True)
class Finding:
    """A rule fired. Findings are advisory; they never delete or mute a device."""

    rule: str
    severity: int          # 0 info .. 4 critical
    title: str
    detail: str
    ts: float = field(default_factory=time.time)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return ("info", "low", "medium", "high", "critical")[max(0, min(4, self.severity))]


@dataclass(slots=True)
class Device:
    """A fused identity across one or more bands."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str | None = None
    device_class: DeviceClass = DeviceClass.UNKNOWN
    trust: Trust = Trust.UNSET

    vendor: str | None = None
    model: str | None = None
    os_hint: str | None = None

    tracks: dict[tuple[str, str], Track] = field(default_factory=dict)
    # Merged decoded attributes, newest wins, provenance kept in attr_source.
    attrs: dict[str, Any] = field(default_factory=dict)
    attr_source: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    # Identifiers we have positively linked to this device across MAC rotations.
    aliases: set[str] = field(default_factory=set)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # ---- derived views -------------------------------------------------

    @property
    def bands(self) -> list[Band]:
        seen: list[Band] = []
        for band, _ in self.tracks:
            b = Band(band)
            if b not in seen:
                seen.append(b)
        return seen

    @property
    def primary_track(self) -> Track | None:
        """Strongest current signal wins; that is what ranging should follow."""
        live = [t for t in self.tracks.values() if t.rssi_smoothed is not None]
        if not live:
            return next(iter(self.tracks.values()), None)
        return max(live, key=lambda t: t.rssi_smoothed or -999)

    @property
    def rssi(self) -> float | None:
        t = self.primary_track
        return t.rssi_smoothed if t else None

    @property
    def address(self) -> str:
        t = self.primary_track
        return t.address if t else "?"

    @property
    def age(self) -> float:
        return time.time() - self.last_seen

    @property
    def risk(self) -> int:
        """Highest live finding severity. Trust MINE/KNOWN clamps it to info."""
        if self.trust in (Trust.MINE, Trust.KNOWN):
            return 0
        return max((f.severity for f in self.findings), default=0)

    def display_name(self) -> str:
        for candidate in (
            self.label,
            self.attrs.get("name"),
            self.model,
            self.attrs.get("ssid"),
            self.attrs.get("rtl433_model"),
        ):
            if candidate:
                return str(candidate)
        if self.vendor:
            return f"{self.vendor} {self.device_class.value}"
        return self.address

    def set_attr(self, key: str, value: Any, source: str) -> None:
        """Record a decoded fact. Empty values never overwrite known ones."""
        if value is None or value == "" or value == []:
            return
        self.attrs[key] = value
        self.attr_source[key] = source

    def estimated_distance_m(self, env_factor: float = 2.6) -> float | None:
        """Log-distance path loss. Order-of-magnitude only — say so in the UI.

        d = 10 ^ ((TxPower - RSSI) / (10 * n))

        `n` is the environment exponent: 2.0 free space, 2.5-3.0 typical indoor,
        3.5+ through walls or a body. Defaulting to 2.6 keeps indoor error near
        a factor of two rather than a factor of ten.
        """
        t = self.primary_track
        if t is None or t.rssi_smoothed is None:
            return None
        tx = self.attrs.get("tx_power")
        if tx is None:
            tx = -59.0 if Band(t.band) in (Band.BLE, Band.BT_CLASSIC) else -45.0
        try:
            return round(10 ** ((float(tx) - t.rssi_smoothed) / (10.0 * env_factor)), 2)
        except (ValueError, OverflowError):
            return None


def rssi_to_quality(rssi: float | None) -> int:
    """Map dBm onto 0-100 for bars. Clamped at -100/-35 where BLE saturates."""
    if rssi is None:
        return 0
    return int(max(0.0, min(100.0, (rssi + 100.0) / 65.0 * 100.0)))


def dbm_delta_to_distance_ratio(delta_db: float, env_factor: float = 2.6) -> float:
    """How much closer/farther a dB change implies. +6 dB ≈ half the distance."""
    return math.pow(10.0, -delta_db / (10.0 * env_factor))
