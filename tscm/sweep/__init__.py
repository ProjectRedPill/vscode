"""sweep — passive multi-band device discovery and counter-surveillance sweep.

Listens to Bluetooth LE, Bluetooth Classic, Wi-Fi, sub-GHz ISM, broadband RF and
infrared, fuses what it hears into device identities, and tells you as much as
each device is willing to reveal about itself.

Receive-only by design: it never connects, pairs, transmits, deauthenticates or
jams. Everything it reports comes from what devices already broadcast in the
clear.
"""

__version__ = "0.1.0"

from .core.engine import Engine, EngineConfig
from .core.models import Band, Device, DeviceClass, Finding, Observation, Trust

__all__ = [
    "Band", "Device", "DeviceClass", "Engine", "EngineConfig",
    "Finding", "Observation", "Trust", "__version__",
]
