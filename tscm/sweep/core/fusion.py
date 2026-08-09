"""Identity resolution: observations in, devices out.

The hard problem in this whole system is not capture, it is deciding that two
observations are the same thing. Three cases, in increasing difficulty:

  1. Same band, same address. Trivial.
  2. Same device, different band (a phone's BLE MAC and its Wi-Fi BSSID are
     unrelated numbers). Solved by correlating stable payload facts — names,
     vendor IDs, and the fact that consumer chipsets often assign adjacent MACs
     to their BLE and Wi-Fi radios.
  3. Same device, same band, rotated address. This is the one that matters for
     counter-surveillance, because a tracker that rotates its MAC every 15
     minutes looks like a stream of strangers unless you can re-link it.

For (3) we key on whatever the payload leaks that survives rotation: Tile's
static ID, Microsoft's rotating-but-slower account hash, a stable local name, a
Fast Pair model ID, or the exact set of advertised service UUIDs combined with
the advertised TX power. None of these is proof, so every link is scored and
recorded with its reason, and the UI shows the reason rather than asserting
identity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..intel import classify as classifier
from ..intel import oui
from .models import Band, Device, DeviceClass, Observation, Track
from .rssi import KalmanRssi

#: Attributes that identify a device across MAC rotation, best first.
LINK_KEYS: tuple[tuple[str, float, str], ...] = (
    ("tile_id", 0.98, "Tile broadcasts a static identifier"),
    ("find_my_pubkey", 0.95, "same Find My rotating public key"),
    ("eddystone_instance", 0.92, "same Eddystone instance ID"),
    ("ibeacon_uuid", 0.75, "same iBeacon UUID/major/minor"),
    ("ms_device_hash", 0.85, "same Microsoft CDP account hash"),
    ("fast_pair_model_id", 0.55, "same Fast Pair model"),
    ("smarttag_privacy_id", 0.90, "same SmartTag privacy ID"),
    ("modalias", 0.70, "same USB/BT modalias"),
)


@dataclass
class LinkEvidence:
    reason: str
    confidence: float
    previous_address: str
    ts: float = field(default_factory=time.time)


@dataclass
class FusionConfig:
    #: Below this score two observations stay separate devices.
    link_threshold: float = 0.6
    #: A rotated address only links to a device seen within this window.
    rotation_window_s: float = 900.0
    #: Kalman tuning, exposed because a moving operator wants faster tracking.
    kalman_q: float = 0.12
    kalman_r: float = 9.0
    #: Devices with no observation for this long stop being "present".
    stale_after_s: float = 120.0


class Fusion:
    """Owns the device table. Single-threaded, driven by the engine loop."""

    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()
        self.devices: dict[str, Device] = {}
        #: (band, address) -> device id
        self._by_address: dict[tuple[str, str], str] = {}
        #: link-key value -> device id
        self._by_link_key: dict[tuple[str, str], str] = {}
        self._filters: dict[tuple[str, str], KalmanRssi] = {}
        self.links: dict[str, list[LinkEvidence]] = {}
        self.observation_count = 0

    # -- ingest ----------------------------------------------------------

    def ingest(self, obs: Observation) -> Device:
        self.observation_count += 1
        key = obs.key()

        device = self._lookup(obs, key)
        if device is None:
            # Seed the timestamps from the observation, not from "now" — a
            # replayed or back-dated observation must not look freshly seen, or
            # the rotation window silently accepts links it should reject.
            device = Device(first_seen=obs.ts, last_seen=obs.ts)
            self.devices[device.id] = device

        self._by_address[key] = device.id
        device.aliases.add(f"{obs.band.value}:{obs.address}")
        device.last_seen = max(device.last_seen, obs.ts)

        self._update_track(device, obs, key)
        self._merge_attrs(device, obs)
        self._index_link_keys(device, obs)
        self._reclassify(device, obs)
        return device

    def _lookup(self, obs: Observation, key: tuple[str, str]) -> Device | None:
        existing = self._by_address.get(key)
        if existing and existing in self.devices:
            return self.devices[existing]

        candidate, reason, confidence = self._match_rotated(obs)
        if candidate is not None and confidence >= self.config.link_threshold:
            self.links.setdefault(candidate.id, []).append(
                LinkEvidence(reason, confidence, obs.address)
            )
            candidate.set_attr("rotation_detected", True, "fusion")
            return candidate

        return self._match_cross_band(obs)

    def _match_rotated(self, obs: Observation) -> tuple[Device | None, str, float]:
        """Re-link an address that rotated, using payload facts."""
        best: tuple[Device | None, str, float] = (None, "", 0.0)
        now = obs.ts

        for attr, confidence, reason in LINK_KEYS:
            value = obs.attrs.get(attr)
            if not value:
                continue
            owner = self._by_link_key.get((attr, str(value)))
            if owner and owner in self.devices:
                dev = self.devices[owner]
                if now - dev.last_seen <= self.config.rotation_window_s and confidence > best[2]:
                    best = (dev, reason, confidence)

        if best[2] >= 0.9:
            return best

        # Fall back to a name + advertisement-shape fingerprint. A stable local
        # name across a MAC change is weak on its own (many devices share
        # names), so it only counts when the advertised service set matches too.
        name = obs.attrs.get("name")
        if name and obs.band is Band.BLE:
            shape = self._ad_shape(obs.attrs)
            for dev in self.devices.values():
                if now - dev.last_seen > self.config.rotation_window_s:
                    continue
                if dev.attrs.get("name") != name:
                    continue
                score = 0.55
                if shape and self._ad_shape(dev.attrs) == shape:
                    score = 0.8
                if score > best[2]:
                    best = (dev, "same local name and advertisement shape", score)

        return best

    @staticmethod
    def _ad_shape(attrs: dict[str, Any]) -> str | None:
        uuids = attrs.get("service_uuids")
        if not uuids:
            return None
        tx = attrs.get("tx_power")
        return "|".join(sorted(str(u).lower() for u in uuids)) + f"#{tx}"

    def _match_cross_band(self, obs: Observation) -> Device | None:
        """Link a device seen on a second radio.

        Consumer SoCs hand out consecutive MACs to their Wi-Fi and Bluetooth
        radios far more often than chance — Espressif, Broadcom and Realtek all
        do it. A same-OUI address within a small numeric distance, seen at the
        same time, is a strong hint. Never applied to rotating addresses, where
        the number carries no information.
        """
        if obs.address_is_random or not oui.is_mac(obs.address):
            return None

        target = oui.normalize(obs.address)
        try:
            target_int = int(target, 16)
        except ValueError:
            return None

        for dev in self.devices.values():
            if obs.ts - dev.last_seen > 60.0:
                continue
            for band, addr in dev.tracks:
                if band == obs.band.value or not oui.is_mac(addr):
                    continue
                other = oui.normalize(addr)
                if other[:6] != target[:6]:
                    continue
                try:
                    delta = abs(int(other, 16) - target_int)
                except ValueError:
                    continue
                if delta <= 4:
                    self.links.setdefault(dev.id, []).append(
                        LinkEvidence(
                            f"adjacent MAC on {band} (delta {delta}) — same chipset",
                            0.7,
                            obs.address,
                        )
                    )
                    dev.set_attr("multi_radio", True, "fusion")
                    return dev
        return None

    # -- state updates ---------------------------------------------------

    def _update_track(self, device: Device, obs: Observation, key: tuple[str, str]) -> None:
        track = device.tracks.get(key)
        if track is None:
            track = Track(
                band=obs.band, address=obs.address,
                first_seen=obs.ts, last_seen=obs.ts,
            )
            device.tracks[key] = track
        track.push(obs.ts, obs.rssi)

        if obs.rssi is not None:
            filt = self._filters.get(key)
            if filt is None:
                filt = KalmanRssi(self.config.kalman_q, self.config.kalman_r)
                self._filters[key] = filt
            track.rssi_smoothed = round(filt.update(obs.rssi), 1)

    def _merge_attrs(self, device: Device, obs: Observation) -> None:
        for key, value in obs.attrs.items():
            if key == "device_class":
                # A decoder's class is a *hint*, not the answer — it is one
                # input to classify() alongside appearance, CoD and signatures.
                # Kept under its own key so it can never be mistaken for the
                # resolved class the rest of the tool reads off the Device.
                device.set_attr("class_hint", value, obs.sensor)
                continue
            if key in ("rotating", "mac"):
                continue
            if key == "services" and isinstance(value, list):
                merged = list(dict.fromkeys((device.attrs.get("services") or []) + value))
                device.set_attr("services", merged, obs.sensor)
                continue
            device.set_attr(key, value, obs.sensor)

        if obs.name:
            device.set_attr("name", obs.name, obs.sensor)
        if obs.tx_power is not None:
            device.set_attr("tx_power", obs.tx_power, obs.sensor)
        if obs.frequency_hz:
            device.set_attr("frequency_hz", obs.frequency_hz, obs.sensor)
        if obs.channel is not None:
            device.set_attr("channel", obs.channel, obs.sensor)

        vendor = obs.attrs.get("vendor")
        if vendor and not device.vendor:
            device.vendor = str(vendor)
        model = obs.attrs.get("model")
        if model:
            device.model = str(model)
        os_hint = obs.attrs.get("os_hint")
        if os_hint and not device.os_hint:
            device.os_hint = str(os_hint)

    def _index_link_keys(self, device: Device, obs: Observation) -> None:
        for attr, _, _ in LINK_KEYS:
            value = obs.attrs.get(attr)
            if value:
                self._by_link_key[(attr, str(value))] = device.id

    def _reclassify(self, device: Device, obs: Observation) -> None:
        cls, reason, hits = classifier.classify(
            device.attrs,
            band=obs.band,
            vendor=device.vendor,
            oui=str(device.attrs.get("oui") or "") or None,
        )
        # Never downgrade a confident class back to unknown on a sparse packet.
        if cls is not DeviceClass.UNKNOWN or device.device_class is DeviceClass.UNKNOWN:
            device.device_class = cls
        device.set_attr("class_reason", reason, "classify")
        if hits:
            device.set_attr("signatures", [h.id for h in hits], "classify")
            device.set_attr("signature_labels", [h.label for h in hits], "classify")
        device.set_attr(
            "summary", classifier.summarize(device.attrs, device.device_class), "classify"
        )

    # -- queries ---------------------------------------------------------

    def present(self, now: float | None = None) -> list[Device]:
        now = now or time.time()
        return [
            d for d in self.devices.values()
            if now - d.last_seen <= self.config.stale_after_s
        ]

    def get(self, needle: str) -> Device | None:
        """Find a device by id prefix, address, alias or display name."""
        needle_l = needle.lower()
        if needle_l in self.devices:
            return self.devices[needle_l]
        for dev in self.devices.values():
            if dev.id.startswith(needle_l):
                return dev
        for (band, addr), dev_id in self._by_address.items():
            if addr == needle_l or f"{band}:{addr}" == needle_l:
                return self.devices.get(dev_id)
        for dev in self.devices.values():
            if dev.display_name().lower() == needle_l:
                return dev
        for dev in self.devices.values():
            if needle_l in dev.display_name().lower():
                return dev
        return None

    def link_history(self, device_id: str) -> list[LinkEvidence]:
        return self.links.get(device_id, [])

    def stats(self) -> dict[str, Any]:
        by_band: dict[str, int] = {}
        for dev in self.devices.values():
            for band in dev.bands:
                by_band[band.value] = by_band.get(band.value, 0) + 1
        return {
            "devices": len(self.devices),
            "observations": self.observation_count,
            "present": len(self.present()),
            "by_band": by_band,
            "rotations_linked": sum(len(v) for v in self.links.values()),
        }
