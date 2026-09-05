"""The engine: runs sensors, feeds fusion, applies rules, persists.

One asyncio task per sensor, all writing into one queue that a single consumer
drains. That keeps fusion single-threaded — no locks around the device table —
while capture stays concurrent, which matters because a Wi-Fi scan blocks for
seconds at a time and BLE adverts must not be dropped while it does.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..sensors import Sensor, SensorStatus, build
from ..threat import SweepContext, evaluate
from .fusion import Fusion, FusionConfig
from .models import Band, Device, Finding, Observation, Trust
from .rssi import Ranger
from .store import Store


@dataclass
class EngineConfig:
    sensors: list[str] = field(default_factory=list)
    sensor_options: dict[str, Any] = field(default_factory=dict)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    log_raw: bool = False
    db_path: str | None = None
    #: Seconds between rule evaluation passes. Rules are cheap but not free,
    #: and re-running them per packet in a busy room is wasteful.
    evaluate_interval: float = 2.0
    persist_interval: float = 20.0


class Engine:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.session = uuid.uuid4().hex[:10]
        self.fusion = Fusion(self.config.fusion)
        self.context = SweepContext()
        self.store = Store(self.config.db_path)
        self.sensors: list[Sensor] = []
        self.stop = asyncio.Event()
        self.queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize=8192)
        self.started = time.time()
        self.dropped = 0

        #: Target being ranged, if any. Set by the finder UI.
        self.range_target: str | None = None
        self.ranger = Ranger()

        self._tasks: list[asyncio.Task[Any]] = []
        self._last_eval = 0.0
        self._last_persist = 0.0
        self._on_finding: list[Callable[[Device, Finding], None]] = []
        # "Seen in earlier sessions" cannot change during this session, but it
        # was queried from SQLite per device on every snapshot — and snapshots
        # drive the SSE stream at 2/s per connected client. Cached by
        # (device id, address) so a re-linked address still gets a fresh look.
        self._seen_before: dict[tuple[str, str], int] = {}

    # -- lifecycle -------------------------------------------------------

    async def probe(self) -> list[SensorStatus]:
        # Merge any host-provided vendor tables (Wireshark manuf, IEEE oui.txt)
        # before scanning starts. This used to happen only in `sweep doctor`,
        # so live scans identified vendors from the small bundled table while
        # doctor showed off the full one — the worse answer where it mattered.
        from ..intel import sig

        sig.load_external()

        self.sensors = build(self.config.sensors or None, **self.config.sensor_options)
        return [await s.probe() for s in self.sensors]

    async def start(self) -> None:
        if not self.sensors:
            await self.probe()
        for sensor in self.sensors:
            if sensor.status.available:
                self._tasks.append(asyncio.create_task(self._pump(sensor)))
        self._tasks.append(asyncio.create_task(self._consume()))

    async def shutdown(self) -> None:
        self.stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Rules run on a timer, so the last few seconds of a sweep would
        # otherwise never be judged — and a short sweep would be judged never.
        self.evaluate_all()
        self.persist()
        self.store.close()

    async def run_for(self, seconds: float | None = None) -> None:
        await self.start()
        try:
            if seconds is None:
                await self.stop.wait()
            else:
                await asyncio.wait_for(self.stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            await self.shutdown()

    # -- capture ---------------------------------------------------------

    async def _pump(self, sensor: Sensor) -> None:
        try:
            async for obs in sensor.run(self.stop):
                try:
                    self.queue.put_nowait(obs)
                except asyncio.QueueFull:
                    self.dropped += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sensor.status.errors += 1
            sensor.status.last_error = f"{type(exc).__name__}: {exc}"

    async def _consume(self) -> None:
        while not self.stop.is_set():
            try:
                obs = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                self._maybe_evaluate()
                continue
            except asyncio.CancelledError:
                raise
            self.handle(obs)
            self._maybe_evaluate()

    # -- processing ------------------------------------------------------

    def handle(self, obs: Observation) -> Device:
        device = self.fusion.ingest(obs)
        self.context.note_seen(device.id)

        if device.trust is Trust.UNSET:
            trust, label = self.store.get_trust(device.aliases)
            device.trust = trust
            if label and not device.label:
                device.label = label

        if self.config.log_raw:
            self.store.log_observation(self.session, obs)

        if self.range_target and self._is_target(device) and obs.rssi is not None:
            self.ranger.feed(obs.ts, obs.rssi)

        return device

    def _is_target(self, device: Device) -> bool:
        if not self.range_target:
            return False
        t = self.range_target.lower()
        return t == device.id or t in {a.lower() for a in device.aliases} or (
            t == device.address.lower()
        )

    def _maybe_evaluate(self) -> None:
        now = time.time()
        if now - self._last_eval >= self.config.evaluate_interval:
            self._last_eval = now
            self.evaluate_all()
        if now - self._last_persist >= self.config.persist_interval:
            self._last_persist = now
            self.persist()
            # Prune only after persisting, so an evicted device that returns is
            # a fresh object in memory but keeps its history and trust on disk.
            self.fusion.prune(now)

    def evaluate_all(self) -> list[tuple[Device, Finding]]:
        """Re-run rules over every present device. Returns findings that are new."""
        fresh: list[tuple[Device, Finding]] = []
        for device in self.fusion.present():
            previous = {(f.rule, f.severity) for f in device.findings}
            for finding in evaluate(device, self.context):
                if (finding.rule, finding.severity) not in previous:
                    fresh.append((device, finding))
                    for cb in self._on_finding:
                        cb(device, finding)
        return fresh

    def on_finding(self, callback: Callable[[Device, Finding], None]) -> None:
        self._on_finding.append(callback)

    def persist(self) -> None:
        for device in self.fusion.devices.values():
            self.store.save_device(device, self.session, self.context.epoch)
            if device.findings:
                self.store.save_findings(device.id, self.session, device.findings)
        self.store.commit()

    # -- operator actions ------------------------------------------------

    def mark_baseline(self) -> int:
        """Freeze the current device set as 'was already here'."""
        self.context.baseline_ids = set(self.fusion.devices)
        return len(self.context.baseline_ids)

    def new_epoch(self) -> int:
        """Tell the engine you have physically moved.

        This is what makes follow-detection possible; without it, 'nearby' and
        'following me' are the same observation.
        """
        return self.context.new_epoch()

    def set_trust(self, needle: str, trust: Trust, label: str | None = None) -> Device | None:
        device = self.fusion.get(needle)
        if device is None:
            return None
        device.trust = trust
        if label:
            device.label = label
        for alias in device.aliases:
            self.store.set_trust(alias, trust, label)
        evaluate(device, self.context)
        return device

    def target(self, needle: str | None) -> Device | None:
        """Start (or clear) ranging on a device."""
        self.ranger.reset()
        if needle is None:
            self.range_target = None
            return None
        device = self.fusion.get(needle)
        if device is None:
            return None
        self.range_target = device.id
        # Seed the ranger from history so the first reading is not blind.
        track = device.primary_track
        if track:
            for ts, rssi in track.history[-64:]:
                self.ranger.feed(ts, rssi)
        return device

    # -- reporting -------------------------------------------------------

    def snapshot(self, full: bool = True) -> dict[str, Any]:
        """The engine's state as one dict.

        `full=False` drops the raw hex payloads and attribute provenance from
        each device. The SSE stream pushes a snapshot twice a second per
        connected client, and in a dense environment the undecoded
        manufacturer-data blobs were most of the bytes — carried on every push
        for fields the web UI never renders. Reports and `/api/device/<id>`
        stay full.
        """
        devices = sorted(
            self.fusion.present(),
            key=lambda d: (-d.risk, -(d.rssi or -999)),
        )
        return {
            "session": self.session,
            "started": self.started,
            "elapsed_s": round(time.time() - self.started, 1),
            "epoch": self.context.epoch,
            "dropped_observations": self.dropped,
            "stats": self.fusion.stats(),
            "sensors": [
                {
                    "name": s.status.name, "band": s.status.band.value,
                    "available": s.status.available, "reason": s.status.reason,
                    "observations": s.status.observations, "errors": s.status.errors,
                    "last_error": s.status.last_error, "hint": s.status.hint,
                }
                for s in self.sensors
            ],
            "devices": [device_dict(d, self, full=full) for d in devices],
        }


#: Attribute prefixes that are raw captured bytes rather than decoded facts.
_RAW_ATTR_PREFIXES = ("mfr_data_", "svc_data_")


def device_dict(
    dev: Device, engine: Engine | None = None, full: bool = True
) -> dict[str, Any]:
    """Full detail for one device — the 'tell me everything' view."""
    tracks = []
    for (band, address), t in dev.tracks.items():
        tracks.append({
            "band": band,
            "address": address,
            "packets": t.count,
            "rssi": t.rssi_smoothed,
            "rssi_raw": t.rssi_raw,
            "rssi_min": t.rssi_min,
            "rssi_max": t.rssi_max,
            "first_seen": t.first_seen,
            "last_seen": t.last_seen,
        })

    out: dict[str, Any] = {
        "id": dev.id,
        "name": dev.display_name(),
        "label": dev.label,
        "class": dev.device_class.value,
        "trust": dev.trust.value,
        "risk": dev.risk,
        "vendor": dev.vendor,
        "model": dev.model,
        "os_hint": dev.os_hint,
        "bands": [b.value for b in dev.bands],
        "address": dev.address,
        "rssi": dev.rssi,
        "distance_m_estimate": dev.estimated_distance_m(),
        "first_seen": dev.first_seen,
        "last_seen": dev.last_seen,
        "age_s": round(dev.age, 1),
        "tracks": tracks,
        "attributes": dev.attrs if full else {
            k: v for k, v in dev.attrs.items()
            if not k.startswith(_RAW_ATTR_PREFIXES)
        },
        "attribute_sources": dev.attr_source if full else {},
        "findings": [
            {
                "rule": f.rule, "severity": f.severity,
                "severity_label": f.severity_label,
                "title": f.title, "detail": f.detail, "evidence": f.evidence,
            }
            for f in dev.findings
        ],
    }
    if engine is not None:
        out["identity_links"] = [
            {"reason": e.reason, "confidence": e.confidence,
             "previous_address": e.previous_address, "ts": e.ts}
            for e in engine.fusion.link_history(dev.id)
        ]
        out["epochs_seen"] = engine.context.epochs_for(dev.id)
        cache_key = (dev.id, dev.address)
        if cache_key not in engine._seen_before:
            engine._seen_before[cache_key] = engine.store.seen_before(
                dev.address, engine.session
            )
        out["seen_in_previous_sessions"] = engine._seen_before[cache_key]
    return out
