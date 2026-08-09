"""What can this setup actually detect, and what would change that.

One catalogue, three consumers: `sweep doctor`, the sweep report, and the web
UI's Coverage screen. They were drifting apart when each built its own answer,
and "what am I blind to?" is too important to have three versions of.

The central distinction this module exists to make clear:

    HOST    the machine running `sweep`. Its radios decide what is detected.
    CLIENT  whatever you are looking at the UI on. Contributes nothing.

That sounds obvious written down, and is very much not obvious when you are
holding the phone. An iPhone viewing the dashboard detects exactly zero of the
devices on it — iOS has no libusb, no raw Bluetooth HCI, no Wi-Fi monitor mode,
and hands apps rotating per-app UUIDs instead of BLE MAC addresses. The phone is
a screen. Saying so plainly, in the UI, is the point.
"""

from __future__ import annotations

import platform
import socket
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Band


@dataclass(frozen=True)
class Upgrade:
    """A thing you could buy or install, and what it would switch on."""

    id: str
    name: str                  # the product, as a noun
    action: str                # exactly what to do — a command, or what to buy
    kind: str                  # "software" | "hardware"
    cost: str                  # human string; "free" for software
    unlocks: tuple[str, ...]   # band values this makes available
    detail: str
    supported: bool = True     # False = sweep does not drive it yet


UPGRADES: dict[str, Upgrade] = {u.id: u for u in (
    Upgrade(
        id="bleak", name="bleak", action="pip install bleak", kind="software", cost="free",
        unlocks=(Band.BLE.value,),
        detail=(
            "Cross-platform BLE with full advertisement payloads. Without it "
            "sweep falls back to bluetoothctl, which reports names and signal "
            "but not the raw manufacturer data every vendor decoder needs."
        ),
    ),
    Upgrade(
        id="bluez", name="BlueZ", action="apt install bluez (or your distro's equivalent)",
        kind="software", cost="free",
        unlocks=(Band.BLE.value, Band.BT_CLASSIC.value),
        detail="Linux Bluetooth stack: bluetoothctl and hcitool.",
    ),
    Upgrade(
        id="nm", name="NetworkManager", action="apt install network-manager, or apt install iw",
        kind="software", cost="free",
        unlocks=(Band.WIFI.value,),
        detail="Ordinary Wi-Fi scanning. No monitor mode and no root needed.",
    ),
    Upgrade(
        id="rtl433", name="rtl_433", action="apt install rtl-433 (or build from source)",
        kind="software", cost="free",
        unlocks=(Band.ISM_SUB_GHZ.value,),
        detail="~250 sub-GHz protocol decoders. Needs an RTL-SDR to hear anything.",
    ),
    Upgrade(
        id="rtlsdr", name="RTL-SDR Blog V4 dongle", action="buy an RTL-SDR Blog V4 (USB)",
        kind="hardware", cost="~$40",
        unlocks=(Band.ISM_SUB_GHZ.value, Band.RF_BROADBAND.value),
        detail=(
            "500 kHz - 1.75 GHz receiver. Feeds rtl_433 for decoded sub-GHz "
            "traffic and rtl_power for spectrum sweeps. The single best value "
            "purchase here: it opens two bands at once."
        ),
    ),
    Upgrade(
        id="hackrf", name="HackRF One", action="buy a HackRF One (USB)",
        kind="hardware", cost="~$150-320",
        unlocks=(Band.RF_BROADBAND.value,),
        detail=(
            "1 MHz - 6 GHz. The only item that reaches 2.4 and 5.8 GHz, where "
            "analogue video transmitters live. Those carry no MAC, no name and "
            "nothing to scan for, so no other sensor can see them at all."
        ),
    ),
    Upgrade(
        id="rf_probe", name="ESP32 + AD8317 RF power probe",
        action="build the RF probe, then pass --rf-port /dev/ttyUSB0",
        kind="hardware", cost="~$20", unlocks=(Band.RF_BROADBAND.value,),
        detail=(
            "Log detector, roughly 1 MHz - 10 GHz, no tuning and no demodulation. "
            "Cannot identify anything, but cannot be defeated by encryption or by "
            "an unknown protocol either. Sweep it over surfaces; the level peaks "
            "over the emitter. Firmware in firmware/rf_probe."
        ),
    ),
    Upgrade(
        id="ir_probe", name="ESP32 + TSOP38238 + BPW34 IR probe",
        action="build the IR probe, then pass --ir-port /dev/ttyUSB0",
        kind="hardware", cost="~$13", unlocks=(Band.IR.value,),
        detail=(
            "Fit both sensors. The TSOP demodulates at 38 kHz and sees remote "
            "controls; the bare BPW34 photodiode sees steady un-modulated light, "
            "which is what a night-vision illuminator emits. A TSOP-only probe "
            "will never find a camera. Firmware in firmware/ir_probe."
        ),
    ),
    Upgrade(
        id="lirc", name="LIRC", action="apt install lirc", kind="software", cost="free",
        unlocks=(Band.IR.value,),
        detail=(
            "Coded infrared only. Cannot see IR illuminators — a 38 kHz "
            "demodulating receiver is deaf to un-modulated light by design."
        ),
    ),
    Upgrade(
        id="pyserial", name="pyserial", action="pip install pyserial",
        kind="software", cost="free",
        unlocks=(Band.IR.value, Band.RF_BROADBAND.value),
        detail="Needed to talk to either probe over USB serial.",
    ),
)}


