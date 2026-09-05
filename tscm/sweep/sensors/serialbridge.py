"""Shared serial line protocol for external probe hardware.

Consumer laptops have no infrared receiver and no broadband RF power detector,
so those two bands need a small external board. Rather than invent a binary
protocol, the bridge accepts newline-delimited JSON *or* a `KEY=value` form, so
a twenty-line Arduino sketch can feed it.

Accepted, one record per line:

    {"t":"ir","proto":"NEC","addr":"0x04","cmd":"0x08","repeat":false}
    {"t":"irlevel","adc":812,"mv":655}
    {"t":"rf","dbm":-42.5,"freq":2450000000}

    t=ir proto=NEC addr=0x04 cmd=0x08
    t=irlevel adc=812
    t=rf dbm=-42.5

The reference firmware for both probes lives in `firmware/` at the repo root.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


def parse_line(line: str) -> dict[str, Any] | None:
    """Parse one probe record. Returns None for noise, never raises."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if line.startswith("{"):
        try:
            record = json.loads(line)
            return record if isinstance(record, dict) else None
        except json.JSONDecodeError:
            return None

    if "=" not in line:
        return None

    record: dict[str, Any] = {}
    for token in line.split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        record[key.strip()] = _coerce(value.strip())
    return record or None


def _coerce(value: str) -> Any:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value, 0) if value.lower().startswith("0x") else int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


async def read_serial(
    port: str, baud: int, stop: asyncio.Event
) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed records from a serial port.

    Uses pyserial in a thread — pyserial has no asyncio API on all platforms and
    a thread is simpler and more portable than pyserial-asyncio here.
    """
    try:
        import serial  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("pyserial is required for serial probes: pip install pyserial")

    import threading

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1024)
    handle = serial.Serial(port, baud, timeout=0.5)
    # The pump thread's own shutdown flag. It must NOT be the shared `stop`:
    # that event belongs to the whole engine, and setting it from this
    # generator's cleanup meant a decode error or an unplugged probe shut the
    # entire sweep down instead of just this one sensor.
    done = threading.Event()

    def pump() -> None:
        while not (stop.is_set() or done.is_set()):
            try:
                raw = handle.readline()
            except Exception:
                break
            if not raw:
                continue
            text = raw.decode("utf-8", "replace")
            try:
                loop.call_soon_threadsafe(queue.put_nowait, text)
            except (asyncio.QueueFull, RuntimeError):
                pass
        try:
            handle.close()
        except Exception:
            pass

    task = loop.run_in_executor(None, pump)
    try:
        while not stop.is_set():
            try:
                line = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            record = parse_line(line)
            if record:
                yield record
    finally:
        done.set()
        try:
            await task
        except Exception:
            pass


async def read_file(path: str, stop: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
    """Tail a file or FIFO of the same protocol.

    Lets a probe be fed by anything that can write lines — a socat bridge, a
    remote ESP32 over MQTT piped to a FIFO, or a recorded capture for testing.
    """
    loop = asyncio.get_running_loop()
    handle = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
    try:
        while not stop.is_set():
            line = await loop.run_in_executor(None, handle.readline)
            if not line:
                await asyncio.sleep(0.2)
                continue
            record = parse_line(line)
            if record:
                yield record
    finally:
        handle.close()
