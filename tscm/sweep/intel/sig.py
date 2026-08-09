"""Bluetooth SIG assigned-number lookups.

Bundled as literals rather than fetched at runtime: a counter-surveillance tool
has to work on an air-gapped laptop in a hotel room. The tables are the subsets
that actually appear in the wild — full SIG registries run to thousands of
entries and add nothing but weight. `load_external()` merges a fuller set if the
host has one (bluez ships `/usr/share/hwdata` style files, Wireshark ships
manuf).
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Company identifiers (BLE manufacturer-specific data, first 2 bytes LE)
# ---------------------------------------------------------------------------

COMPANY_IDS: dict[int, str] = {
    0x0000: "Ericsson", 0x0001: "Nokia Mobile Phones", 0x0002: "Intel",
    0x0003: "IBM", 0x0004: "Toshiba", 0x0005: "3Com", 0x0006: "Microsoft",
    0x0007: "Lucent", 0x0008: "Motorola", 0x0009: "Infineon",
    0x000A: "Cambridge Silicon Radio", 0x000D: "Texas Instruments",
    0x000F: "Broadcom", 0x0010: "Mitel", 0x0012: "Zeevo", 0x0013: "Atmel",
    0x0015: "Digianswer", 0x0018: "Transilica", 0x001D: "Qualcomm",
    0x0025: "Eclipse", 0x002D: "Synopsys", 0x0030: "ST Microelectronics",
    0x003A: "Panasonic", 0x0046: "MediaTek", 0x004C: "Apple",
    0x0057: "Harman International", 0x0059: "Nordic Semiconductor",
    0x0065: "Hewlett-Packard", 0x0075: "Samsung Electronics",
    0x0078: "Nike", 0x0087: "Garmin", 0x008A: "Jawbone",
    0x0099: "Zscan Software", 0x009E: "Bose", 0x00C4: "LG Electronics",
    0x00D2: "Sennheiser", 0x00E0: "Google", 0x0104: "Fitbit",
    0x0110: "Bang & Olufsen", 0x0118: "Beats Electronics",
    0x0131: "Cypress Semiconductor", 0x0141: "Fossil",
    0x0154: "Logitech", 0x0157: "Huawei", 0x0171: "Amazon",
    0x0180: "Withings", 0x01A5: "Xiaomi", 0x01D7: "Anhui Huami (Amazfit)",
    0x01DA: "Tile", 0x0201: "Chipolo", 0x0211: "Sony",
    0x0224: "Espressif", 0x0225: "Realtek", 0x0276: "Oura Health",
    0x02D0: "Xiaomi Inc.", 0x02E0: "Roku", 0x02FF: "Silicon Labs",
    0x0310: "SGL Italia", 0x038F: "Xiaomi Communications",
    0x03DA: "Ubiquiti", 0x0499: "Ruuvi Innovations",
    0x0500: "Wyze Labs", 0x0553: "Tuya", 0x05A7: "Sonos",
    0x0644: "Shenzhen Yidian (generic tracker)",
    0x06D8: "Ring (Amazon)", 0x0757: "Govee", 0x075B: "Eufy (Anker)",
    0x0822: "Arlo Technologies", 0x0A1E: "Insta360",
    0x0C60: "DJI", 0x0E4C: "Reolink", 0xFEED: "Tile (legacy)",
    0xFFFF: "Reserved / test",
}

# ---------------------------------------------------------------------------
# 16-bit GATT service UUIDs. A device's advertised services are the single most
# informative field on BLE — they say what it *does*.
# ---------------------------------------------------------------------------

SERVICE_UUIDS: dict[int, str] = {
    0x1800: "Generic Access", 0x1801: "Generic Attribute",
    0x1802: "Immediate Alert", 0x1803: "Link Loss", 0x1804: "Tx Power",
    0x1805: "Current Time", 0x1808: "Glucose", 0x1809: "Health Thermometer",
    0x180A: "Device Information", 0x180D: "Heart Rate",
    0x180F: "Battery Service", 0x1810: "Blood Pressure",
    0x1812: "Human Interface Device", 0x1813: "Scan Parameters",
    0x1814: "Running Speed and Cadence", 0x1816: "Cycling Speed and Cadence",
    0x1818: "Cycling Power", 0x181A: "Environmental Sensing",
    0x181B: "Body Composition", 0x181C: "User Data", 0x181D: "Weight Scale",
    0x181E: "Bond Management", 0x1820: "Internet Protocol Support",
    0x1821: "Indoor Positioning", 0x1822: "Pulse Oximeter",
    0x1824: "Transport Discovery", 0x1826: "Fitness Machine",
    0x1827: "Mesh Provisioning", 0x1828: "Mesh Proxy",
    0x183A: "Insulin Delivery", 0xFD5A: "Samsung SmartTag",
    0xFD6F: "Exposure Notification (COVID contact tracing)",
    0xFDF3: "Amazon Sidewalk", 0xFE07: "Microsoft Swift Pair",
    0xFE2C: "Google Fast Pair", 0xFE9F: "Google (misc)",
    0xFEAA: "Eddystone beacon", 0xFEED: "Tile tracker",
    0xFD44: "Apple Find My network", 0xFE95: "Xiaomi MiBeacon",
    0xFDA5: "Apple continuity", 0xFE59: "Nordic DFU (firmware update mode)",
    0xFE61: "Logitech", 0xFE8F: "Apple", 0xFEBE: "Bose",
    0xFD3D: "Wyze", 0xFD6E: "Amazon", 0xFCF1: "Google LE",
}

# ---------------------------------------------------------------------------
# GAP appearance (0x19). Encodes category<<6 | subcategory.
# ---------------------------------------------------------------------------

APPEARANCE_CATEGORIES: dict[int, str] = {
    0: "Unknown", 1: "Phone", 2: "Computer", 3: "Watch", 4: "Clock",
    5: "Display", 6: "Remote Control", 7: "Eye Glasses", 8: "Tag",
    9: "Keyring", 10: "Media Player", 11: "Barcode Scanner", 12: "Thermometer",
    13: "Heart Rate Sensor", 14: "Blood Pressure", 15: "Human Interface Device",
    16: "Glucose Meter", 17: "Running Walking Sensor", 18: "Cycling",
    19: "Control Device", 20: "Network Device", 21: "Sensor",
    22: "Light Fixture", 23: "Fan", 24: "HVAC", 25: "Air Conditioning",
    26: "Humidifier", 27: "Heating", 28: "Access Control", 29: "Motorized Device",
    30: "Power Device", 31: "Light Source", 32: "Window Covering",
    33: "Audio Sink", 34: "Audio Source", 35: "Motorized Vehicle",
    36: "Domestic Appliance", 37: "Wearable Audio Device", 38: "Aircraft",
    39: "AV Equipment", 40: "Display Equipment", 41: "Hearing Aid",
    42: "Gaming", 43: "Signage", 49: "Pulse Oximeter", 50: "Weight Scale",
    51: "Personal Mobility Device", 52: "Continuous Glucose Monitor",
    53: "Insulin Pump", 54: "Medication Delivery", 55: "Spirometer",
    81: "Outdoor Sports Activity",
}

APPEARANCE_SUBCATEGORIES: dict[int, str] = {
    0x0041: "Generic Phone", 0x0081: "Generic Computer", 0x0085: "Laptop",
    0x0086: "Tablet", 0x0087: "Docking Station", 0x0088: "All-in-One",
    0x00C1: "Generic Watch", 0x00C2: "Sports Watch", 0x00C3: "Smartwatch",
    0x0200: "Generic Tag", 0x0240: "Generic Keyring",
    0x03C0: "Generic HID", 0x03C1: "Keyboard", 0x03C2: "Mouse",
    0x03C3: "Joystick", 0x03C4: "Gamepad", 0x03C5: "Digitizer Tablet",
    0x03C6: "Card Reader", 0x03C7: "Digital Pen", 0x03C8: "Barcode Scanner",
    0x0541: "Generic Motion Sensor", 0x0542: "Air Quality Sensor",
    0x0543: "Temperature Sensor", 0x0544: "Humidity Sensor",
    0x0548: "Smoke Sensor", 0x054B: "Occupancy Sensor",
    0x0553: "Camera",
    0x0840: "Generic Audio Sink", 0x0841: "Standalone Speaker",
    0x0880: "Generic Audio Source", 0x0881: "Microphone",
    0x0882: "Alarm", 0x0883: "Bell", 0x0884: "Horn",
    0x0885: "Broadcasting Device", 0x0886: "Service Desk",
    0x0887: "Kiosk", 0x0888: "Broadcasting Room", 0x0889: "Auditorium",
    0x0941: "Wearable Earbud", 0x0942: "Wearable Headset",
    0x0943: "Wearable Headphones", 0x0944: "Wearable Neck Band",
}

# ---------------------------------------------------------------------------
# Class of Device (BR/EDR inquiry results). 24-bit field.
# ---------------------------------------------------------------------------

COD_MAJOR: dict[int, str] = {
    0x00: "Miscellaneous", 0x01: "Computer", 0x02: "Phone",
    0x03: "LAN/Network Access Point", 0x04: "Audio/Video",
    0x05: "Peripheral", 0x06: "Imaging", 0x07: "Wearable",
    0x08: "Toy", 0x09: "Health", 0x1F: "Uncategorized",
}

COD_AV_MINOR: dict[int, str] = {
    0x01: "Wearable Headset", 0x02: "Hands-free", 0x04: "Microphone",
    0x05: "Loudspeaker", 0x06: "Headphones", 0x07: "Portable Audio",
    0x08: "Car Audio", 0x09: "Set-top box", 0x0A: "HiFi Audio",
    0x0B: "VCR", 0x0C: "Video Camera", 0x0D: "Camcorder",
    0x0E: "Video Monitor", 0x0F: "Video Display and Loudspeaker",
    0x10: "Video Conferencing", 0x12: "Gaming/Toy",
}

COD_IMAGING_BITS: dict[int, str] = {
    0x04: "Display", 0x08: "Camera", 0x10: "Scanner", 0x20: "Printer",
}

COD_SERVICE_BITS: dict[int, str] = {
    13: "Limited Discoverable", 16: "Positioning", 17: "Networking",
    18: "Rendering", 19: "Capturing", 20: "Object Transfer",
    21: "Audio", 22: "Telephony", 23: "Information",
}


def company_name(cid: int | None) -> str | None:
    if cid is None:
        return None
    return COMPANY_IDS.get(cid)


def service_name(uuid: str) -> str | None:
    """Accepts 16-bit shorthand ('180f'), or a full 128-bit base UUID."""
    u = uuid.lower().replace("0x", "")
    m = re.fullmatch(r"0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb", u)
    if m:
        u = m.group(1)
    if len(u) == 4:
        try:
            return SERVICE_UUIDS.get(int(u, 16))
        except ValueError:
            return None
    return VENDOR_128BIT.get(u)


# Full 128-bit UUIDs worth naming: vendors that never registered a short one.
VENDOR_128BIT: dict[str, str] = {
    "6e400001-b5a3-f393-e0a9-e50e24dcca9e": "Nordic UART (serial bridge)",
    "0000fee7-0000-1000-8000-00805f9b34fb": "Tencent",
    "d0611e78-bbb4-4591-a5f8-487910ae4366": "Apple Continuity",
    "9fa480e0-4967-4542-9390-d343dc5d04ae": "Apple Notification Center",
    "0000ffe0-0000-1000-8000-00805f9b34fb": "HM-10 / generic serial module",
    "0000ff00-0000-1000-8000-00805f9b34fb": "Generic vendor serial",
    "0000fff0-0000-1000-8000-00805f9b34fb": "Generic vendor control",
    "1bc5d5a5-0200-b89f-e611-45f0d3f1a5ff": "Espressif / ESP32 provisioning",
}


def appearance_name(value: int | None) -> str | None:
    if value is None:
        return None
    exact = APPEARANCE_SUBCATEGORIES.get(value)
    if exact:
        return exact
    return APPEARANCE_CATEGORIES.get(value >> 6)


def decode_cod(cod: int | None) -> dict[str, object]:
    """Split a 24-bit Class of Device into major/minor/services."""
    if cod is None:
        return {}
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    out: dict[str, object] = {"cod_major": COD_MAJOR.get(major, f"0x{major:02x}")}

    if major == 0x04:
        out["cod_minor"] = COD_AV_MINOR.get(minor, f"0x{minor:02x}")
    elif major == 0x06:
        roles = [n for bit, n in COD_IMAGING_BITS.items() if minor & bit]
        if roles:
            out["cod_minor"] = "+".join(roles)
    elif major == 0x01:
        out["cod_minor"] = {
            0x01: "Desktop", 0x02: "Server", 0x03: "Laptop",
            0x04: "Handheld PDA", 0x05: "Palm PDA", 0x06: "Wearable computer",
            0x07: "Tablet",
        }.get(minor, f"0x{minor:02x}")
    elif major == 0x02:
        out["cod_minor"] = {
            0x01: "Cellular", 0x02: "Cordless", 0x03: "Smartphone",
            0x04: "Wired modem", 0x05: "ISDN",
        }.get(minor, f"0x{minor:02x}")
    elif major == 0x05:
        out["cod_minor"] = {
            0x10: "Keyboard", 0x20: "Pointing device", 0x30: "Combo",
        }.get(minor & 0x30, f"0x{minor:02x}")

    services = [n for bit, n in COD_SERVICE_BITS.items() if cod & (1 << bit)]
    if services:
        out["cod_services"] = services
    return out


# ---------------------------------------------------------------------------
# Optional enrichment from host files.
# ---------------------------------------------------------------------------

_EXTERNAL_PATHS = (
    "/usr/share/wireshark/manuf",
    "/usr/share/ieee-data/oui.txt",
    "/usr/share/hwdata/oui.txt",
)


def load_external() -> int:
    """Merge any host-provided vendor table. Returns entries added."""
    from . import oui

    added = 0
    for path in _EXTERNAL_PATHS:
        if os.path.exists(path):
            added += oui.load_file(path)
    return added
