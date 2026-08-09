"""Threat rules.

A rule looks at one device plus the sweep context and either fires or does not.
Rules are advisory and additive: they never suppress a device, never delete
data, and every one of them carries the reasoning that produced it so a person
can disagree with it.

The single most important idea here is the **location epoch**. A tracker in your
bag and a tracker on your neighbour's shelf look identical from one spot — both
are just a tag with a good signal. They stop looking identical the moment you
move: the neighbour's tag drops out, yours does not. So the operator marks a new
epoch when they change location (key `m` in the live view, `sweep mark` on the
CLI, or automatically from GPS when available), and the follow rules ask "has
this been present in several epochs?" rather than "is this nearby?".

That is the same insight AirGuard uses, generalised to every band rather than
just Find My.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.models import Band, Device, DeviceClass, Finding, Trust


@dataclass
class SweepContext:
    """What the rule engine knows beyond a single device."""

    epoch: int = 0
    epoch_started: float = field(default_factory=time.time)
    #: device id -> set of epochs it was observed in
    epochs_seen: dict[str, set[int]] = field(default_factory=dict)
    #: Devices present at the start, used to spot arrivals.
    baseline_ids: set[str] = field(default_factory=set)
    #: Rolling median RSSI across all devices; a proxy for the noise floor.
    ambient_rssi: float | None = None
    mobile: bool = False   # operator is walking, set by the UI
    started: float = field(default_factory=time.time)

    def note_seen(self, device_id: str) -> None:
        self.epochs_seen.setdefault(device_id, set()).add(self.epoch)

    def epochs_for(self, device_id: str) -> int:
        return len(self.epochs_seen.get(device_id, ()))

    def new_epoch(self) -> int:
        self.epoch += 1
        self.epoch_started = time.time()
        return self.epoch


Rule = Callable[[Device, SweepContext], Finding | None]
RULES: list[tuple[str, Rule]] = []


def rule(name: str) -> Callable[[Rule], Rule]:
    def wrap(fn: Rule) -> Rule:
        RULES.append((name, fn))
        return fn

    return wrap


# ---------------------------------------------------------------------------
# Trackers
# ---------------------------------------------------------------------------

@rule("tracker.separated")
def _separated_tracker(dev: Device, ctx: SweepContext) -> Finding | None:
    if not dev.attrs.get("find_my_separated"):
        return None
    network = dev.attrs.get("tracker_network", "a finding network")
    return Finding(
        rule="tracker.separated",
        severity=2,
        title=f"Tracker broadcasting as separated from its owner ({network})",
        detail=(
            "Finding-network tags only advertise the separated state when their "
            "owner's phone is not nearby. On its own this is common — it is any "
            "tag whose owner stepped away. It matters if the tag stays with you "
            "as you move. Mark a new location (m) and see whether it is still here."
        ),
        evidence={
            "network": network,
            "state": dev.attrs.get("find_my_state") or dev.attrs.get("smarttag_state"),
            "battery": dev.attrs.get("find_my_battery"),
        },
    )


@rule("tracker.following")
def _following_tracker(dev: Device, ctx: SweepContext) -> Finding | None:
    if dev.device_class is not DeviceClass.TRACKER:
        return None
    epochs = ctx.epochs_for(dev.id)
    if epochs < 3:
        return None
    return Finding(
        rule="tracker.following",
        severity=4,
        title=f"Tracker has followed you across {epochs} locations",
        detail=(
            f"This tag has been detected in {epochs} separate locations you marked. "
            "A tag belonging to someone else's stationary property cannot do that. "
            "Treat this as a probable unwanted tracker: search your bag, clothing "
            "and vehicle. Do not destroy it — it may be evidence."
        ),
        evidence={
            "epochs": epochs,
            "network": dev.attrs.get("tracker_network"),
            "first_seen": dev.first_seen,
            "rotation_detected": dev.attrs.get("rotation_detected", False),
        },
    )


@rule("tracker.persistent-rotating")
def _persistent_rotating(dev: Device, ctx: SweepContext) -> Finding | None:
    """A rotating identifier that keeps coming back is a device travelling with you.

    Address rotation is a privacy feature, and most rotating devices are just
    phones. It becomes interesting when we have *linked* several rotations of
    the same device together over a long period across epochs — that is the
    signature of something that stays in range no matter where you go.
    """
    if not dev.attrs.get("rotation_detected"):
        return None
    epochs = ctx.epochs_for(dev.id)
    duration = dev.last_seen - dev.first_seen
    if epochs < 3 or duration < 900:
        return None
    if dev.device_class in (DeviceClass.PHONE, DeviceClass.COMPUTER, DeviceClass.WEARABLE):
        # A phone that follows you is usually your own or a companion's.
        return None
    return Finding(
        rule="tracker.persistent-rotating",
        severity=3,
        title="Rotating-address device present across many locations",
        detail=(
            f"Seen for {duration / 60:.0f} minutes across {epochs} locations, "
            "re-identified through address rotations. Devices that rotate their "
            "address are trying not to be followed — one that follows you anyway "
            "is worth accounting for."
        ),
        evidence={"epochs": epochs, "duration_s": round(duration)},
    )


# ---------------------------------------------------------------------------
# Cameras and microphones
# ---------------------------------------------------------------------------

@rule("camera.signature")
def _camera_signature(dev: Device, ctx: SweepContext) -> Finding | None:
    sigs = dev.attrs.get("signatures") or []
    hits = [s for s in sigs if s.startswith("cam.")]
    if not hits:
        return None
    severe = "cam.generic-ap" in hits or "cam.oui" in hits
    return Finding(
        rule="camera.signature",
        severity=3 if severe else 1,
        title="Device matches a camera signature",
        detail=(
            "Identification is by name pattern and hardware address, both of "
            "which a determined operator can change. A match means 'looks like a "
            "camera', not 'is a camera' — and an absence of matches is not an "
            "all-clear. Confirm physically."
        ),
        evidence={
            "signatures": hits,
            "labels": dev.attrs.get("signature_labels"),
            "name": dev.attrs.get("name") or dev.attrs.get("ssid"),
            "vendor": dev.vendor,
        },
    )


@rule("audio.capture-capable")
def _capture_capable(dev: Device, ctx: SweepContext) -> Finding | None:
    services = dev.attrs.get("cod_services") or []
    if "Capturing" not in services and dev.device_class is not DeviceClass.MICROPHONE:
        return None
    if dev.trust in (Trust.MINE, Trust.KNOWN):
        return None
    return Finding(
        rule="audio.capture-capable",
        severity=2,
        title="Device advertises an audio/video capture role",
        detail=(
            "The Bluetooth Class of Device includes the Capturing service bit, "
            "meaning the device presents itself as a microphone or camera source. "
            "Headsets set this bit too, so check what it is before worrying."
        ),
        evidence={"cod_services": services, "cod_minor": dev.attrs.get("cod_minor")},
    )


@rule("ir.illuminator")
def _ir_flood(dev: Device, ctx: SweepContext) -> Finding | None:
    if not dev.attrs.get("ir_flood"):
        return None
    return Finding(
        rule="ir.illuminator",
        severity=4,
        title="Sustained infrared illumination detected",
        detail=(
            "Steady un-modulated infrared with no remote-control protocol is how "
            "a night-vision camera lights a dark room, and it is invisible to the "
            "eye. Darken the room, sweep the probe, and look for the source — a "
            "phone camera without an IR-cut filter (most front cameras) will often "
            "show it as a faint purple glow."
        ),
        evidence={
            "level": dev.attrs.get("ir_level_adc"),
            "sustained_s": dev.attrs.get("ir_sustained_s"),
        },
    )


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

@rule("wifi.hidden-ssid-close")
def _hidden_ssid(dev: Device, ctx: SweepContext) -> Finding | None:
    if not dev.attrs.get("hidden_ssid"):
        return None
    rssi = dev.rssi
    if rssi is None or rssi < -55:
        return None
    return Finding(
        rule="wifi.hidden-ssid-close",
        severity=2,
        title="Hidden-SSID access point at close range",
        detail=(
            f"An access point within a few metres ({rssi:.0f} dBm) that does not "
            "broadcast its name. Hiding an SSID provides no real security, so it "
            "is usually either an IoT device's setup network or someone who did "
            "not want it listed."
        ),
        evidence={"rssi": rssi, "bssid": dev.address, "vendor": dev.vendor},
    )


@rule("wifi.open-ap-close")
def _open_ap(dev: Device, ctx: SweepContext) -> Finding | None:
    if Band.WIFI not in dev.bands:
        return None
    security = str(dev.attrs.get("security") or "").lower()
    if security not in ("", "open", "none", "--"):
        return None
    rssi = dev.rssi
    if rssi is None or rssi < -50:
        return None
    return Finding(
        rule="wifi.open-ap-close",
        severity=2,
        title="Unencrypted access point within a few metres",
        detail=(
            "Cheap cameras and IoT gadgets host an open network while unconfigured. "
            "An open AP this close in a private space is worth identifying."
        ),
        evidence={"ssid": dev.attrs.get("ssid"), "rssi": rssi},
    )


# ---------------------------------------------------------------------------
# RF
# ---------------------------------------------------------------------------

@rule("rf.near-field")
def _near_field(dev: Device, ctx: SweepContext) -> Finding | None:
    if Band.RF_BROADBAND not in dev.bands:
        return None
    excess = dev.attrs.get("rf_excess_db") or dev.attrs.get("excess_db")
    if excess is None or float(excess) < 15:
        return None
    return Finding(
        rule="rf.near-field",
        severity=3,
        title=f"Strong RF field, {float(excess):.0f} dB above local baseline",
        detail=(
            "A broadband power detector cannot identify what is transmitting — "
            "only that something is, and roughly where. Sweep slowly; the level "
            "peaks over the emitter. Rule out your own phone and Wi-Fi first by "
            "putting them in airplane mode and re-checking."
        ),
        evidence={
            "excess_db": excess,
            "dbm": dev.attrs.get("rf_dbm") or dev.attrs.get("power_dbfs"),
            "band": dev.attrs.get("band_label"),
        },
    )


@rule("rf.unknown-carrier")
def _unknown_carrier(dev: Device, ctx: SweepContext) -> Finding | None:
    if Band.RF_BROADBAND not in dev.bands or not dev.attrs.get("band_label"):
        return None
    label = str(dev.attrs["band_label"])
    if "video" not in label.lower():
        return None
    return Finding(
        rule="rf.unknown-carrier",
        severity=3,
        title=f"Carrier in an analogue video band ({label})",
        detail=(
            "1.2, 2.4 and 5.8 GHz analogue video transmitters are the classic "
            "wireless-camera technology and carry no digital identity at all — no "
            "MAC, no name, nothing to scan for. A persistent carrier in these "
            "bands with no matching Wi-Fi AP deserves a physical search."
        ),
        evidence={"band": label, "frequency_hz": dev.attrs.get("frequency_hz")},
    )


@rule("rf.jamming")
def _jamming(dev: Device, ctx: SweepContext) -> Finding | None:
    excess = dev.attrs.get("excess_db")
    if excess is None or float(excess) < 30:
        return None
    if dev.attrs.get("rtl433_model"):
        return None
    return Finding(
        rule="rf.jamming",
        severity=4,
        title="Very high wideband energy — possible jamming",
        detail=(
            "Energy far above the noise floor with nothing decodable in it. That "
            "is what a jammer looks like. It is also what a failing switch-mode "
            "power supply, an LED driver or a microwave oven looks like — verify "
            "by walking away and by switching off nearby appliances."
        ),
        evidence={"excess_db": excess, "frequency_hz": dev.attrs.get("frequency_hz")},
    )


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

@rule("device.arrived")
def _arrived(dev: Device, ctx: SweepContext) -> Finding | None:
    """Something appeared after the sweep started, in a space you are watching."""
    if dev.id in ctx.baseline_ids or ctx.mobile:
        return None
    if dev.first_seen - ctx.started < 60:
        return None
    if dev.device_class in (DeviceClass.PHONE, DeviceClass.COMPUTER, DeviceClass.UNKNOWN):
        return None
    return Finding(
        rule="device.arrived",
        severity=1,
        title="New device appeared during the sweep",
        detail=(
            "Not present at baseline. In a static sweep of a fixed room, an "
            "arrival is either something waking from sleep or something that "
            "physically entered."
        ),
        evidence={"first_seen": dev.first_seen, "class": dev.device_class.value},
    )


@rule("device.unnamed-close")
def _unnamed_close(dev: Device, ctx: SweepContext) -> Finding | None:
    """Nameless hardware at arm's length.

    Finished consumer products almost always set a name. Bare modules — the
    building blocks of home-made devices — usually do not.
    """
    if dev.attrs.get("name") or dev.attrs.get("ssid"):
        return None
    rssi = dev.rssi
    if rssi is None or rssi < -50:
        return None
    if dev.device_class not in (DeviceClass.UNKNOWN, DeviceClass.COVERT):
        return None
    return Finding(
        rule="device.unnamed-close",
        severity=2,
        title="Unnamed device at very close range",
        detail=(
            f"Advertising at {rssi:.0f} dBm — within roughly a metre — with no "
            "name, no recognised services and no vendor. That combination is "
            "unusual for a finished product and typical of a bare radio module."
        ),
        evidence={"rssi": rssi, "address": dev.address, "bands": [b.value for b in dev.bands]},
    )


@rule("device.signature")
def _generic_signature(dev: Device, ctx: SweepContext) -> Finding | None:
    sigs = dev.attrs.get("signatures") or []
    interesting = [s for s in sigs if s.startswith(("bug.", "tracker.gps", "ism.covert"))]
    if not interesting:
        return None
    from ..intel.signatures import by_id

    top = max((by_id(s) for s in interesting if by_id(s)), key=lambda s: s.severity)  # type: ignore[union-attr]
    return Finding(
        rule="device.signature",
        severity=top.severity,
        title=top.label,
        detail=top.why,
        evidence={"signatures": interesting, "name": dev.attrs.get("name")},
    )


# ---------------------------------------------------------------------------

def evaluate(dev: Device, ctx: SweepContext) -> list[Finding]:
    """Run every rule against one device. Findings replace the previous set."""
    ctx.note_seen(dev.id)
    if dev.trust is Trust.BLOCKED:
        return []

    out: list[Finding] = []
    for name, fn in RULES:
        try:
            finding = fn(dev, ctx)
        except Exception:
            # A broken rule must never take down a sweep in progress.
            continue
        if finding is not None:
            out.append(finding)

    if dev.trust in (Trust.MINE, Trust.KNOWN):
        # Keep the findings for the record, but strip their urgency.
        for f in out:
            f.severity = min(f.severity, 1)
    if dev.trust is Trust.SUSPECT:
        for f in out:
            f.severity = min(4, f.severity + 1)

    out.sort(key=lambda f: -f.severity)
    dev.findings = out
    return out
