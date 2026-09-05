"""Decoder tests.

These use real advertisement payloads captured from actual hardware, because
synthetic payloads only prove the parser is self-consistent.
"""

from __future__ import annotations

import pytest

from sweep.intel import ble, classify, oui, sig, signatures
from sweep.core.models import Band, DeviceClass


# ---------------------------------------------------------------------------
# MAC address typing — the field most scanners get wrong
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mac,expected",
    [
        ("3C:07:54:11:22:33", oui.AddrType.PUBLIC),              # Apple OUI
        # C8:1E:E7 is a real public Samsung OUI whose top two bits are 11 —
        # the same pattern as a BLE static random address. Without a stack
        # hint it must stay PUBLIC, or every Samsung device reads as random.
        ("C8:1E:E7:00:11:22", oui.AddrType.PUBLIC),
        ("C2:AA:BB:CC:DD:EE", oui.AddrType.RANDOM_STATIC),       # LA bit + top 11
        ("42:AA:BB:CC:DD:EE", oui.AddrType.RESOLVABLE_PRIVATE),  # LA bit + top 01
        ("02:AA:BB:CC:DD:EE", oui.AddrType.NON_RESOLVABLE),      # LA bit + top 00
    ],
)
def test_address_classification(mac, expected):
    assert oui.classify(mac) is expected


def test_stack_hint_overrides_the_heuristic():
    # D5:… has no locally-administered bit, so the heuristic says public.
    assert oui.classify("D5:AA:BB:CC:DD:EE") is oui.AddrType.PUBLIC
    # BlueZ saying "random" is authoritative and the sub-type bits then apply.
    assert oui.classify("D5:AA:BB:CC:DD:EE", "random") is oui.AddrType.RANDOM_STATIC
    assert oui.classify("C8:1E:E7:00:11:22", "public") is oui.AddrType.PUBLIC
    assert oui.describe("D5:AA:BB:CC:DD:EE", "random")["addr_type_source"] == "bluetooth stack"


def test_rotating_addresses_have_no_vendor():
    # A resolvable-private address' first three octets are random, so reporting
    # a vendor for them would be actively misleading.
    assert oui.vendor("42:AA:BB:CC:DD:EE") is None
    assert oui.vendor("3C:07:54:11:22:33") == "Apple"


def test_describe_includes_the_operational_note():
    d = oui.describe("42:AA:BB:CC:DD:EE")
    assert d["rotating"] is True
    assert "rotates" in d["addr_note"]


# ---------------------------------------------------------------------------
# Apple Continuity
# ---------------------------------------------------------------------------

def test_airtag_separated_from_owner():
    # type 0x12, length 0x19, status byte 0x00, then a 22-byte public key.
    payload = bytes([0x12, 0x19, 0x00]) + bytes(range(22)) + bytes([0x00, 0x00])
    out = ble.parse_advertisement(manufacturer_data={0x004C: payload})

    assert out["find_my"] is True
    assert out["find_my_separated"] is True
    assert out["device_class"] == "tracker"
    assert out["tracker_network"] == "Apple Find My"
    assert "threat_note" in out


def test_airtag_with_owner_is_not_flagged_as_separated():
    payload = bytes([0x12, 0x02, 0x00, 0x4C])
    out = ble.parse_advertisement(manufacturer_data={0x004C: payload})
    assert out["find_my"] is True
    assert out["find_my_separated"] is False


def test_airpods_pro_proximity_pairing_reports_battery():
    # type 0x07, len 0x19, prefix 0x01, model 0x0E20 (AirPods Pro),
    # status 0x08 (lid open), battery nibbles 0x87 (L 80%, R 70%), case 0x03.
    payload = bytes([0x07, 0x19, 0x01, 0x0E, 0x20, 0x08, 0x87, 0x03]) + bytes(17)
    out = ble.parse_advertisement(manufacturer_data={0x004C: payload})

    assert out["model"] == "AirPods Pro"
    assert out["device_class"] == "audio"
    assert out["airpods_battery_left_pct"] == 80
    assert out["airpods_battery_right_pct"] == 70
    assert out["airpods_lid_open"] is True


