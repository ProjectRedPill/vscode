"""Apple Continuity and Find My decoding (company ID 0x004C).

Apple packs several independent messages into one manufacturer-data blob, each
a (type, length, payload) triple. Two are worth real effort:

  0x12 Find My / offline finding — this is what an AirTag left in your bag
       broadcasts. The 25-byte form means *separated from its owner*, which is
       exactly the state a stalking tracker is in.
  0x10 Nearby Info — leaks device state (screen on, wifi status, activity) from
       any nearby iPhone without pairing. Useful for telling a live handset
       from a dormant one.

Everything here is passive decoding of what devices already shout in the clear.
"""

from __future__ import annotations

from typing import Any

APPLE_CID = 0x004C

CONTINUITY_TYPES = {
    0x01: "iBeacon-legacy",
    0x02: "iBeacon",
    0x03: "AirPrint",
    0x05: "AirDrop",
    0x06: "HomeKit",
    0x07: "Proximity Pairing",
    0x08: "Hey Siri",
    0x09: "AirPlay target",
    0x0A: "AirPlay source",
    0x0B: "Watch connection",
    0x0C: "Handoff",
    0x0D: "Tethering target",
    0x0E: "Tethering source",
    0x0F: "Nearby Action",
    0x10: "Nearby Info",
    0x12: "Find My",
}

# Proximity-pairing model IDs, in the byte order they appear on the wire
# (payload[1:3] read big-endian) — e.g. AirPods Pro transmits `.. 0E 20 ..`.
AIRPODS_MODELS = {
    0x0220: "AirPods (1st gen)", 0x0F20: "AirPods (2nd gen)",
    0x1320: "AirPods (3rd gen)", 0x1920: "AirPods (4th gen)",
    0x0E20: "AirPods Pro", 0x1420: "AirPods Pro (2nd gen)",
    0x2420: "AirPods Pro (2nd gen, USB-C)",
    0x0A20: "AirPods Max", 0x1F20: "AirPods Max (USB-C)",
    0x0320: "Powerbeats 3", 0x0B20: "Powerbeats Pro",
    0x0D20: "Powerbeats 4", 0x0520: "BeatsX",
    0x0620: "Beats Solo 3", 0x0920: "Beats Studio 3",
    0x1020: "Beats Flex", 0x1120: "Beats Studio Buds",
    0x1720: "Beats Fit Pro", 0x1220: "Beats Solo Pro",
}

NEARBY_ACTION_TYPES = {
    0x01: "Apple TV setup", 0x04: "Mobile backup", 0x05: "Watch setup",
    0x06: "Apple TV pair", 0x07: "Internet relay", 0x08: "WiFi password share",
    0x09: "iOS setup", 0x0A: "Repair", 0x0B: "Speaker setup",
    0x0C: "Apple pay", 0x0D: "Whole-home audio setup", 0x0E: "Developer tools pairing",
    0x0F: "Answered call", 0x10: "Ended call", 0x11: "DD ping",
    0x13: "Companion link proximity", 0x14: "Custody handoff",
    0x17: "Setup new phone", 0x18: "Transfer number", 0x1B: "Unknown-tracker-alert",
    0x1E: "Proximity pairing", 0x20: "Shared Audio",
}

# Nearby Info status byte, low nibble = activity level.
NEARBY_ACTIVITY = {
    0x00: "idle / locked", 0x01: "activity level unknown",
    0x03: "idle user", 0x05: "audio playing, screen off",
    0x07: "active user (screen on)", 0x09: "screen on, video playing",
    0x0A: "watch on wrist, unlocked", 0x0B: "recent user interaction",
    0x0D: "user driving a vehicle", 0x0E: "phone call or FaceTime active",
}

DEVICE_CLASS_FROM_TYPE = {
    0x07: "audio", 0x0B: "wearable", 0x12: "tracker",
}


def decode(cid: int, payload: bytes) -> dict[str, Any] | None:
    if cid != APPLE_CID or len(payload) < 2:
        return None

    out: dict[str, Any] = {"vendor": "Apple", "os_hint": "Apple ecosystem"}
    messages: list[str] = []

    i = 0
    while i + 1 < len(payload):
        mtype = payload[i]
        mlen = payload[i + 1]
        body = payload[i + 2 : i + 2 + mlen]
        i += 2 + mlen
        if mlen == 0 and mtype == 0:
            break

        label = CONTINUITY_TYPES.get(mtype, f"unknown-0x{mtype:02x}")
        messages.append(label)

        if mtype == 0x12:
            out.update(_find_my(body, mlen))
        elif mtype == 0x07:
            out.update(_proximity_pairing(body))
        elif mtype == 0x10:
            out.update(_nearby_info(body))
        elif mtype == 0x0F:
            out.update(_nearby_action(body))
        elif mtype == 0x0C and len(body) >= 2:
            out["apple_handoff"] = True
            out["apple_handoff_iv"] = int.from_bytes(body[1:3], "little")
        elif mtype in (0x0D, 0x0E):
            out["apple_hotspot"] = label
            if mtype == 0x0E and len(body) >= 3:
                out["apple_hotspot_battery_pct"] = body[1]
                out["apple_hotspot_cell_bars"] = body[2] & 0x0F
        elif mtype == 0x05:
            out["apple_airdrop_active"] = True
        elif mtype == 0x08:
            out["apple_hey_siri"] = True
            if len(body) >= 6:
                out["apple_siri_device_class"] = f"0x{body[4]:02x}"
        elif mtype == 0x0B and len(body) >= 1:
            out["apple_watch_paired"] = True

    if messages:
        out["apple_messages"] = messages
    return out


