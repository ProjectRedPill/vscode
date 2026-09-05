"""MAC address intelligence.

Two jobs:

  1. Vendor from OUI. A bundled table covers the vendors that matter for a
     sweep (phones, cameras, APs, trackers); `load_file()` merges the full IEEE
     registry when the host happens to have one.

  2. Address *type*. This is the part most scanners skip and it is the most
     operationally useful bit on the whole packet — a static public MAC can be
     followed forever, a resolvable-private one rotates every ~15 minutes and
     has to be re-identified by other means. Knowing which you are looking at
     tells you whether "I keep seeing this device" is even a meaningful claim.
"""

from __future__ import annotations

import re
from enum import Enum

_OUI: dict[str, str] = {}

# Bundled seed table. Keys are the first 3 octets, uppercase, no separators.
_SEED = {
    # Apple (a small slice of ~800 Apple OUIs; enough to catch most gear)
    "3C0754": "Apple", "A85C2C": "Apple", "F0989D": "Apple", "AC1F74": "Apple",
    "DC2B2A": "Apple", "E0B52D": "Apple", "84FCFE": "Apple", "8866A5": "Apple",
    "9C207B": "Apple", "F82793": "Apple", "0C3021": "Apple", "6C4008": "Apple",
    # Samsung
    "0012FB": "Samsung", "5CF6DC": "Samsung", "8425DB": "Samsung",
    "E8508B": "Samsung", "C81EE7": "Samsung", "1C232C": "Samsung",
    # Google / Nest
    "3C5AB4": "Google", "F4F5D8": "Google", "1CF29A": "Google",
    "6466B3": "Google Nest", "18B430": "Nest Labs", "641666": "Nest Labs",
    # Amazon / Ring / Blink
    "44650D": "Amazon", "68544C": "Amazon", "FC65DE": "Amazon",
    "0C47C9": "Amazon", "B47C9C": "Amazon", "9C8ECD": "Amazon (Ring)",
    "F0038C": "Amazon (Blink)",
    # Cameras and DVRs — high value for a sweep
    "00408C": "Axis Communications", "ACCC8E": "Axis Communications",
    "B8A44F": "Axis Communications", "001C27": "Hikvision",
    "4419B6": "Hikvision", "BCAD28": "Hikvision", "C0560E": "Hikvision",
    "28571C": "Dahua", "3CEF8C": "Dahua", "9C144D": "Dahua",
    "001344": "Vivotek", "0002D1": "Vivotek", "00126D": "Mobotix",
    "7CDD90": "Shenzhen Ogemray (IP camera modules)",
    "A4DA22": "Wyze Labs", "2CAA8E": "Wyze Labs",
    "8CEB2E": "Eufy / Anker", "E85A8B": "Anker",
    "B0C554": "D-Link (cameras)", "C4E90A": "D-Link",
    "0C8C24": "Reolink", "EC71DB": "Reolink",
    "3480B3": "Xiaomi", "78110F": "Xiaomi", "64B473": "Xiaomi",
    "9C9D7E": "Tuya (white-label IoT/cameras)",
    "D4A651": "Tuya", "68572D": "Tuya",
    "3C6A2C": "Espressif (ESP32 — common in DIY bugs)",
    "246F28": "Espressif", "8CAAB5": "Espressif", "A0764E": "Espressif",
    "24D7EB": "Espressif", "7CDFA1": "Espressif", "C8C9A3": "Espressif",
    "B4E62D": "Espressif", "2462AB": "Espressif",
    # Network gear
    "002606": "Ubiquiti", "24A43C": "Ubiquiti", "788A20": "Ubiquiti",
    "F09FC2": "Ubiquiti", "0018E7": "Cameo/Netgear", "A00460": "Netgear",
    "9C3DCF": "Netgear", "B0B98A": "Netgear", "C40415": "TP-Link",
    "50C7BF": "TP-Link", "9C5322": "TP-Link", "AC84C6": "TP-Link",
    "001A2B": "Cisco", "00259C": "Cisco-Linksys", "E0CB4E": "ASUSTek",
    "2C4D54": "ASUSTek", "708BCD": "ASUSTek", "60A4B7": "ASUSTek",
    "F832E4": "ASUSTek", "3C7C3F": "ASUSTek",
    # Phones / chipsets
    "10D07A": "AMPAK (ESP/RTL modules)", "60019E": "Sony",
    "0035FF": "Texas Instruments", "1CBA8C": "Texas Instruments",
    "D0B5C2": "Texas Instruments", "F4B85E": "Texas Instruments",
    "C0EE40": "Laird / Nordic modules", "E4E112": "Nordic Semiconductor",
    "F49F54": "Nordic Semiconductor",
    "001BDC": "Vencer / GPS trackers", "0080E1": "STMicroelectronics",
    "88DA1A": "Redpine / Silicon Labs", "0CAE7D": "Texas Instruments",
    "48DA35": "Tile", "F8E079": "Motorola", "3C286D": "Motorola",
    "001A11": "Google (legacy)", "7C2EBD": "Google",
    "0018DE": "Intel", "34F39A": "Intel", "3C58C2": "Intel",
    "5CC5D4": "Intel", "7C7A91": "Intel", "A0A8CD": "Intel",
    # Vehicle / GPS trackers commonly repurposed for covert tracking
    "0C1105": "Concox (GPS tracker)", "00D0C9": "Advantech",
    "3413A8": "Queclink (GPS tracker)", "1C1BB5": "Teltonika",
}
_OUI.update(_SEED)

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?)(?:[0-9A-Fa-f]{2}\1?){4}[0-9A-Fa-f]{2}$")
_LINE_RE = re.compile(
    r"^\s*([0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2})\s+(?:\S+\s+)?(.+?)\s*$"
)


