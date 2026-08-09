"""BLE advertisement decoding.

`parse_advertisement()` takes whatever a platform BLE stack hands back and
returns a flat dict of decoded facts. Every vendor decoder is best-effort and
isolated: a malformed Apple payload must never stop the Microsoft one from
running, because in a real sweep the interesting packets are the malformed ones.
"""

from __future__ import annotations

from typing import Any

from . import sig
from .parsers import apple, beacons, google, microsoft, samsung, tile


def _hex(data: bytes) -> str:
    return data.hex()


def parse_advertisement(
    *,
    local_name: str | None = None,
    manufacturer_data: dict[int, bytes] | None = None,
    service_data: dict[str, bytes] | None = None,
    service_uuids: list[str] | None = None,
    tx_power: int | None = None,
    appearance: int | None = None,
    platform_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decode one advertisement into named facts.

    Returns a flat dict. Keys are stable and safe to render directly; the
    caller decides what to show.
    """
    out: dict[str, Any] = {}

    if local_name:
        out["name"] = local_name
    if tx_power is not None:
        out["tx_power"] = tx_power
    if appearance is not None:
        out["appearance"] = appearance
        name = sig.appearance_name(appearance)
        if name:
            out["appearance_name"] = name

    # -- advertised services -------------------------------------------
    uuids = list(service_uuids or [])
    if uuids:
        out["service_uuids"] = uuids
        named = []
        for u in uuids:
            n = sig.service_name(u)
            if n:
                named.append(n)
        if named:
            out["services"] = named

    # -- manufacturer-specific data ------------------------------------
    for cid, payload in (manufacturer_data or {}).items():
        company = sig.company_name(cid)
        out.setdefault("company_ids", []).append(f"0x{cid:04X}")
        if company:
            out.setdefault("companies", []).append(company)
            out.setdefault("vendor", company)
        out[f"mfr_data_{cid:04x}"] = _hex(payload)

        for decoder in (
            apple.decode,
            microsoft.decode,
            samsung.decode,
            google.decode_manufacturer,
            tile.decode_manufacturer,
            beacons.decode_ibeacon,
        ):
            try:
                found = decoder(cid, payload)
            except Exception:  # a bad packet must not kill the sweep
                continue
            if found:
                out.update(found)

    # -- service data ---------------------------------------------------
    for uuid, payload in (service_data or {}).items():
        short = _short_uuid(uuid)
        out[f"svc_data_{short or uuid}"] = _hex(payload)
        name = sig.service_name(uuid)
        if name:
            out.setdefault("services", []).append(name)
        for decoder in (
            beacons.decode_eddystone,
            google.decode_service_data,
            samsung.decode_service_data,
            tile.decode_service_data,
            apple.decode_service_data,
        ):
            try:
                found = decoder(uuid, payload)
            except Exception:
                continue
            if found:
                out.update(found)

    if platform_data:
        for k, v in platform_data.items():
            if isinstance(v, (str, int, float, bool)):
                out[f"plat_{k}"] = v

    # Dedupe list-valued fields while keeping order.
    for key in ("services", "companies", "company_ids"):
        if key in out:
            out[key] = list(dict.fromkeys(out[key]))

    return out


def _short_uuid(uuid: str) -> str | None:
    u = uuid.lower()
    if len(u) == 4:
        return u
    if u.startswith("0000") and u.endswith("-0000-1000-8000-00805f9b34fb"):
        return u[4:8]
    return None


def parse_ad_structures(raw: bytes) -> dict[str, Any]:
    """Parse a raw length/type/value AD payload (HCI-level captures).

    Platform APIs normally pre-split this, but sniffers and `btmon` hand back
    the raw blob, and some fields (flags, slave connection interval) are only
    visible here.
    """
    out: dict[str, Any] = {}
    mfr: dict[int, bytes] = {}
    svc_data: dict[str, bytes] = {}
    uuids: list[str] = []
    i = 0
    while i < len(raw):
        length = raw[i]
        if length == 0 or i + length >= len(raw) + 1:
            break
        ad_type = raw[i + 1]
        value = raw[i + 2 : i + 1 + length]
        i += 1 + length

        if ad_type == 0x01 and value:
            out["flags"] = value[0]
            out["flags_decoded"] = _decode_flags(value[0])
        elif ad_type in (0x02, 0x03):
            uuids += [
                f"{int.from_bytes(value[j:j+2], 'little'):04x}"
                for j in range(0, len(value) - 1, 2)
            ]
        elif ad_type in (0x06, 0x07):
            for j in range(0, len(value) - 15, 16):
                b = value[j : j + 16][::-1]
                uuids.append(
                    f"{b[:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-{b[8:10].hex()}-{b[10:].hex()}"
                )
        elif ad_type in (0x08, 0x09):
            out["name"] = value.decode("utf-8", "replace")
            out["name_complete"] = ad_type == 0x09
        elif ad_type == 0x0A and value:
            out["tx_power"] = int.from_bytes(value[:1], "little", signed=True)
        elif ad_type == 0x12 and len(value) >= 4:
            out["conn_interval_min_ms"] = int.from_bytes(value[0:2], "little") * 1.25
            out["conn_interval_max_ms"] = int.from_bytes(value[2:4], "little") * 1.25
        elif ad_type == 0x16 and len(value) >= 2:
            svc_data[f"{int.from_bytes(value[:2], 'little'):04x}"] = value[2:]
        elif ad_type == 0x19 and len(value) >= 2:
            out["appearance"] = int.from_bytes(value[:2], "little")
        elif ad_type == 0xFF and len(value) >= 2:
            mfr[int.from_bytes(value[:2], "little")] = value[2:]

    decoded = parse_advertisement(
        local_name=out.get("name"),
        manufacturer_data=mfr or None,
        service_data=svc_data or None,
        service_uuids=uuids or None,
        tx_power=out.get("tx_power"),
        appearance=out.get("appearance"),
    )
    decoded.update({k: v for k, v in out.items() if k not in decoded})
    return decoded


def _decode_flags(flags: int) -> list[str]:
    bits = [
        (0x01, "LE Limited Discoverable"),
        (0x02, "LE General Discoverable"),
        (0x04, "BR/EDR Not Supported"),
        (0x08, "LE + BR/EDR (controller)"),
        (0x10, "LE + BR/EDR (host)"),
    ]
    return [name for bit, name in bits if flags & bit]
