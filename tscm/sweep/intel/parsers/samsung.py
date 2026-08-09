"""Samsung SmartThings Find / SmartTag decoding (company 0x0075, service 0xFD5A).

SmartTags are the third major consumer tracker network after Apple and Google,
and the one least covered by phone-native "unknown tracker" alerts on non-Samsung
handsets — which makes them the most likely to be used and the most useful to
detect independently.
"""

from __future__ import annotations

from typing import Any

SAMSUNG_CID = 0x0075


def decode(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid != SAMSUNG_CID or not payload:
        return None

    out: dict[str, Any] = {"vendor": "Samsung", "samsung_mfr_data": payload.hex()}

    # Samsung uses a leading type byte across its BLE products.
    kind = payload[0]
    if kind == 0x42 and len(payload) >= 4:
        out["samsung_service"] = "SmartThings / Galaxy quick-connect"
        out["device_class"] = "phone"
    elif kind == 0x01:
        out["samsung_service"] = "Galaxy Watch"
        out["device_class"] = "wearable"
    elif kind in (0x02, 0x03):
        out["samsung_service"] = "Galaxy Buds"
        out["device_class"] = "audio"
    return out


def decode_service_data(uuid: str, payload: bytes) -> dict[str, Any] | None:
    u = uuid.lower()
    if "fd5a" not in u:
        return None

    out: dict[str, Any] = {
        "vendor": "Samsung",
        "device_class": "tracker",
        "tracker_network": "Samsung SmartThings Find",
        "smarttag_payload": payload.hex(),
    }
    if not payload:
        return out

    # Byte 0 low bits carry the region/registration state; the "overmature
    # offline" states are what a tag broadcasts once it is far from its owner.
    state = payload[0] & 0x03
    out["smarttag_state"] = (
        "premature offline (just separated)",
        "overmature offline (long separated)",
        "with owner",
        "unknown",
    )[state]
    if state in (0, 1):
        out["find_my_separated"] = True
        out["threat_note"] = (
            "Samsung SmartTag separated from its owner — Samsung tags are not "
            "surfaced by iOS or stock Android tracker alerts"
        )
    if len(payload) >= 2:
        out["smarttag_privacy_id"] = payload[1:].hex()
    return out
