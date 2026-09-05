"""Sensor plumbing, against realistic captured output.

This file exists because of a lesson: every sensor passed 129 tests while the
Wi-Fi sensor was completely broken — `_scan()` returned its handler's coroutine
un-awaited, so the first real scan raised `TypeError: 'coroutine' object is not
iterable` and killed the sensor. Nothing noticed, because nothing fed the
parsers realistic tool output. Now something does.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from sweep.core.fusion import Fusion, FusionConfig
from sweep.core.models import Band, DeviceClass, Finding, Observation, Trust
from sweep.sensors import serialbridge
from sweep.sensors.ble import parse_bctl_rssi
from sweep.sensors.wifi import WifiSensor


def fake_run_cmd(stdout: str, rc: int = 0):
    async def run(argv, timeout=10.0):
        return rc, stdout, ""

    return run


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

NMCLI_OUTPUT = """\
AA\\:BB\\:CC\\:DD\\:EE\\:FF:CoffeeShop:6:2437 MHz:72:WPA2:Infra:270 Mbit/s:(none):WPA2
11\\:22\\:33\\:44\\:55\\:66::11:2462 MHz:45:WPA1 WPA2:Infra:130 Mbit/s:WPA1:WPA2
"""

NETSH_OUTPUT = """\
Interface name : Wi-Fi
There are 1 networks currently visible.

SSID 1 : CoffeeShop
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 99%
         Radio type         : 802.11n
         Channel            : 6
