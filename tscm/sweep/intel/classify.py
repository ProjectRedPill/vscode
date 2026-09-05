"""Turn decoded attributes into a device class and a human summary.

Classification is deliberately conservative and ordered: an explicit self-report
(Fast Pair model ID, Microsoft device type, GAP appearance) always beats a
guess from a name string, and a name-string guess always beats an OUI guess.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.models import Band, DeviceClass
from . import signatures

#: (regex, class, label). Order matters — first match wins, so put the specific
#: patterns before the general ones.
_NAME_RULES: list[tuple[re.Pattern[str], DeviceClass, str]] = [
    (re.compile(p, re.IGNORECASE), cls, label)
    for p, cls, label in (
        (r"\bairpods\b", DeviceClass.AUDIO, "airpods"),
        (r"\biphone\b", DeviceClass.PHONE, "iphone"),
        (r"\bipad\b", DeviceClass.COMPUTER, "ipad"),
        (r"\bmac(book)?\b", DeviceClass.COMPUTER, "macbook"),
        (r"\bgalaxy\b", DeviceClass.PHONE, "galaxy"),
        (r"\bpixel\b", DeviceClass.PHONE, "pixel"),
        (r"\bdoorbell\b", DeviceClass.CAMERA, "doorbell"),
        (r"\b(webcam|ipcam|cam(era)?)\b", DeviceClass.CAMERA, "camera"),
        (r"\b(smart)?watch\b", DeviceClass.WEARABLE, "watch"),
        (r"\b(mi|fitness|smart)?\s?band\b", DeviceClass.WEARABLE, "fitness band"),
        (r"\bbuds\b", DeviceClass.AUDIO, "earbuds"),
        (r"\bhead(phones?|set)\b", DeviceClass.AUDIO, "headphones"),
        (r"\b(speaker|soundbar|sound\s?bar)\b", DeviceClass.AUDIO, "speaker"),
        (r"\bprinter\b", DeviceClass.PERIPHERAL, "printer"),
        (r"\bkeyboard\b", DeviceClass.PERIPHERAL, "keyboard"),
        (r"\bmouse\b", DeviceClass.PERIPHERAL, "mouse"),
        (r"\btv\b", DeviceClass.APPLIANCE, "tv"),
        (r"\b(router|access\s?point|repeater|extender)\b", DeviceClass.NETWORK, "router"),
        (r"\bthermostat\b", DeviceClass.SENSOR, "thermostat"),
        (r"\b(air)?tag\b", DeviceClass.TRACKER, "tag"),
        (r"\btracker\b", DeviceClass.TRACKER, "tracker"),
    )
]

# Weakest to strongest; later sources overwrite earlier ones.
_SOURCE_RANK = {
    "oui": 1,
    "name": 2,
    "cod": 3,
    "appearance": 4,
    "service": 5,
    "vendor-protocol": 6,
    "signature": 7,
}

_APPEARANCE_TO_CLASS = {
    "Phone": DeviceClass.PHONE, "Computer": DeviceClass.COMPUTER,
    "Laptop": DeviceClass.COMPUTER, "Tablet": DeviceClass.COMPUTER,
    "Watch": DeviceClass.WEARABLE, "Smartwatch": DeviceClass.WEARABLE,
    "Tag": DeviceClass.TRACKER, "Keyring": DeviceClass.TRACKER,
    "Generic Tag": DeviceClass.TRACKER, "Generic Keyring": DeviceClass.TRACKER,
    "Camera": DeviceClass.CAMERA, "Microphone": DeviceClass.MICROPHONE,
    "Generic Audio Sink": DeviceClass.AUDIO, "Audio Sink": DeviceClass.AUDIO,
    "Audio Source": DeviceClass.AUDIO, "Standalone Speaker": DeviceClass.AUDIO,
    "Wearable Earbud": DeviceClass.AUDIO, "Wearable Headset": DeviceClass.AUDIO,
    "Wearable Headphones": DeviceClass.AUDIO,
    "Keyboard": DeviceClass.PERIPHERAL, "Mouse": DeviceClass.PERIPHERAL,
    "Generic HID": DeviceClass.PERIPHERAL, "Gamepad": DeviceClass.PERIPHERAL,
    "Heart Rate Sensor": DeviceClass.MEDICAL, "Pulse Oximeter": DeviceClass.MEDICAL,
    "Glucose Meter": DeviceClass.MEDICAL, "Insulin Pump": DeviceClass.MEDICAL,
    "Network Device": DeviceClass.NETWORK, "Sensor": DeviceClass.SENSOR,
    "Generic Motion Sensor": DeviceClass.SENSOR,
    "Occupancy Sensor": DeviceClass.SENSOR,
    "Thermometer": DeviceClass.SENSOR, "Temperature Sensor": DeviceClass.SENSOR,
}

_COD_TO_CLASS = {
    "Computer": DeviceClass.COMPUTER, "Phone": DeviceClass.PHONE,
    "LAN/Network Access Point": DeviceClass.NETWORK,
    "Audio/Video": DeviceClass.AUDIO, "Peripheral": DeviceClass.PERIPHERAL,
    "Imaging": DeviceClass.CAMERA, "Wearable": DeviceClass.WEARABLE,
    "Health": DeviceClass.MEDICAL,
}

_SERVICE_TO_CLASS = {
    "Human Interface Device": DeviceClass.PERIPHERAL,
    "Heart Rate": DeviceClass.MEDICAL, "Glucose": DeviceClass.MEDICAL,
    "Blood Pressure": DeviceClass.MEDICAL, "Pulse Oximeter": DeviceClass.MEDICAL,
    "Environmental Sensing": DeviceClass.SENSOR,
    "Fitness Machine": DeviceClass.APPLIANCE,
    "Apple Find My network": DeviceClass.TRACKER,
    "Samsung SmartTag": DeviceClass.TRACKER,
    "Tile tracker": DeviceClass.TRACKER,
    "Eddystone beacon": DeviceClass.BEACON,
    "Indoor Positioning": DeviceClass.BEACON,
    "Exposure Notification (COVID contact tracing)": DeviceClass.PHONE,
}


def classify(
    attrs: dict[str, Any],
    *,
    band: Band,
    vendor: str | None = None,
    oui: str | None = None,
) -> tuple[DeviceClass, str, list[signatures.Signature]]:
    """Return (class, reason, matched signatures)."""
    best: tuple[int, DeviceClass, str] = (0, DeviceClass.UNKNOWN, "no distinguishing features")

    def offer(rank_key: str, cls: DeviceClass, reason: str) -> None:
        nonlocal best
        rank = _SOURCE_RANK[rank_key]
        if cls is not DeviceClass.UNKNOWN and rank >= best[0]:
            best = (rank, cls, reason)

    # 1. Band gives a floor.
    if band is Band.WIFI:
        offer("oui", DeviceClass.NETWORK, "seen as a Wi-Fi radio")
    elif band is Band.IR:
        offer("oui", DeviceClass.IR_EMITTER, "infrared emission")
    elif band is Band.ISM_SUB_GHZ:
        offer("oui", DeviceClass.SENSOR, "sub-GHz ISM transmitter")

    # 2. OUI vendor.
    if vendor:
        low = vendor.lower()
        for needle, cls in (
            ("hikvision", DeviceClass.CAMERA), ("dahua", DeviceClass.CAMERA),
            ("axis", DeviceClass.CAMERA), ("wyze", DeviceClass.CAMERA),
            ("reolink", DeviceClass.CAMERA), ("vivotek", DeviceClass.CAMERA),
            ("mobotix", DeviceClass.CAMERA), ("arlo", DeviceClass.CAMERA),
            ("ring", DeviceClass.CAMERA), ("eufy", DeviceClass.CAMERA),
            ("ubiquiti", DeviceClass.NETWORK), ("netgear", DeviceClass.NETWORK),
            ("tp-link", DeviceClass.NETWORK), ("cisco", DeviceClass.NETWORK),
            ("asustek", DeviceClass.NETWORK), ("d-link", DeviceClass.NETWORK),
            ("tile", DeviceClass.TRACKER), ("chipolo", DeviceClass.TRACKER),
            ("gps tracker", DeviceClass.TRACKER),
            ("bose", DeviceClass.AUDIO), ("sonos", DeviceClass.AUDIO),
            ("sennheiser", DeviceClass.AUDIO), ("beats", DeviceClass.AUDIO),
        ):
            if needle in low:
                offer("oui", cls, f"vendor {vendor}")
                break

    # 3. Name heuristics.
    # Matched on word boundaries, not as substrings. "Broadband RF field" is not
    # a fitness band, "Camden-guest" is not a camera, and "Octavia" is not a TV —
    # bare `in` checks produce exactly those errors, and a confidently wrong
    # class is worse here than an honest "unknown".
    name = str(attrs.get("name") or attrs.get("ssid") or "")
    if name:
        for pattern, cls, label in _NAME_RULES:
            if pattern.search(name):
                offer("name", cls, f"name matches '{label}'")
                break

    # 4. Class of Device (BR/EDR).
    cod_major = attrs.get("cod_major")
    if cod_major in _COD_TO_CLASS:
        offer("cod", _COD_TO_CLASS[cod_major], f"Class of Device: {cod_major}")
    if attrs.get("cod_minor") in ("Camera", "Video Camera", "Camcorder"):
        offer("cod", DeviceClass.CAMERA, "Class of Device minor: camera")
    if attrs.get("cod_minor") == "Microphone":
        offer("cod", DeviceClass.MICROPHONE, "Class of Device minor: microphone")
    if "Capturing" in (attrs.get("cod_services") or []):
        offer("cod", DeviceClass.MICROPHONE, "advertises the Capturing service (audio/video capture)")

    # 5. GAP appearance — the device stating its own form factor.
    ap = attrs.get("appearance_name")
    if ap in _APPEARANCE_TO_CLASS:
        offer("appearance", _APPEARANCE_TO_CLASS[ap], f"GAP appearance: {ap}")

    # 6. Advertised services.
    for svc in attrs.get("services") or []:
        if svc in _SERVICE_TO_CLASS:
            offer("service", _SERVICE_TO_CLASS[svc], f"advertises {svc}")

    # 7. Vendor protocol self-report — strongest non-signature evidence.
    # `class_hint` is what fusion stores; `device_class` is what a decoder emits
    # before fusion has seen it, so accept either.
    hint = attrs.get("class_hint") or attrs.get("device_class")
    if hint:
        try:
            offer(
                "vendor-protocol",
                DeviceClass(str(hint)),
                "decoded from the vendor's own advertisement",
            )
        except ValueError:
            pass

    # 8. Covert-hardware signatures.
    hits = signatures.match(
        names=[name, str(attrs.get("model") or "")],
        attrs=attrs,
        oui=oui,
    )
    for hit in hits:
        if hit.severity >= 2:
            try:
                offer("signature", DeviceClass(hit.device_class), f"matches signature: {hit.label}")
            except ValueError:
                pass
            break

    return best[1], best[2], hits


def summarize(attrs: dict[str, Any], device_class: DeviceClass) -> str:
    """One line a human can read without knowing what a UUID is."""
    bits: list[str] = []
    if attrs.get("model"):
        bits.append(str(attrs["model"]))
    if attrs.get("os_hint"):
        bits.append(f"running {attrs['os_hint']}")
    if attrs.get("apple_activity"):
        bits.append(f"state: {attrs['apple_activity']}")
    if attrs.get("tracker_network"):
        bits.append(f"on the {attrs['tracker_network']} network")
    if attrs.get("find_my_separated"):
        bits.append("SEPARATED from its owner")
    for key, label in (
        ("airpods_battery_left_pct", "L battery"),
        ("airpods_battery_right_pct", "R battery"),
        ("airpods_battery_case_pct", "case battery"),
        ("battery_pct", "battery"),
    ):
        if attrs.get(key) is not None:
            bits.append(f"{label} {attrs[key]}%")
    if attrs.get("services"):
        svcs = [s for s in attrs["services"] if s not in ("Generic Access", "Generic Attribute")]
        if svcs:
            bits.append("services: " + ", ".join(svcs[:4]))

    # Non-BLE bands carry none of the above, so give them their own summary
    # rather than falling through to a bare class name.
    if attrs.get("ir_flood"):
        bits.append(f"sustained IR flood for {attrs.get('ir_sustained_s', '?')}s")
    elif attrs.get("ir_protocol"):
        bits.append(f"{attrs['ir_protocol']} addr {attrs.get('ir_address')} "
                    f"cmd {attrs.get('ir_command')}")
    if attrs.get("band_label"):
        bits.append(str(attrs["band_label"]))
    if attrs.get("rf_excess_db") is not None:
        bits.append(f"{attrs['rf_excess_db']:+.0f} dB over baseline")
    elif attrs.get("excess_db") is not None:
        bits.append(f"{attrs['excess_db']:+.0f} dB over noise floor")
    if attrs.get("ssid") and attrs.get("security"):
        bits.append(f"{attrs['ssid']} ({attrs['security']})")
    if attrs.get("ism_temperature_C") is not None:
        bits.append(f"{attrs['ism_temperature_C']} °C")

    if not bits:
        bits.append(device_class.value.replace("_", " "))
    return "; ".join(bits)
