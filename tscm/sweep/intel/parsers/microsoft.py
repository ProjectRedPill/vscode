"""Microsoft CDP (Connected Devices Platform) beacon, company ID 0x0006.

Windows, Xbox and the Your Phone app broadcast this constantly. The device-type
byte is one of the few places on BLE where a device states its own form factor
in plain text, so it is disproportionately useful for inventorying a room.
"""

from __future__ import annotations

from typing import Any

MS_CID = 0x0006

DEVICE_TYPES = {
    1: "Xbox One", 6: "Apple iPhone", 7: "Apple iPad", 8: "Android device",
    9: "Windows 10 Desktop", 11: "Windows 10 Phone", 12: "Linux device",
    13: "Windows IoT", 14: "Surface Hub", 15: "Windows laptop",
    16: "Windows tablet", 31: "Xbox Series X/S",
}

DEVICE_CLASS = {
    1: "appliance", 6: "phone", 7: "computer", 8: "phone",
    9: "computer", 11: "phone", 12: "computer", 13: "appliance",
    14: "display", 15: "computer", 16: "computer", 31: "appliance",
}

SCENARIOS = {0x01: "Bluetooth advertisement (CDP)"}


def decode(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid != MS_CID or len(payload) < 2:
        return None

    out: dict[str, Any] = {"vendor": "Microsoft", "ms_cdp": True}
    scenario = payload[0]
    out["ms_scenario"] = SCENARIOS.get(scenario, f"0x{scenario:02x}")

    if scenario != 0x01 or len(payload) < 3:
        return out

    # Byte 1: version (high nibble) + device type (low 5 bits).
    dtype = payload[1] & 0x1F
    out["ms_device_type_id"] = dtype
    name = DEVICE_TYPES.get(dtype)
    if name:
        out["ms_device_type"] = name
        out["model"] = name
    cls = DEVICE_CLASS.get(dtype)
    if cls:
        out["device_class"] = cls
        if cls == "computer" and dtype in (9, 15, 16):
            out["os_hint"] = "Windows"
        elif dtype == 12:
            out["os_hint"] = "Linux"
        elif dtype in (6, 7):
            out["os_hint"] = "iOS/iPadOS"
        elif dtype == 8:
            out["os_hint"] = "Android"

    flags = payload[2]
    out["ms_share_nearby"] = bool(flags & 0x01)
    out["ms_salt"] = payload[3:7].hex() if len(payload) >= 7 else None
    # Bytes 7.. are a SHA-256-derived hash of the account + salt: it rotates,
    # so it cannot identify a user, but it *is* stable within a rotation and
    # therefore links MAC changes together for a few minutes.
    if len(payload) > 7:
        out["ms_device_hash"] = payload[7:].hex()

    return {k: v for k, v in out.items() if v is not None}


def decode_swift_pair(uuid: str, payload: bytes) -> dict[str, Any] | None:
    """Swift Pair (service 0xFE07) carries a plaintext display name."""
    if "fe07" not in uuid.lower() or len(payload) < 3:
        return None
    out: dict[str, Any] = {"ms_swift_pair": True, "vendor": "Microsoft (Swift Pair)"}
    try:
        name = payload[3:].decode("utf-8", "replace").strip("\x00")
        if name:
            out["name"] = name
    except Exception:
        pass
    return out