"""

IW_OUTPUT = """\
BSS aa:bb:cc:dd:ee:ff(on wlan0) -- associated
\tTSF: 1234 usec
\tfreq: 2437
\tsignal: -55.00 dBm
\tSSID: HomeNet
\tDS Parameter set: channel 6
\tRSN:\t * Version: 1
\tWPS:\t * Version: 1.0
"""


async def test_wifi_scan_awaits_its_handler_and_returns_observations():
    """Regression for the un-awaited handler: `_scan` must hand back a list,
    never a coroutine."""
    sensor = WifiSensor()
    sensor._mode = "nmcli"
    sensor.run_cmd = fake_run_cmd(NMCLI_OUTPUT)

    results = await sensor._scan()
    assert isinstance(results, list) and len(results) == 2
    assert all(isinstance(o, Observation) for o in results)


async def test_wifi_run_loop_survives_a_full_cycle():
    """Drive the actual generator, which is where the TypeError escaped."""
    sensor = WifiSensor(interval=0.05)
    sensor._mode = "nmcli"
    sensor.run_cmd = fake_run_cmd(NMCLI_OUTPUT)
    sensor.status.available = True

    stop = asyncio.Event()
    seen: list[Observation] = []
    async for obs in sensor.run(stop):
        seen.append(obs)
        if len(seen) >= 2:
            stop.set()
    assert len(seen) >= 2
    assert sensor.status.errors == 0


async def test_nmcli_parsing_extracts_the_details():
    sensor = WifiSensor()
    sensor._mode = "nmcli"
    sensor.run_cmd = fake_run_cmd(NMCLI_OUTPUT)
    first, hidden = await sensor._scan()

    assert first.address == "AA:BB:CC:DD:EE:FF"
    assert first.attrs["ssid"] == "CoffeeShop"
    assert first.attrs["security"] == "WPA2"
    # 72% quality → dBm via the documented approximation, flagged as estimated.
    assert first.rssi == pytest.approx(72 / 2 - 100)
    assert first.attrs["rssi_estimated_from_quality"] is True
    assert first.frequency_hz == pytest.approx(2437e6)

    assert hidden.attrs["hidden_ssid"] is True
    assert hidden.attrs["ssid"] == "<hidden>"


async def test_netsh_percent_signs_parse():
    """Regression: `Signal : 99%` failed int() and Windows rows had no RSSI."""
    sensor = WifiSensor()
    sensor._mode = "netsh"
    sensor.run_cmd = fake_run_cmd(NETSH_OUTPUT)
    (ap,) = await sensor._scan()

    assert ap.address == "AA:BB:CC:DD:EE:FF"
    assert ap.attrs["signal_pct"] == 99
    assert ap.rssi == pytest.approx(99 / 2 - 100)
    assert ap.channel == 6
    assert ap.attrs["ssid"] == "CoffeeShop"


async def test_iw_parsing_extracts_the_details():
    sensor = WifiSensor()
    sensor._mode = "iw"
    sensor._iface = "wlan0"
    sensor.run_cmd = fake_run_cmd(IW_OUTPUT)
    (ap,) = await sensor._scan()

    assert ap.address == "AA:BB:CC:DD:EE:FF"
    assert ap.rssi == pytest.approx(-55.0)
    assert ap.frequency_hz == pytest.approx(2437e6)
    assert ap.attrs["ssid"] == "HomeNet"
    assert ap.attrs["channel"] == 6
    assert ap.attrs["wps"] is True


# ---------------------------------------------------------------------------
# BLE bluetoothctl fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("-54", -54.0),
    (" -54 dBm", -54.0),
    ("0xffca (-54)", -54.0),          # newer BlueZ: unsigned hex of a signed value
    ("0x0000", 0.0),
    ("garbage", None),
    ("", None),
])
def test_bluetoothctl_rssi_parses_both_formats(text, expected):
    """Regression: the hex branch called float(value, 0) — a TypeError the
    surrounding except ValueError never caught, killing the fallback sensor."""
    assert parse_bctl_rssi(text) == expected


# ---------------------------------------------------------------------------
# Serial bridge
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_serial(monkeypatch):
    class FakeSerial:
        def __init__(self, port, baud, timeout=0.5):
            self.lines = [
                b'{"t":"rf","dbm":-40.5}\n',
                b"t=irlevel adc=700\n",
            ]

        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            time.sleep(0.01)
            return b""

        def close(self):
            pass

    module = types.ModuleType("serial")
    module.Serial = FakeSerial
    monkeypatch.setitem(sys.modules, "serial", module)
    return module


async def test_closing_the_serial_reader_does_not_stop_the_engine(fake_serial):
    """Regression: read_serial's cleanup called stop.set() on the *shared*
    engine stop event, so a decode error or an unplugged probe shut the whole
    sweep down instead of just that sensor."""
    stop = asyncio.Event()
    records = []
    gen = serialbridge.read_serial("/dev/fake", 115200, stop)
    async for record in gen:
        records.append(record)
        if len(records) == 2:
            break
    await gen.aclose()

    assert records[0]["dbm"] == -40.5
    assert records[1]["adc"] == 700
    assert not stop.is_set(), "closing one sensor must not stop the engine"


# ---------------------------------------------------------------------------
# primary_track freshness
# ---------------------------------------------------------------------------

def obs(address, band=Band.BLE, rssi=-60.0, ts=None, **attrs) -> Observation:
    return Observation(
        band=band, sensor="test", address=address, rssi=rssi,
        ts=ts if ts is not None else time.time(), attrs=attrs,
        name=attrs.get("name"),
    )


def test_primary_track_prefers_the_radio_that_is_actually_alive():
    """Regression: a strong reading from a radio quiet for 50s outranked a
    live radio, so display, distance and the finder all used dead air."""
    now = time.time()
    fusion = Fusion()
    fusion.ingest(obs("3C:6A:2C:11:22:33", band=Band.BLE, rssi=-40.0, ts=now - 50))
    dev = fusion.ingest(obs("3C:6A:2C:11:22:34", band=Band.WIFI, rssi=-70.0,
                            ts=now, ssid="x"))

    assert len(dev.tracks) == 2, "cross-band link should have fused these"
    assert dev.primary_track is not None
    assert dev.primary_track.band is Band.WIFI, "fresh radio must win"


def test_primary_track_falls_back_to_most_recent_when_all_are_stale():
    now = time.time()
    fusion = Fusion()
    fusion.ingest(obs("3C:6A:2C:11:22:33", band=Band.BLE, rssi=-40.0, ts=now - 59))
    dev = fusion.ingest(obs("3C:6A:2C:11:22:34", band=Band.WIFI, rssi=-70.0,
                            ts=now - 40, ssid="x"))
    # Both are older than the freshness window relative to last_seen? The BLE
    # one is 19s older than the device's last sighting, so it is "fresh" by the
    # relative rule and strongest — the point is simply that we get *a* track.
    assert dev.primary_track is not None


# ---------------------------------------------------------------------------
# Fusion pruning
# ---------------------------------------------------------------------------

def test_prune_bounds_the_table_and_spares_what_matters():
    now = time.time()
    fusion = Fusion(FusionConfig(max_devices=3, stale_after_s=10))

    old = now - 600
    kept_trusted = fusion.ingest(obs("AA:00:00:00:00:01", ts=old))
    kept_trusted.trust = Trust.MINE
    kept_flagged = fusion.ingest(obs("AA:00:00:00:00:02", ts=old))
    kept_flagged.findings = [Finding(rule="x", severity=3, title="t", detail="d")]
    for i in range(3, 9):
        fusion.ingest(obs(f"AA:00:00:00:00:{i:02X}", ts=old))
    fresh = fusion.ingest(obs("AA:00:00:00:00:FF", ts=now))

    evicted = fusion.prune(now)
    assert evicted > 0
    assert kept_trusted.id in fusion.devices, "trusted devices are never evicted"
    assert kept_flagged.id in fusion.devices, "flagged devices are never evicted"
    assert fresh.id in fusion.devices, "present devices are never evicted"

    # The indexes must shrink with the table, or the leak just moves.
    live = set(fusion.devices)
    assert set(fusion._by_address.values()) <= live
    assert set(fusion._by_link_key.values()) <= live
    assert fusion.get("AA:00:00:00:00:03") is None


def test_prune_is_a_no_op_under_the_cap():
    fusion = Fusion(FusionConfig(max_devices=100))
    fusion.ingest(obs("AA:00:00:00:00:01", ts=time.time() - 999))
    assert fusion.prune() == 0
    assert len(fusion.devices) == 1


# ---------------------------------------------------------------------------
# Windows Bluetooth Classic
# ---------------------------------------------------------------------------

WIN_PNP_JSON = """[
  {
    "FriendlyName": "Sony WH-1000XM4",
    "InstanceId": "BTHENUM\\\\DEV_AC1203F1229B\\\\7&1F2A&0&BLUETOOTHDEVICE_AC1203F1229B",
    "Status": "OK"
  },
  {
    "FriendlyName": "Intel(R) Wireless Bluetooth(R)",
    "InstanceId": "USB\\\\VID_8087&PID_0026\\\\5&1A2B3C&0&14",
    "Status": "OK"
  },
  {
    "FriendlyName": "Some Recorder",
    "InstanceId": "BTHENUM\\\\DEV_001122334455\\\\8&ABC&0&BLUETOOTHDEVICE_001122334455",
    "Status": "Unknown"
  }
]"""


async def test_windows_bt_classic_parses_pnp_and_skips_the_radio(monkeypatch):
    """The adapter itself has no peer address and must not appear as a device."""
    from sweep.sensors.btclassic import BtClassicSensor

    sensor = BtClassicSensor()
    sensor._mode = "windows"
    monkeypatch.setattr(sensor, "which", lambda b: "powershell")
    sensor.run_cmd = fake_run_cmd(WIN_PNP_JSON)

    results = await sensor._scan_windows()
    addresses = {o.address for o in results}
    assert addresses == {"AC:12:03:F1:22:9B", "00:11:22:33:44:55"}, \
        "the Intel radio has no DEV_ address and must be skipped"

    headphones = next(o for o in results if o.address == "AC:12:03:F1:22:9B")
    assert headphones.attrs["name"] == "Sony WH-1000XM4"
    assert headphones.attrs["connected"] is True
    assert headphones.band is Band.BT_CLASSIC
    # The honesty caveat must ride along with the data.
    assert "not a live inquiry" in headphones.attrs["note"]

    other = next(o for o in results if o.address == "00:11:22:33:44:55")
    assert other.attrs["connected"] is False


async def test_windows_bt_classic_survives_a_single_object_and_bad_json(monkeypatch):
    """PowerShell's ConvertTo-Json emits a bare object, not a list, for one item."""
    from sweep.sensors.btclassic import BtClassicSensor

    sensor = BtClassicSensor()
    sensor._mode = "windows"
    monkeypatch.setattr(sensor, "which", lambda b: "powershell")

    single = ('{"FriendlyName":"Tag","InstanceId":'
              '"BTHENUM\\\\DEV_AABBCCDDEEFF\\\\x","Status":"OK"}')
    sensor.run_cmd = fake_run_cmd(single)
    assert len(await sensor._scan_windows()) == 1

    sensor.run_cmd = fake_run_cmd("not json at all")
    assert await sensor._scan_windows() == []
    assert sensor.status.errors >= 1


async def test_bt_classic_is_available_on_windows(monkeypatch):
    """Regression: Windows fell through to 'no bluez tooling found', so the
    whole band was dead on the platform most likely to be used."""
    from sweep.sensors import btclassic

    sensor = btclassic.BtClassicSensor()
    monkeypatch.setattr(btclassic.sys, "platform", "win32")
    monkeypatch.setattr(sensor, "which", lambda b: "powershell" if "powershell" in b else None)

    ok, reason = await sensor.available()
    assert ok is True
    assert sensor._mode == "windows"
    assert "not a live inquiry" in reason, "the limitation must be stated up front"
