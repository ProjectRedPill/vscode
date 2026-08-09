"""Sensor contract.

A sensor is an async generator of Observations plus a capability probe. It owns
its hardware and nothing else — no storage, no classification, no alerting. That
separation is what lets `sweep` run with whatever subset of hardware is actually
plugged in, degrading to "fewer bands" instead of failing.

Adding a band is: subclass Sensor, implement `available()` and `run()`, register
it in `sensors/__init__.py`. Nothing else in the tree changes.
"""

from __future__ import annotations

import abc
import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Band, Observation


@dataclass
class SensorStatus:
    name: str
    band: Band
    available: bool
    reason: str = ""
    hint: str = ""            # what the user should install/plug in
    needs_root: bool = False
    observations: int = 0
    errors: int = 0
    last_error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Sensor(abc.ABC):
    """Base class for every capture source."""

    name: str = "sensor"
    band: Band = Band.BLE
    #: Human-readable install hint shown when `available()` fails.
    hint: str = ""
    #: True when the sensor typically needs elevated privileges.
    needs_root: bool = False

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.status = SensorStatus(
            name=self.name, band=self.band, available=False,
            hint=self.hint, needs_root=self.needs_root,
        )

    # -- lifecycle -------------------------------------------------------

    @abc.abstractmethod
    async def available(self) -> tuple[bool, str]:
        """Return (usable, reason). Must not raise and must be quick."""

    @abc.abstractmethod
    def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        """Yield Observations until `stop` is set."""

    async def probe(self) -> SensorStatus:
        try:
            ok, reason = await self.available()
        except Exception as exc:  # a missing library must not be fatal
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        self.status.available = ok
        self.status.reason = reason
        return self.status

    # -- helpers shared by subprocess-backed sensors ---------------------

    def _count(self) -> None:
        self.status.observations += 1

    def _fail(self, exc: BaseException | str) -> None:
        self.status.errors += 1
        self.status.last_error = str(exc)

    @staticmethod
    def which(binary: str) -> str | None:
        return shutil.which(binary)

    @staticmethod
    async def run_cmd(argv: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
        """Run a command, capture output. Returns (rc, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            return 127, "", str(exc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", "timed out"
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def stream_cmd(
        self, argv: list[str], stop: asyncio.Event
    ) -> AsyncIterator[str]:
        """Yield stdout lines from a long-running command until `stop`."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError) as exc:
            self._fail(exc)
            return

        assert proc.stdout is not None
        try:
            while not stop.is_set():
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                yield line.decode("utf-8", "replace").rstrip("\n")
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    proc.kill()


class NullSensor(Sensor):
    """Placeholder for a band with no working backend, so the UI can say why."""

    def __init__(self, name: str, band: Band, reason: str, hint: str = "") -> None:
        self.name = name
        self.band = band
        self.hint = hint
        super().__init__()
        self._reason = reason

    async def available(self) -> tuple[bool, str]:
        return False, self._reason

    async def run(self, stop: asyncio.Event) -> AsyncIterator[Observation]:
        return
        yield  # pragma: no cover - makes this an async generator