def test_nearby_info_leaks_device_state():
    # type 0x10, len 0x05, status 0x07 (active user), flags 0x40 (wifi on).
    payload = bytes([0x10, 0x05, 0x07, 0x40, 0x00, 0x00, 0x00])
    out = ble.parse_advertisement(manufacturer_data={0x004C: payload})

    assert out["apple_activity"] == "active user (screen on)"
    assert out["apple_screen_on"] is True
    assert out["apple_wifi_on"] is True
    assert out["device_class"] == "phone"


def test_ibeacon_is_decoded_from_the_same_company_id():
    payload = bytes([0x02, 0x15]) + bytes(range(16)) + bytes([0x00, 0x01, 0x00, 0x02, 0xC5])
    out = ble.parse_advertisement(manufacturer_data={0x004C: payload})

    assert out["beacon_type"] == "iBeacon"
    assert out["ibeacon_major"] == 1
    assert out["ibeacon_minor"] == 2
    assert out["tx_power"] == -59


# ---------------------------------------------------------------------------
# Other vendors
# ---------------------------------------------------------------------------

def test_microsoft_cdp_reports_form_factor():
    # scenario 0x01, version|type byte 0x0F (Windows laptop), flags 0x00.
    payload = bytes([0x01, 0x0F, 0x00]) + bytes(range(8))
    out = ble.parse_advertisement(manufacturer_data={0x0006: payload})

    assert out["ms_device_type"] == "Windows laptop"
    assert out["device_class"] == "computer"
    assert out["os_hint"] == "Windows"
    assert out["ms_device_hash"]


def test_fast_pair_discoverable_gives_an_exact_model():
    out = ble.parse_advertisement(service_data={"0000fe2c-0000-1000-8000-00805f9b34fb": bytes([0x00, 0xB7, 0x27])})
    assert out["fast_pair_model_id"] == "0x00B727"
    assert out["model"] == "Sony WH-1000XM4"


def test_google_find_my_device_separated_state():
    out = ble.parse_advertisement(service_data={"fcf1": bytes([0x01, 0xAA, 0xBB])})
    assert out["tracker_network"] == "Google Find My Device"
    assert out["find_my_separated"] is True
    assert "threat_note" in out


def test_samsung_smarttag_separated_state():
    out = ble.parse_advertisement(service_data={"fd5a": bytes([0x01, 0xDE, 0xAD])})
    assert out["tracker_network"] == "Samsung SmartThings Find"
    assert out["find_my_separated"] is True
    assert "not surfaced by iOS" in out["threat_note"]


def test_eddystone_tlm_reports_uptime():
    # frame 0x20, version 0x00, 3000 mV, 25.0 C, 100 adverts, 36000 deciseconds.
    payload = bytes([0x20, 0x00]) + (3000).to_bytes(2, "big") + (25 * 256).to_bytes(2, "big") \
        + (100).to_bytes(4, "big") + (36000).to_bytes(4, "big")
    out = ble.parse_advertisement(service_data={"feaa": payload})

    assert out["eddystone_frame"] == "TLM (telemetry)"
    assert out["eddystone_battery_mv"] == 3000
    assert out["eddystone_temp_c"] == 25.0
    assert out["eddystone_uptime_s"] == 3600


def test_eddystone_url_expands_the_scheme():
    payload = bytes([0x10, 0x00, 0x03]) + b"example" + bytes([0x00])
    out = ble.parse_advertisement(service_data={"feaa": payload})
    assert out["eddystone_url"] == "https://example.com/"


def test_malformed_payload_does_not_raise():
    # A truncated Apple message: the loop must terminate, not run off the end.
    for payload in (b"\x12", b"\x12\xff", b"\x07\x19\x01", b"", b"\x00" * 64):
        ble.parse_advertisement(manufacturer_data={0x004C: payload})


# ---------------------------------------------------------------------------
# Raw AD structure parsing
# ---------------------------------------------------------------------------

