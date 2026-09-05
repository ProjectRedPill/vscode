"""Standard beacon formats: iBeacon, Eddystone, AltBeacon.

Beacons are worth decoding for a sweep because a beacon in a private space is
almost never innocent — it is either retail analytics infrastructure that
should not be there, or someone's proximity-logging rig.
"""

from __future__ import annotations

import struct
from typing import Any

APPLE_CID = 0x004C
ALTBEACON_PREFIX = b"\xbe\xac"


def decode_ibeacon(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid == APPLE_CID and len(payload) >= 23 and payload[0] == 0x02 and payload[1] == 0x15:
        uuid_bytes = payload[2:18]
        major, minor, power = struct.unpack(">HHb", payload[18:23])
        return {
            "beacon_type": "iBeacon",
            "device_class": "beacon",
            "ibeacon_uuid": _fmt_uuid(uuid_bytes),
            "ibeacon_major": major,
            "ibeacon_minor": minor,
            "tx_power": power,
            "note": "fixed-position proximity beacon",
        }

    if len(payload) >= 24 and payload[:2] == ALTBEACON_PREFIX:
        return {
            "beacon_type": "AltBeacon",
            "device_class": "beacon",
            "altbeacon_id": payload[2:22].hex(),
            "tx_power": struct.unpack("b", payload[22:23])[0],
        }
    return None


def decode_eddystone(uuid: str, payload: bytes) -> dict[str, Any] | None:
    if "feaa" not in uuid.lower() or not payload:
        return None

    frame = payload[0]
    out: dict[str, Any] = {"beacon_type": "Eddystone", "device_class": "beacon"}

    if frame == 0x00 and len(payload) >= 18:
        out["eddystone_frame"] = "UID"
        out["tx_power"] = struct.unpack("b", payload[1:2])[0]
        out["eddystone_namespace"] = payload[2:12].hex()
        out["eddystone_instance"] = payload[12:18].hex()
    elif frame == 0x10 and len(payload) >= 3:
        out["eddystone_frame"] = "URL"
        out["tx_power"] = struct.unpack("b", payload[1:2])[0]
        url = _eddystone_url(payload[2:])
        out["eddystone_url"] = url
        out["note"] = f"beacon is broadcasting a URL: {url}"
    elif frame == 0x20 and len(payload) >= 14:
        out["eddystone_frame"] = "TLM (telemetry)"
        volt, temp, adv_cnt, uptime = struct.unpack(">HhII", payload[2:14])
        out["eddystone_battery_mv"] = volt
        out["eddystone_temp_c"] = round(temp / 256.0, 1)
        out["eddystone_adv_count"] = adv_cnt
        out["eddystone_uptime_s"] = uptime // 10
        out["note"] = (
            f"beacon has been powered on for {uptime // 36000:.0f}h — "
            "long uptime in a private space suggests permanent installation"
        )
    elif frame == 0x30:
        out["eddystone_frame"] = "EID (ephemeral, rotating)"
        out["eddystone_eid"] = payload[2:10].hex()
    return out


_URL_SCHEMES = ["http://www.", "https://www.", "http://", "https://"]
_URL_EXPANSIONS = {
    0x00: ".com/", 0x01: ".org/", 0x02: ".edu/", 0x03: ".net/",
    0x04: ".info/", 0x05: ".biz/", 0x06: ".gov/", 0x07: ".com",
    0x08: ".org", 0x09: ".edu", 0x0A: ".net", 0x0B: ".info",
    0x0C: ".biz", 0x0D: ".gov",
}


def _eddystone_url(data: bytes) -> str:
    if not data:
        return ""
    out = _URL_SCHEMES[data[0]] if data[0] < len(_URL_SCHEMES) else ""
    for byte in data[1:]:
        out += _URL_EXPANSIONS.get(byte, chr(byte) if 0x20 <= byte < 0x7F else "")
    return out


def _fmt_uuid(b: bytes) -> str:
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
