"""Web UI: a responsive dashboard served from the sweep engine itself.

The natural deployment is a split one — the radios live on a laptop or a
Raspberry Pi, and you read the results on a phone. iOS cannot host most of this
hardware (no libusb, no raw HCI, no monitor mode), so the phone being a client
rather than a sensor is a platform constraint, not a shortcut.
"""

from .server import WebServer

__all__ = ["WebServer"]