class AddrType(str, Enum):
    PUBLIC = "public"                    # globally unique, IEEE-assigned, permanent
    RANDOM_STATIC = "random-static"      # random but stable until reboot
    RESOLVABLE_PRIVATE = "resolvable"    # rotates; resolvable only with the IRK
    NON_RESOLVABLE = "non-resolvable"    # rotates; unlinkable by design
    LOCALLY_ADMIN = "locally-administered"  # software-set (Wi-Fi MAC randomisation)
    UNKNOWN = "unknown"

    @property
    def is_rotating(self) -> bool:
        return self in (
            AddrType.RESOLVABLE_PRIVATE,
            AddrType.NON_RESOLVABLE,
            AddrType.LOCALLY_ADMIN,
        )

    @property
    def explanation(self) -> str:
        return {
            AddrType.PUBLIC: "permanent hardware address — trackable across sessions",
            AddrType.RANDOM_STATIC: "stable until the device reboots",
            AddrType.RESOLVABLE_PRIVATE: "rotates ~every 15 min; identity hidden without pairing key",
            AddrType.NON_RESOLVABLE: "rotates and is deliberately unlinkable",
            AddrType.LOCALLY_ADMIN: "software-assigned (Wi-Fi MAC randomisation)",
            AddrType.UNKNOWN: "address type could not be determined",
        }[self]


def normalize(mac: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", mac).upper()


def is_mac(value: str) -> bool:
    return bool(_MAC_RE.match(value.strip()))


def vendor(mac: str, stack_type: str | None = None) -> str | None:
    """Vendor for a MAC, or None. Rotating addresses have no real vendor."""
    n = normalize(mac)
    if len(n) < 6:
        return None
    if classify(mac, stack_type).is_rotating:
        return None
    return _OUI.get(n[:6])


def classify(mac: str, stack_type: str | None = None) -> AddrType:
    """Address type, from the stack's own report where we have it.

    This cannot be done reliably from the address alone, and pretending
    otherwise produces confident wrong answers. BLE random addresses encode a
    sub-type in bits 7:6 of the MSB (11 static, 01 resolvable, 00 non-resolvable)
    — but those same bit patterns occur in perfectly ordinary public OUIs, so
    reading them without knowing the address is random misclassifies real
    hardware. Samsung's C8:1E:E7 prefix has top bits 11 and is entirely public.

    So: if the Bluetooth stack told us public-or-random (BlueZ `AddressType`,
    CoreBluetooth, WinRT all do), that answer is authoritative and the sub-type
    bits refine it. Without that hint we fall back to the IEEE
    locally-administered bit, which is the only self-describing thing an address
    carries, and report PUBLIC otherwise.
    """
    n = normalize(mac)
    if len(n) < 12:
        return AddrType.UNKNOWN
    first = int(n[:2], 16)
    top2 = first >> 6
    locally_admin = bool(first & 0b10)

    hint = (stack_type or "").lower()
    if hint.startswith("public"):
        return AddrType.PUBLIC
    is_random = hint.startswith("random") or locally_admin

    if not is_random:
        return AddrType.PUBLIC
    if top2 == 0b11:
        return AddrType.RANDOM_STATIC
    if top2 == 0b01:
        return AddrType.RESOLVABLE_PRIVATE
    if top2 == 0b00:
        return AddrType.NON_RESOLVABLE
    return AddrType.LOCALLY_ADMIN


def describe(mac: str, stack_type: str | None = None) -> dict[str, object]:
    """Everything derivable from the address alone."""
    t = classify(mac, stack_type)
    out: dict[str, object] = {
        "mac": normalize(mac),
        "addr_type": t.value,
        "addr_note": t.explanation,
        "rotating": t.is_rotating,
    }
    if stack_type:
        out["addr_type_source"] = "bluetooth stack"
    v = vendor(mac, stack_type)
    if v:
        out["vendor"] = v
        out["oui"] = normalize(mac)[:6]
    n = normalize(mac)
    if len(n) >= 2 and int(n[:2], 16) & 0b1:
        out["multicast"] = True
    return out


def load_file(path: str) -> int:
    """Merge an IEEE OUI / Wireshark manuf file. Returns entries added."""
    added = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                m = _LINE_RE.match(line)
                if not m:
                    continue
                prefix = normalize(m.group(1))
                name = m.group(2).split("#")[0].strip()
                if len(prefix) == 6 and name and prefix not in _OUI:
                    _OUI[prefix] = name
                    added += 1
    except OSError:
        return 0
    return added


def table_size() -> int:
    return len(_OUI)
