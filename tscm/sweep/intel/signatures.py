"""Known signatures of covert and surveillance-capable hardware.

These are *identification* aids, not accusations. A match means "this looks like
a class of device that is frequently used covertly", and the UI must say so in
those words. Cheap IP cameras and legitimate baby monitors share firmware.

Sources are public product documentation and the default SSID/name strings the
firmware ships with. Nothing here requires interacting with the device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signature:
    id: str
    label: str
    device_class: str
    severity: int
    why: str
    # Matched case-insensitively against name / SSID / model strings.
    name_patterns: tuple[str, ...] = ()
    # Matched against decoded attribute keys present on the device.
    attr_keys: tuple[str, ...] = ()
    oui_prefixes: tuple[str, ...] = ()


SIGNATURES: tuple[Signature, ...] = (
    # ---- covert cameras ------------------------------------------------
    Signature(
        id="cam.generic-ap",
        label="Wireless camera in AP/setup mode",
        device_class="camera",
        severity=3,
        why=(
            "Cheap Wi-Fi cameras expose a self-hosted access point with a stock "
            "SSID while unconfigured or when they lose their network. Seeing one "
            "in a space you did not set up is a strong indicator."
        ),
        name_patterns=(
            r"^HD[-_]?\d{4,}", r"^CAM[-_]?\d{4,}", r"^IPC[-_]?", r"^IPCAM",
            r"^ANYKA", r"^A9[-_]?\d+", r"^Q\d{1,2}[-_]?\d{4,}",
            r"^SQ\d{2}", r"^JX[-_]?\d+", r"^V380", r"^YCC365",
            r"^CLOUDCAM", r"^MINICAM", r"^SPYCAM", r"^HIDDEN",
            r"^WIFICAM", r"^P2P[-_]?CAM", r"^BC\d{2}[-_]?", r"^XMEye",
            r"^DVR[-_]?\d+", r"^NVR[-_]?\d+", r"^GoodCam", r"^LIVE[-_]?\d{4,}",
        ),
    ),
    Signature(
        id="cam.branded",
        label="Consumer security camera",
        device_class="camera",
        severity=1,
        why="A known-brand camera. Expected in many homes; note it and move on unless unexpected.",
        name_patterns=(
            r"wyze", r"blink", r"ring", r"arlo", r"nest\s*cam", r"eufy",
            r"reolink", r"tapo[-_ ]?c\d", r"kasa[-_ ]?cam", r"mi[-_ ]?camera",
            r"hikvision", r"dahua", r"amcrest", r"lorex", r"annke",
        ),
    ),
    Signature(
        id="cam.oui",
        label="Camera-vendor hardware address",
        device_class="camera",
        severity=2,
        why="The OUI belongs to a vendor that predominantly ships cameras or DVRs.",
        oui_prefixes=(
            "001C27", "4419B6", "BCAD28", "C0560E",  # Hikvision
            "28571C", "3CEF8C", "9C144D",            # Dahua
            "00408C", "ACCC8E", "B8A44F",            # Axis
            "A4DA22", "2CAA8E",                       # Wyze
            "0C8C24", "EC71DB",                       # Reolink
            "7CDD90",                                 # Ogemray camera modules
        ),
    ),

    # ---- audio bugs and recorders --------------------------------------
    Signature(
        id="bug.audio-ble",
        label="Bluetooth audio bug / covert microphone",
        device_class="microphone",
        severity=3,
        why=(
            "Advertises a microphone or audio-source role under a generic or "
            "numeric name. Covert BLE recorders commonly ship with an unedited "
            "module name."
        ),
        name_patterns=(
            r"^BT[-_]?REC", r"^VOICE[-_]?REC", r"^MIC[-_]?\d+",
            r"^AUDIO[-_]?\d{3,}", r"^N9\b", r"^S8[-_]?SPY",
        ),
        attr_keys=("appearance_name",),
    ),
    Signature(
        id="bug.serial-module",
        label="Bare serial radio module",
        device_class="covert",
        severity=2,
        why=(
            "HM-10/JDY/ESP-style modules advertise a factory default name. "
            "They are the building block of home-made bugs and are almost never "
            "found in finished consumer products."
        ),
        name_patterns=(
            r"^HM-?10\b", r"^HC-?0[568]\b", r"^JDY-?\d+", r"^AT-?09",
            r"^MLT-?BT", r"^BT0[45]-?A", r"^ESP32?[-_]?[A-F0-9]{4,}$",
            r"^Bluetooth[-_ ]?BLE$", r"^BLE[-_]?Device$", r"^Unnamed$",
        ),
    ),

    # ---- trackers ------------------------------------------------------
    Signature(
        id="tracker.separated",
        label="Tracker separated from its owner",
        device_class="tracker",
        severity=3,
        why=(
            "Finding-network tags only broadcast the separated state when their "
            "owner is not nearby. If this tag stays with you as you move, it is "
            "travelling with you and not with its owner."
        ),
        attr_keys=("find_my_separated",),
    ),
    Signature(
        id="tracker.gps",
        label="GPS/GSM tracker hardware",
        device_class="tracker",
        severity=3,
        why="OUI belongs to a vendor whose main product line is vehicle/asset GPS trackers.",
        oui_prefixes=("0C1105", "3413A8", "1C1BB5", "001BDC"),
        name_patterns=(r"^GT0?\d{2}\b", r"^TK\d{3}\b", r"^GPS[-_]?TRACK"),
    ),

    # ---- sub-GHz / ISM --------------------------------------------------
    Signature(
        id="ism.covert-tx",
        label="Continuous sub-GHz transmitter",
        device_class="covert",
        severity=3,
        why=(
            "Analogue and FSK bugs on 315/433/868 MHz transmit continuously or "
            "on voice activation, unlike sensors which send short bursts minutes "
            "apart."
        ),
    ),
    Signature(
        id="ism.sensor",
        label="ISM-band sensor",
        device_class="sensor",
        severity=0,
        why="A decoded consumer sensor (weather, TPMS, door contact). Usually benign.",
    ),

    # ---- infrared -------------------------------------------------------
    Signature(
        id="ir.illuminator",
        label="Infrared illuminator (night-vision camera)",
        device_class="camera",
        severity=3,
        why=(
            "Steady 850/940 nm emission with no remote-control protocol is how a "
            "night-vision camera lights a dark room. It is invisible to the eye "
            "and one of the most reliable hidden-camera tells."
        ),
    ),
    Signature(
        id="ir.remote",
        label="Infrared remote traffic",
        device_class="ir_emitter",
        severity=0,
        why="Decoded a standard consumer remote protocol. Benign unless unexpected.",
    ),

    # ---- interference ---------------------------------------------------
    Signature(
        id="rf.jammer",
        label="Possible jamming or wideband interference",
        device_class="jammer",
        severity=4,
        why=(
            "Broadband noise floor is elevated across a band with no decodable "
            "traffic. This is what a jammer looks like, and it is also what a "
            "faulty switching supply looks like — verify by moving."
        ),
    ),
)

_COMPILED = {
    s.id: [re.compile(p, re.IGNORECASE) for p in s.name_patterns] for s in SIGNATURES
}


def match(
    *,
    names: list[str] | None = None,
    attrs: dict | None = None,
    oui: str | None = None,
) -> list[Signature]:
    """Return every signature matching the supplied evidence, strongest first."""
    names = [n for n in (names or []) if n]
    attrs = attrs or {}
    hits: list[Signature] = []

    for sig_def in SIGNATURES:
        if sig_def.oui_prefixes and oui and oui.upper() in sig_def.oui_prefixes:
            hits.append(sig_def)
            continue
        patterns = _COMPILED[sig_def.id]
        if patterns and any(p.search(n) for p in patterns for n in names):
            hits.append(sig_def)
            continue
        if sig_def.attr_keys and all(attrs.get(k) for k in sig_def.attr_keys):
            hits.append(sig_def)

    return sorted(hits, key=lambda s: -s.severity)


def by_id(sig_id: str) -> Signature | None:
    return next((s for s in SIGNATURES if s.id == sig_id), None)