def _find_my(body: bytes, mlen: int) -> dict[str, Any]:
    """Offline-finding advert. Length is the tell for owner separation."""
    out: dict[str, Any] = {
        "find_my": True,
        "device_class": "tracker",
        "tracker_network": "Apple Find My",
    }
    if mlen >= 0x19 and len(body) >= 24:
        # Full public-key broadcast: the accessory has lost contact with its
        # owner and is soliciting help from any passing iPhone.
        out["find_my_state"] = "separated from owner"
        out["find_my_separated"] = True
        out["find_my_pubkey"] = body[1:23].hex()
        out["find_my_status_byte"] = f"0x{body[0]:02x}"
        # Bits 6-7 of the status byte carry a coarse battery level.
        out["find_my_battery"] = ("full", "medium", "low", "critically low")[
            (body[0] >> 6) & 0x03
        ]
        out["threat_note"] = (
            "separated Find My accessory — if this follows you across locations "
            "it may be an unwanted tracker"
        )
    elif len(body) >= 2:
        out["find_my_state"] = "with owner / paired nearby"
        out["find_my_separated"] = False
        out["find_my_status_byte"] = f"0x{body[0]:02x}"
        out["find_my_hint"] = body[1:].hex()
    return out


def _proximity_pairing(body: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"device_class": "audio"}
    if len(body) >= 3:
        model = int.from_bytes(body[1:3], "big")
        out["apple_model_id"] = f"0x{model:04X}"
        name = AIRPODS_MODELS.get(model)
        if name:
            out["model"] = name
    # Layout after the type/length header:
    #   [0] prefix  [1:3] model  [3] status  [4] battery  [5] charge+case  [6] lid
    if len(body) >= 4:
        out["airpods_lid_open"] = bool(body[3] & 0x08)
    if len(body) >= 5:
        batt = body[4]
        # Each nibble is battery in tens of percent; 0x0F means "not reported"
        # (bud in the case, or a single-battery product).
        left, right = (batt >> 4) & 0x0F, batt & 0x0F
        if left != 0x0F:
            out["airpods_battery_left_pct"] = left * 10
        if right != 0x0F:
            out["airpods_battery_right_pct"] = right * 10
    if len(body) >= 6:
        case = body[5] & 0x0F
        if case != 0x0F:
            out["airpods_battery_case_pct"] = case * 10
        out["airpods_charging"] = bool(body[5] & 0xF0)
    return out


def _nearby_info(body: bytes) -> dict[str, Any]:
    """Status flags every modern iPhone/Mac emits continuously."""
    out: dict[str, Any] = {"device_class": "phone"}
    if not body:
        return out
    status = body[0]
    out["apple_activity"] = NEARBY_ACTIVITY.get(
        status & 0x0F, f"code 0x{status & 0x0F:x}"
    )
    out["apple_screen_on"] = bool(status & 0x0F in (0x07, 0x09, 0x0B, 0x0E))
    if len(body) >= 2:
        flags = body[1]
        out["apple_wifi_on"] = bool(flags & 0x40)
        out["apple_authenticated"] = bool(flags & 0x04)
        out["apple_airdrop_rx_on"] = bool(flags & 0x01)
        out["apple_airpods_connected"] = bool(flags & 0x02)
        out["apple_primary_device"] = bool(flags & 0x20)
    return out


def _nearby_action(body: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if len(body) >= 2:
        action = body[1]
        out["apple_nearby_action"] = NEARBY_ACTION_TYPES.get(
            action, f"0x{action:02x}"
        )
        if action == 0x1B:
            out["threat_note"] = "device is itself reporting an unknown-tracker alert"
    return out


def decode_service_data(uuid: str, payload: bytes) -> dict[str, Any] | None:
    u = uuid.lower()
    if "fd44" in u:
        return {
            "find_my": True,
            "device_class": "tracker",
            "tracker_network": "Apple Find My (service data)",
            "find_my_payload": payload.hex(),
        }
    return None