@dataclass
class BandInfo:
    """What one band is for, in the user's language rather than the radio's."""

    band: str
    title: str
    detects: tuple[str, ...]
    blind_without: str
    upgrades: tuple[str, ...]


CATALOGUE: tuple[BandInfo, ...] = (
    BandInfo(
        band=Band.BLE.value,
        title="Bluetooth LE",
        detects=(
            "AirTags, SmartTags, Tile and Chipolo trackers — including whether "
            "they are separated from their owner",
            "Phones, watches and earbuds, with model, battery and screen state",
            "Beacons, and how long they have been installed",
            "Bare radio modules — the building block of home-made bugs",
        ),
        blind_without="every consumer tracker, and most modern accessories",
        upgrades=("bleak", "bluez"),
    ),
    BandInfo(
        band=Band.BT_CLASSIC.value,
        title="Bluetooth Classic",
        detects=(
            "Wireless microphones and body-worn recorders",
            "Dashcams, car kits and action cameras",
            "Devices declaring an audio or video capture role",
        ),
        blind_without=(
            "microphones and cameras that are BR/EDR-only, which a BLE-only "
            "scanner cannot see at all"
        ),
        upgrades=("bluez",),
    ),
    BandInfo(
        band=Band.WIFI.value,
        title="Wi-Fi",
        detects=(
            "Wi-Fi cameras — the most common covert camera by a wide margin",
            "Cameras hosting their own setup network when unconfigured",
            "Hidden-SSID and open access points at close range",
        ),
        blind_without="Wi-Fi cameras, which is the single biggest gap you can have",
        upgrades=("nm",),
    ),
    BandInfo(
        band=Band.ISM_SUB_GHZ.value,
        title="Sub-GHz ISM (315 / 433 / 868 / 915 MHz)",
        detects=(
            "Door and window contacts, PIR motion sensors",
            "Tyre-pressure sensors — which move with a vehicle, so they reveal a "
            "car that keeps reappearing",
            "Key fobs, remotes, and the cheap FSK bugs that live on 433 MHz",
        ),
        blind_without="alarm sensors, vehicle telemetry and low-cost sub-GHz bugs",
        upgrades=("rtlsdr", "rtl433"),
    ),
    BandInfo(
        band=Band.RF_BROADBAND.value,
        title="Broadband RF",
        detects=(
            "Analogue video transmitters at 1.2, 2.4 and 5.8 GHz",
            "Encrypted or unknown links that no decoder recognises",
            "Strong near-field emitters, located by sweeping a probe over surfaces",
            "Jamming and wideband interference",
        ),
        blind_without=(
            "anything without a digital identity — the classic wireless camera, "
            "and any protocol nobody has written a decoder for"
        ),
        upgrades=("rf_probe", "rtlsdr", "hackrf", "pyserial"),
    ),
    BandInfo(
        band=Band.IR.value,
        title="Infrared",
        detects=(
            "Night-vision illuminators — steady 850/940 nm light that is "
            "invisible to the eye and one of the most reliable camera tells",
            "Infrared remote and control traffic",
        ),
        blind_without="hidden cameras operating in the dark",
        upgrades=("ir_probe", "pyserial", "lirc"),
    ),
)

CATALOGUE_BY_BAND = {b.band: b for b in CATALOGUE}


# ---------------------------------------------------------------------------
# Client-side reality, stated once so the UI cannot get it wrong
# ---------------------------------------------------------------------------

CLIENT_NOTES: dict[str, dict[str, Any]] = {
    # Selected when the browser connects over loopback: same machine, same
    # radios. This is the ordinary desktop case and it deserves to be told
    # plainly, because "the radios are on the host" is technically true and
    # actively misleading when the host is the thing you are sitting at.
    "host_local": {
        "label": "this machine",
        "can_sense": True,
        "headline": "You are on the machine doing the sensing.",
        "why": (
            "The browser is just the interface — but it is running on the same "
            "computer as the radios, so everything listed here is what is "
            "physically near you right now."
        ),
        "tips": [
            "Walk around with the laptop and use the finder to close in on a device.",
            "Press m (or Mark location) whenever you move rooms, so anything that "
            "follows you gets flagged.",
            "Check the Coverage tab for the bands this machine cannot hear yet.",
        ],
    },
    "ios": {
        "label": "iPhone or iPad",
        "can_sense": False,
        "headline": "Your phone is the screen. The radios are on the host.",
        "why": (
            "iOS gives apps no libusb, so an SDR in the USB-C port is inert; no "
            "raw Bluetooth HCI; no Wi-Fi monitor mode; and rotating per-app UUIDs "
            "instead of BLE MAC addresses, which is exactly what identity fusion "
            "needs. None of that is fixable with an adapter."
        ),
        "tips": [
            "Share ▸ Add to Home Screen gives you a full-screen app with no browser bars.",
            "Your front camera is a free infrared viewer — its IR-cut filter is much "
            "weaker than the rear camera's. Darken the room, open the selfie camera and "
            "sweep it around: a night-vision illuminator shows as a faint purple glow.",
            "Leave iOS's own tracker alerts on. They cover Apple's network well, "
            "Google's partially, and Samsung SmartTags not at all — which is the gap "
            "this tool fills.",
            "A USB-C thermal camera (iPhone 15 and later) finds warm devices behind "
            "plastic, including recorders with no radio. It runs in its own app, not here.",
        ],
    },
    "android": {
        "label": "Android phone or tablet",
        "can_sense": False,
        "headline": "Your phone is the screen. The radios are on the host.",
        "why": (
            "Android is less locked down than iOS, but this build has no Android "
            "sensor backend — nothing you do on the phone contributes to detection."
        ),
        "tips": [
            "Add to Home screen for a full-screen app.",
            "Most phone cameras see 850 nm infrared faintly. Try the front camera in "
            "a dark room to spot a night-vision illuminator.",
        ],
    },
    "desktop": {
        "label": "Desktop browser",
        "can_sense": False,
        "headline": "The browser is the screen. The radios are on the host.",
        "why": (
            "Browsers cannot open raw radios. If this browser is on the same "
            "machine as the host, that machine's adapters are already doing the work."
        ),
        "tips": [],
    },
}


