"""Sensor registry.

`build()` is the only entry point the rest of the tool uses. It returns every
sensor the caller asked for, configured but not yet probed — probing is the
engine's job so the UI can show progress.
"""

from __future__ import annotations

from typing import Any

from .base import NullSensor, Sensor, SensorStatus
from .ble import BleSensor
from .btclassic import BtClassicSensor
from .ir import IrSensor
from .rfpower import RfPowerSensor
from .sdr import Rtl433Sensor, SpectrumSensor
from .wifi import WifiSensor

REGISTRY: dict[str, type[Sensor]] = {
    "ble": BleSensor,
    "bt-classic": BtClassicSensor,
    "wifi": WifiSensor,
    "rtl433": Rtl433Sensor,
    "spectrum": SpectrumSensor,
    "ir": IrSensor,
    "rf-power": RfPowerSensor,
}

#: Sensors enabled when the user does not choose. These are the ones that work
#: with no extra hardware on a normal laptop.
DEFAULT_SENSORS = ("ble", "bt-classic", "wifi")

#: Everything, including the sensors that need a dongle or a probe.
ALL_SENSORS = tuple(REGISTRY)


def build(names: list[str] | None = None, **options: Any) -> list[Sensor]:
    """Instantiate sensors by name, passing each only the options it declares."""
    selected = list(names or DEFAULT_SENSORS)
    out: list[Sensor] = []
    for name in selected:
        cls = REGISTRY.get(name)
        if cls is None:
            continue
        out.append(cls(**_options_for(name, options)))
    return out


_OPTION_PREFIX = {
    "ble": "ble_",
    "bt-classic": "bt_",
    "wifi": "wifi_",
    "rtl433": "rtl433_",
    "spectrum": "spectrum_",
    "ir": "ir_",
    "rf-power": "rf_",
}


def _options_for(name: str, options: dict[str, Any]) -> dict[str, Any]:
    # Sensors registered at runtime (tests, plugins) get a prefix derived from
    # their name rather than a KeyError.
    prefix = _OPTION_PREFIX.get(name, name.replace("-", "_") + "_")
    return {
        key[len(prefix) :]: value
        for key, value in options.items()
        if key.startswith(prefix) and value is not None
    }


__all__ = [
    "ALL_SENSORS",
    "DEFAULT_SENSORS",
    "REGISTRY",
    "NullSensor",
    "Sensor",
    "SensorStatus",
    "build",
]
