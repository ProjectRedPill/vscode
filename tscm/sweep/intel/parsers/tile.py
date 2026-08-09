"""Tile / Chipolo tracker decoding (services 0xFEED, 0xFEEC; company 0x01DA)."""

from __future__ import annotations

from typing import Any

TILE_CID = 0x01DA
CHIPOLO_CID = 0x0201


def decode_manufacturer(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid == TILE_CID:
        return {
            "vendor": "Tile",
            "device_class": "tracker",
            "tracker_network": "Tile (Life360)",
            "tile_mfr_data": payload.hex(),
        }
    if cid == CHIPOLO_CID:
        return {
            "vendor": "Chipolo",
            "device_class": "tracker",
            "tracker_network": "Chipolo",
            "chipolo_mfr_data": payload.hex(),
        }
    return None


def decode_service_data(uuid: str, payload: bytes) -> dict[str, Any] | None:
    u = uuid.lower()
    if "feed" not in u and "feec" not in u:
        return None

    out: dict[str, Any] = {
        "vendor": "Tile",
        "device_class": "tracker",
        "tracker_network": "Tile (Life360)",
        "tile_payload": payload.hex(),
    }
    if len(payload) >= 1:
        # Tile IDs are static per device — unlike Apple/Google, a Tile can be
        # followed indefinitely once seen, which cuts both ways.
        out["tile_id"] = payload[:8].hex() if len(payload) >= 8 else payload.hex()
        out["note"] = (
            "Tile identifiers are static; this device is trivially re-identifiable "
            "across sessions"
        )
    return out
