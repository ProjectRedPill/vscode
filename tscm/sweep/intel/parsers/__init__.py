"""Vendor-specific advertisement decoders.

Each module exposes best-effort `decode(cid, payload)` and/or
`decode_service_data(uuid, payload)`. Both must return `None` when the payload
is not theirs, and must never raise on malformed input.
"""

from . import apple, beacons, google, microsoft, samsung, tile

__all__ = ["apple", "beacons", "google", "microsoft", "samsung", "tile"]
