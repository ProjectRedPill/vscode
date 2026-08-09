"""Sweep reports.

A sweep is worth writing down: it is a record of what was present in a place at
a time, and its value is mostly in comparison against the next one. Markdown for
reading, JSON for diffing and for feeding anything else.

The report is deliberately explicit about what was *not* covered. A report that
lists three findings and says nothing about the bands it could not sense invites
the reader to conclude the room is clean, which the tool has no basis to claim.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..core.engine import Engine, device_dict
from ..core.models import Band, Trust

SEVERITY_WORD = {0: "Info", 1: "Low", 2: "Medium", 3: "High", 4: "Critical"}

BAND_MEANING = {
    Band.BLE.value: "Bluetooth Low Energy advertisements",
    Band.BT_CLASSIC.value: "Bluetooth Classic (BR/EDR) discoverable devices",
    Band.WIFI.value: "Wi-Fi access points",
    Band.ISM_SUB_GHZ.value: "decoded 315/433/868/915 MHz device traffic",
    Band.RF_BROADBAND.value: "raw RF energy (no demodulation)",
    Band.IR.value: "infrared, coded and flood",
}

#: What a missing sensor means you did not look at.
BLIND_SPOTS = {
    Band.BLE.value: "BLE trackers, beacons, and most modern accessories",
    Band.BT_CLASSIC.value: "wireless microphones, dashcams and headsets that are BR/EDR-only",
    Band.WIFI.value: "Wi-Fi cameras — the most common covert camera type",
    Band.ISM_SUB_GHZ.value: "sub-GHz sensors, key fobs and cheap 433 MHz bugs",
    Band.RF_BROADBAND.value: "analogue video transmitters and any protocol without a decoder",
    Band.IR.value: "night-vision camera illuminators and IR control links",
}


def json_report(engine: Engine) -> str:
    snap = engine.snapshot()
    snap["generated"] = time.time()
    snap["coverage"] = _coverage(engine)
    return json.dumps(snap, indent=2, default=str)


def markdown_report(engine: Engine, *, include_all: bool = False) -> str:
    snap = engine.snapshot()
    devices = snap["devices"]
    coverage = _coverage(engine)

    lines: list[str] = []
    add = lines.append

    add("# Sweep report")
    add("")
    add(f"- **Session** `{engine.session}`")
    add(f"- **Started** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(engine.started))}")
    add(f"- **Duration** {snap['elapsed_s'] / 60:.1f} min across {snap['epoch'] + 1} location(s)")
    add(f"- **Devices** {snap['stats']['devices']} seen, {snap['stats']['present']} present at the end")
    add(f"- **Packets** {snap['stats']['observations']:,}")
    if snap["dropped_observations"]:
        add(f"- **Dropped** {snap['dropped_observations']:,} observations "
            "(capture outran processing — results are still valid but incomplete)")
    add("")

    # -- what was actually covered -------------------------------------
    add("## Coverage")
    add("")
    add("| Band | What it sees | Status |")
    add("| --- | --- | --- |")
    for band, info in coverage.items():
        status = "✅ active" if info["active"] else f"❌ not covered — {info['reason']}"
        add(f"| `{band}` | {BAND_MEANING.get(band, band)} | {status} |")
    add("")
    missing = [b for b, i in coverage.items() if not i["active"]]
    if missing:
        add("**Blind spots in this sweep.** The following were not sensed, so nothing "
            "in this report says anything about them:")
        add("")
        for band in missing:
            add(f"- **{band}** — {BLIND_SPOTS.get(band, 'unknown')}")
        add("")

    # -- findings -------------------------------------------------------
    flagged = [d for d in devices if d["risk"] >= 2]
    add("## Findings")
    add("")
    if not flagged:
        add("No device reached medium severity or above.")
        add("")
        add("> This is not an all-clear. A powered-off camera, a wired device, an "
            "SD-card recorder with no radio, or a device using a protocol none of "
            "the active sensors decode would all produce exactly this result.")
    else:
        for dev in sorted(flagged, key=lambda d: -d["risk"]):
            add(f"### {SEVERITY_WORD[dev['risk']]} — {dev['name']}")
            add("")
            add(f"- **Class** {dev['class']} · **Vendor** {dev['vendor'] or 'unknown'}"
                f" · **Bands** {', '.join(dev['bands'])}")
            add(f"- **Address** `{dev['address']}` · **Signal** "
                f"{dev['rssi']} dBm (~{dev['distance_m_estimate']} m)")
            add(f"- **Seen** {(dev['last_seen'] - dev['first_seen']) / 60:.1f} min "
                f"across {dev.get('epochs_seen', 1)} location(s)")
            if dev.get("seen_in_previous_sessions"):
                add(f"- **History** also present in {dev['seen_in_previous_sessions']} "
                    "earlier sweep(s)")
            add("")
            for f in dev["findings"]:
                add(f"**{f['severity_label'].upper()} · {f['title']}**")
                add("")
                add(f["detail"])
                add("")
                if f["evidence"]:
                    add(f"<sub>Evidence: `{json.dumps(f['evidence'], default=str)}`</sub>")
                    add("")
            if dev.get("identity_links"):
                add("Identity links:")
                for link in dev["identity_links"]:
                    add(f"- {link['confidence']:.2f} — {link['reason']} "
                        f"(from `{link['previous_address']}`)")
                add("")

    # -- inventory ------------------------------------------------------
    add("## Inventory")
    add("")
    shown = devices if include_all else [d for d in devices if d["trust"] != Trust.MINE.value]
    add("| Signal | Class | Name | Vendor | Address | Bands | Notes |")
    add("| ---: | --- | --- | --- | --- | --- | --- |")
    for dev in shown:
        rssi = f"{dev['rssi']:.0f}" if dev["rssi"] is not None else "?"
        notes = dev["attributes"].get("summary", "")
        add(
            f"| {rssi} | {dev['class']} | {_esc(dev['name'])} | "
            f"{_esc(dev['vendor'] or '—')} | `{dev['address']}` | "
            f"{', '.join(dev['bands'])} | {_esc(str(notes))[:120]} |"
        )
    add("")

    add("## Method and limits")
    add("")
    add(
        "Every sensor here is receive-only: advertisements, beacon frames, ISM "
        "traffic and RF energy that the devices themselves broadcast. Nothing was "
        "connected to, paired with, deauthenticated or jammed."
    )
    add("")
    add(
        "Distance figures come from a log-distance path-loss model. Indoors, "
        "expect them to be wrong by a factor of two or more — walls, bodies and "
        "metal all attenuate. Use them for ordering, not for measuring."
    )
    add("")
    add(
        "Signature matches identify devices that *look like* a class of hardware. "
        "Names and MAC addresses can be changed by anyone who cares to. A match is "
        "a reason to look; an absence of matches is not a reason to relax."
    )
    return "\n".join(lines)


def _coverage(engine: Engine) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for band in BAND_MEANING:
        sensors = [s for s in engine.sensors if s.band.value == band]
        active = [s for s in sensors if s.status.available]
        if active:
            out[band] = {
                "active": True,
                "reason": "; ".join(s.status.reason for s in active),
                "observations": sum(s.status.observations for s in active),
                "sensors": [s.name for s in active],
            }
        elif sensors:
            out[band] = {
                "active": False,
                "reason": "; ".join(s.status.reason or "unavailable" for s in sensors),
                "observations": 0,
                "sensors": [s.name for s in sensors],
                "hint": "; ".join(s.hint for s in sensors if s.hint),
            }
        else:
            out[band] = {
                "active": False,
                "reason": "sensor not enabled for this sweep",
                "observations": 0,
                "sensors": [],
            }
    return out


def device_report(engine: Engine, needle: str) -> str:
    """Everything known about one device, as JSON."""
    dev = engine.fusion.get(needle)
    if dev is None:
        return json.dumps({"error": f"no device matching {needle!r}"}, indent=2)
    detail = device_dict(dev, engine)
    detail["history"] = engine.store.history_for_address(dev.address)
    return json.dumps(detail, indent=2, default=str)


def _esc(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
