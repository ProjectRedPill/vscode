"""End-to-end: a fake sensor drives the real engine, fusion, rules and report."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from sweep.core.engine import Engine, EngineConfig
from sweep.core.models import Band, Observation, Trust
from sweep.sensors import REGISTRY
from sweep.sensors.base import Sensor
from sweep.ui import report


class FakeSensor(Sensor):
    """Replays a scripted list of observations, then idles."""

    name = "fake"
    band = Band.BLE

    script: list[Observation] = []

    async def available(self) -> tuple[bool, str]:
        return True, "fake sensor"

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        for obs in self.script:
            if stop.is_set():
                return
            self._count()
            yield obs
        while not stop.is_set():
            await asyncio.sleep(0.05)


@pytest.fixture
def fake_registry():
    REGISTRY["fake"] = FakeSensor
    yield
    REGISTRY.pop("fake", None)
    FakeSensor.script = []


def adv(address, **attrs) -> Observation:
    return Observation(
        band=Band.BLE, sensor="fake", address=address,
        rssi=attrs.pop("rssi", -55.0), attrs=attrs, name=attrs.get("name"),
    )


async def test_engine_ingests_classifies_and_reports(tmp_path, fake_registry):
    FakeSensor.script = [
        adv("AA:BB:CC:00:00:01", name="Ricardo's iPhone", device_class="phone"),
        adv("42:11:22:33:44:55", device_class="tracker", find_my_separated=True,
            tracker_network="Apple Find My", rssi=-45.0),
        adv("C2:99:88:77:66:55", rssi=-38.0),   # unnamed, very close
    ]

    engine = Engine(EngineConfig(sensors=["fake"], db_path=str(tmp_path / "e.db")))
    await engine.run_for(1.5)

    names = {d.display_name() for d in engine.fusion.devices.values()}
    assert "Ricardo's iPhone" in names

    flagged = [d for d in engine.fusion.devices.values() if d.risk >= 2]
    assert flagged, "the separated tracker and the unnamed close device should flag"

    rules = {f.rule for d in engine.fusion.devices.values() for f in d.findings}
    assert "tracker.separated" in rules
    assert "device.unnamed-close" in rules


async def test_report_names_its_own_blind_spots(tmp_path, fake_registry):
    FakeSensor.script = [adv("AA:BB:CC:00:00:01", name="thing")]
    engine = Engine(EngineConfig(sensors=["fake", "ir"], db_path=str(tmp_path / "e.db")))
    await engine.probe()
    await engine.start()
    await asyncio.sleep(0.5)
    engine.evaluate_all()
    text = report.markdown_report(engine)
    await engine.shutdown()

    assert "## Coverage" in text
    assert "Blind spots" in text
    # IR was requested but has no probe attached, so the report must say what
    # that costs rather than quietly omitting the band.
    assert "night-vision camera illuminators" in text
    assert "not an all-clear" in text or "Findings" in text


async def test_json_report_is_valid_and_complete(tmp_path, fake_registry):
    import json

    FakeSensor.script = [
        adv("42:11:22:33:44:55", device_class="tracker", find_my_separated=True,
            tracker_network="Apple Find My"),
    ]
    engine = Engine(EngineConfig(sensors=["fake"], db_path=str(tmp_path / "e.db")))
    await engine.probe()
    await engine.start()
    await asyncio.sleep(0.5)
    engine.evaluate_all()
    data = json.loads(report.json_report(engine))
    await engine.shutdown()

    assert data["devices"]
    device = data["devices"][0]
    for key in ("attributes", "findings", "tracks", "identity_links", "bands"):
        assert key in device


async def test_trust_persists_across_engine_restarts(tmp_path, fake_registry):
    db = str(tmp_path / "e.db")
    FakeSensor.script = [adv("AA:BB:CC:00:00:01", name="my speaker")]

    first = Engine(EngineConfig(sensors=["fake"], db_path=db))
    await first.probe()
    await first.start()
    await asyncio.sleep(0.4)
    dev = first.fusion.get("AA:BB:CC:00:00:01")
    assert dev is not None
    first.set_trust(dev.id, Trust.MINE, "living room speaker")
    await first.shutdown()

    second = Engine(EngineConfig(sensors=["fake"], db_path=db))
    await second.probe()
    await second.start()
    await asyncio.sleep(0.4)
    again = second.fusion.get("AA:BB:CC:00:00:01")
    await second.shutdown()

    assert again is not None
    assert again.trust is Trust.MINE
    assert again.label == "living room speaker"


async def test_ranging_target_receives_samples(tmp_path, fake_registry):
    FakeSensor.script = [adv("AA:BB:CC:00:00:01", name="target", rssi=-70.0)] * 20
    engine = Engine(EngineConfig(sensors=["fake"], db_path=str(tmp_path / "e.db")))
    await engine.probe()
    await engine.start()
    await asyncio.sleep(0.4)

    target = engine.target("target")
    assert target is not None
    engine.handle(adv("AA:BB:CC:00:00:01", name="target", rssi=-50.0))

    reading = engine.ranger.read(time.time())
    await engine.shutdown()
    assert reading.samples_total > 0


async def test_engine_survives_a_sensor_that_raises(tmp_path):
    class BrokenSensor(Sensor):
        name = "broken"
        band = Band.BLE

        async def available(self):
            return True, "broken on purpose"

        async def run(self, stop):
            yield adv("AA:BB:CC:00:00:01", name="one")
            raise RuntimeError("sensor exploded")

    REGISTRY["broken"] = BrokenSensor
    try:
        engine = Engine(EngineConfig(sensors=["broken"], db_path=str(tmp_path / "e.db")))
        await engine.run_for(1.0)
        assert len(engine.fusion.devices) == 1
        assert engine.sensors[0].status.errors == 1
    finally:
        REGISTRY.pop("broken", None)


async def test_unavailable_sensors_are_skipped_not_fatal(tmp_path):
    engine = Engine(EngineConfig(sensors=["ir", "rf-power"], db_path=str(tmp_path / "e.db")))
    statuses = await engine.probe()
    assert all(not s.available for s in statuses)
    await engine.run_for(0.3)   # must not hang or raise