# ---------------------------------------------------------------------------

def lan_addresses() -> list[str]:
    """Best-effort list of addresses another device on the LAN could reach.

    The UDP-connect trick asks the routing table which local address would be
    used to reach the internet. It sends no packets and needs no connectivity —
    it is just the cleanest way to find the interface that actually matters,
    rather than listing every loopback and virtual bridge on the machine.
    """
    found: list[str] = []

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))     # TEST-NET-1, guaranteed unrouted
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found:
                found.append(addr)
    except (socket.gaierror, OSError):
        pass

    return [a for a in found if not a.startswith("127.")]


def host_info() -> dict[str, Any]:
    system = platform.system()
    return {
        "hostname": socket.gethostname(),
        "system": system,
        "pretty": {
            "Linux": "Linux", "Darwin": "macOS", "Windows": "Windows",
        }.get(system, system),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def assess(sensors: list[Any]) -> dict[str, Any]:
    """Build the full capability picture from probed sensors.

    `sensors` are `Sensor` instances that have had `probe()` called. Sensors not
    selected for this run are reported as "off" rather than omitted, because a
    band you chose not to enable is still a band you cannot see.
    """
    by_band: dict[str, list[Any]] = {}
    for sensor in sensors:
        by_band.setdefault(sensor.band.value, []).append(sensor)

    bands: list[dict[str, Any]] = []
    for info in CATALOGUE:
        found = by_band.get(info.band, [])
        active = [s for s in found if s.status.available]

        if active:
            status = "active"
            reason = "; ".join(s.status.reason for s in active if s.status.reason)
        elif found:
            status = "unavailable"
            reason = "; ".join(s.status.reason or "unavailable" for s in found)
        else:
            status = "off"
            reason = "sensor not enabled for this run"

        # Only suggest upgrades that would actually change this band's status.
        upgrades = [
            {**asdict(UPGRADES[u]), "unlocks": list(UPGRADES[u].unlocks)}
            for u in info.upgrades if u in UPGRADES
        ] if status != "active" else []

        bands.append({
            "band": info.band,
            "title": info.title,
            "status": status,
            "reason": reason,
            "detects": list(info.detects),
            "blind_without": info.blind_without,
            "sensors": [
                {
                    "name": s.name,
                    "available": s.status.available,
                    "reason": s.status.reason,
                    "hint": s.hint,
                    "observations": s.status.observations,
                    "errors": s.status.errors,
                }
                for s in found
            ],
            "upgrades": upgrades,
        })

    active_bands = [b["band"] for b in bands if b["status"] == "active"]
    missing = [b for b in bands if b["status"] != "active"]

    return {
        "host": host_info(),
        "bands": bands,
        "active_count": len(active_bands),
        "total_count": len(bands),
        "active_bands": active_bands,
        "blind_spots": [
            {"band": b["band"], "title": b["title"], "cost": b["blind_without"]}
            for b in missing
        ],
        "next_upgrade": _best_next(missing),
        "client_notes": CLIENT_NOTES,
    }


def _best_next(missing: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one purchase or install that would open the most bands.

    Free software wins ties: telling someone to buy a dongle when a `pip
    install` would do is how a tool loses trust.
    """
    if not missing:
        return None

    missing_bands = {b["band"] for b in missing}
    scored: list[tuple[int, int, Upgrade]] = []
    for upgrade in UPGRADES.values():
        gain = len(set(upgrade.unlocks) & missing_bands)
        if gain:
            scored.append((gain, 1 if upgrade.kind == "software" else 0, upgrade))
    if not scored:
        return None

    gain, _, best = max(scored, key=lambda t: (t[0], t[1]))
    return {
        **asdict(best),
        "unlocks": list(best.unlocks),
        "bands_gained": gain,
    }