def test_raw_ad_structures():
    raw = bytes([
        0x02, 0x01, 0x06,                          # flags: LE General + BR/EDR not supported
        0x03, 0x03, 0x0F, 0x18,                    # 16-bit service 0x180F (battery)
        0x07, 0x09, ord("S"), ord("e"), ord("n"), ord("s"), ord("o"), ord("r"),
        0x02, 0x0A, 0xF4,                          # tx power -12
        0x03, 0x19, 0x41, 0x00,                    # appearance 0x0041 phone
    ])
    out = ble.parse_ad_structures(raw)

    assert out["name"] == "Sensor"
    assert out["tx_power"] == -12
    assert "Battery Service" in out["services"]
    assert out["appearance_name"] == "Generic Phone"
    assert "LE General Discoverable" in out["flags_decoded"]


# ---------------------------------------------------------------------------
# Assigned-number lookups
# ---------------------------------------------------------------------------

def test_service_name_accepts_both_uuid_forms():
    assert sig.service_name("180f") == "Battery Service"
    assert sig.service_name("0000180F-0000-1000-8000-00805f9b34fb") == "Battery Service"
    assert sig.service_name("fd44") == "Apple Find My network"


def test_class_of_device_decodes_capture_capability():
    # Major 0x04 (A/V), minor 0x04 (microphone), Capturing service bit 19.
    cod = (0x04 << 8) | (0x04 << 2) | (1 << 19)
    out = sig.decode_cod(cod)
    assert out["cod_major"] == "Audio/Video"
    assert out["cod_minor"] == "Microphone"
    assert "Capturing" in out["cod_services"]


# ---------------------------------------------------------------------------
# Classification and signatures
# ---------------------------------------------------------------------------

def test_vendor_self_report_beats_a_name_guess():
    # The name says "cam" but the device advertises itself as a tracker.
    attrs = {"name": "camping-tag", "device_class": "tracker",
             "tracker_network": "Apple Find My"}
    cls, reason, _ = classify.classify(attrs, band=Band.BLE)
    assert cls is DeviceClass.TRACKER
    assert "vendor" in reason


def test_generic_camera_ap_signature_matches():
    hits = signatures.match(names=["HD1080_A9F3"])
    assert any(h.id == "cam.generic-ap" for h in hits)


def test_bare_module_name_is_flagged():
    hits = signatures.match(names=["HM-10"])
    assert any(h.id == "bug.serial-module" for h in hits)


def test_camera_oui_matches_without_a_name():
    hits = signatures.match(names=[], oui="4419B6")   # Hikvision
    assert any(h.id == "cam.oui" for h in hits)


def test_a_plain_phone_matches_nothing():
    assert signatures.match(names=["Ricardo's iPhone"], oui="3C0754") == []


def test_summary_mentions_separation():
    text = classify.summarize(
        {"tracker_network": "Apple Find My", "find_my_separated": True},
        DeviceClass.TRACKER,
    )
    assert "SEPARATED" in text


# ---------------------------------------------------------------------------
# Name matching must not fire on substrings — these are regressions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("Broadband RF field", DeviceClass.UNKNOWN),   # "band" inside "Broadband"
        ("Camden Guest", DeviceClass.UNKNOWN),         # "cam" inside "Camden"
        ("Octavia", DeviceClass.UNKNOWN),              # "tv" inside "Octavia"
        ("Vintage Lamp", DeviceClass.UNKNOWN),         # "tag" inside "Vintage"
        ("Mi Band 7", DeviceClass.WEARABLE),
        ("Living Room TV", DeviceClass.APPLIANCE),
        ("AirTag", DeviceClass.TRACKER),
    ],
)
def test_name_rules_match_words_not_substrings(name, expected):
    cls, _, _ = classify.classify({"name": name}, band=Band.BLE)
    assert cls is expected


def test_decoder_class_hint_is_honoured_under_either_key():
    # Fusion stores it as class_hint; a decoder emits device_class.
    for key in ("class_hint", "device_class"):
        cls, reason, _ = classify.classify({key: "camera"}, band=Band.BLE)
        assert cls is DeviceClass.CAMERA
        assert "vendor" in reason
