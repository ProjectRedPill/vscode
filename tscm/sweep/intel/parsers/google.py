"""Google Fast Pair (0xFE2C) and Find My Device network (0xFEAA/0xFCF1).

Fast Pair has two modes. Discoverable adverts carry a plaintext 24-bit model ID
that maps to an exact product in Google's registry — the single most precise
identification available on BLE. Non-discoverable adverts carry only a rotating
account-key filter, which identifies *an account*, not a model.
"""

from __future__ import annotations

from typing import Any

GOOGLE_CID = 0x00E0

# A working subset of the Fast Pair model registry. The full registry is an
# online lookup; these are the IDs common enough to matter offline.
FAST_PAIR_MODELS = {
    0x000107: "Google Pixel Buds", 0x00000C: "Google Pixel Buds A",
    0x821F66: "Pixel Buds Pro", 0x0002F0: "Google Pixel Stand",
    0x00B727: "Sony WH-1000XM4", 0x0000F0: "Sony WF-1000XM3",
    0x02AA91: "Sony WH-1000XM5", 0x9601F5: "JBL Live Pro",
    0x718FA4: "Bose QuietComfort", 0x0001F0: "Bose 700",
    0x2D7A23: "Samsung Galaxy Buds", 0xF52494: "Samsung Galaxy Buds2",
    0x00D0F1: "Anker Soundcore", 0x9F1CE2: "Jabra Elite",
    0x8B77C7: "Chipolo One Point (Find My Device tracker)",
    0x0DC7F8: "Pebblebee Tag (Find My Device tracker)",
    0x8E17A4: "Moto Tag (Find My Device tracker)",
}


def decode_manufacturer(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid != GOOGLE_CID:
        return None
    return {"vendor": "Google", "google_mfr_data": payload.hex()}


def decode_service_data(uuid: str, payload: bytes) -> dict[str, Any] | None:
    u = uuid.lower()
    if "fe2c" in u:
        return _fast_pair(payload)
    if "fcf1" in u:
        return _find_my_device(payload)
    if "fe9f" in u:
        return {"vendor": "Google", "google_service": "Google LE (misc)"}
    return None


def _fast_pair(payload: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"vendor": "Google", "fast_pair": True}

    if len(payload) == 3:
        model = int.from_bytes(payload, "big")
        out["fast_pair_mode"] = "discoverable (pairing)"
        out["fast_pair_model_id"] = f"0x{model:06X}"
        name = FAST_PAIR_MODELS.get(model)
        if name:
            out["model"] = name
            out["device_class"] = (
                "tracker" if "tracker" in name.lower() else "audio"
            )
        else:
            out["device_class"] = "audio"
        out["note"] = "device is in pairing mode and broadcasting its exact model"
        return out

    if len(payload) >= 1:
        version = payload[0] >> 4
        out["fast_pair_mode"] = "non-discoverable (account key filter)"
        out["fast_pair_version"] = version
        out["fast_pair_filter"] = payload[1:].hex()
        # Bits in the flags byte signal an active unwanted-tracking state.
        if len(payload) >= 2 and payload[1] & 0x02:
            out["threat_note"] = "Fast Pair accessory reporting separated state"
    return out


def _find_my_device(payload: bytes) -> dict[str, Any]:
    """Google's crowd-sourced finding network — the Android AirTag equivalent."""
    out: dict[str, Any] = {
        "vendor": "Google",
        "device_class": "tracker",
        "tracker_network": "Google Find My Device",
        "fmdn_payload": payload.hex(),
    }
    if payload:
        state = payload[0] & 0x03
        out["fmdn_state"] = (
            "with owner", "separated from owner", "unwanted-tracking mode", "unknown"
        )[state]
        out["find_my_separated"] = state == 1
        if state == 1:
            out["threat_note"] = (
                "separated Find My Device tracker — check whether it follows you"
            )
    return out
