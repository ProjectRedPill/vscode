"""Fusion, ranging, rules and storage."""

from __future__ import annotations

import time

import pytest

from sweep.core.fusion import Fusion, FusionConfig
from sweep.core.models import Band, DeviceClass, Observation, Trust
from sweep.core.rssi import Heat, KalmanRssi, Ranger
from sweep.core.store import Store
from sweep.threat import SweepContext, evaluate


def obs(address, band=Band.BLE, rssi=-60.0, ts=None, **attrs) -> Observation:
    return Observation(
        band=band, sensor="test", address=address, rssi=rssi,
        ts=ts if ts is not None else time.time(), attrs=attrs,
        name=attrs.get("name"),
        address_is_random=attrs.get("rotating", False),
    )


# ---------------------------------------------------------------------------
# RSSI conditioning
# ---------------------------------------------------------------------------

def test_kalman_suppresses_noise_but_tracks_the_mean():
    f = KalmanRssi()
    for value in [-60, -75, -45, -62, -58, -61, -59, -60, -60, -61]:
        f.update(value)
    # The filtered value must sit near the true mean, not chase the outliers.
    assert -65 < (f.value or 0) < -55


def test_kalman_eventually_follows_a_real_step():
    f = KalmanRssi()
    for _ in range(40):
        f.update(-80.0)
    assert f.value == pytest.approx(-80, abs=2)
    for _ in range(60):
        f.update(-50.0)
    assert f.value == pytest.approx(-50, abs=4)


def test_ranger_calibrates_before_it_judges():
    r = Ranger()
    now = time.time()
    r.feed(now, -70.0)
    assert r.read(now).heat is Heat.CALIBRATING


def test_ranger_reports_warmer_when_the_signal_rises():
    r = Ranger()
    now = time.time()
    # Baseline: 20s of -80 dBm ending 4s ago.
    for i in range(40):
        r.feed(now - 24 + i * 0.5, -80.0)
    # Recent: the last 3s at -65 dBm.
    for i in range(12):
        r.feed(now - 2.5 + i * 0.2, -65.0)

    reading = r.read(now)
    assert reading.heat is Heat.HOT
    assert reading.delta_db > 8
    assert reading.distance_ratio < 1.0   # closer than baseline


def test_ranger_reports_cooler_when_the_signal_falls():
    r = Ranger()
    now = time.time()
    for i in range(40):
        r.feed(now - 24 + i * 0.5, -60.0)
    for i in range(12):
        r.feed(now - 2.5 + i * 0.2, -78.0)

    reading = r.read(now)
    assert reading.heat is Heat.COLD
    assert reading.distance_ratio > 1.0


def test_ranger_reports_lost_when_packets_stop():
    r = Ranger()
    now = time.time()
    for i in range(20):
        r.feed(now - 60 + i, -70.0)
    assert r.read(now).heat is Heat.LOST


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def test_same_address_fuses_into_one_device():
    f = Fusion()
    a = f.ingest(obs("AA:BB:CC:DD:EE:FF"))
    b = f.ingest(obs("AA:BB:CC:DD:EE:FF", rssi=-55.0))
    assert a.id == b.id
    assert a.tracks[(Band.BLE.value, "aa:bb:cc:dd:ee:ff")].count == 2


def test_rotated_address_relinks_via_a_static_tile_id():
    f = Fusion()
    first = f.ingest(obs("42:11:22:33:44:55", tile_id="deadbeef", rotating=True))
    second = f.ingest(obs("46:99:88:77:66:55", tile_id="deadbeef", rotating=True))

    assert first.id == second.id
    assert second.attrs["rotation_detected"] is True
    links = f.link_history(second.id)
    assert links and "static identifier" in links[0].reason


def test_unrelated_devices_do_not_get_merged():
    f = Fusion()
    a = f.ingest(obs("42:11:22:33:44:55", tile_id="aaaa", rotating=True))
    b = f.ingest(obs("46:99:88:77:66:55", tile_id="bbbb", rotating=True))
    assert a.id != b.id


def test_rotation_link_expires_outside_the_window():
    f = Fusion(FusionConfig(rotation_window_s=10.0))
    old = time.time() - 600
    a = f.ingest(obs("42:11:22:33:44:55", ts=old, tile_id="deadbeef", rotating=True))
    b = f.ingest(obs("46:99:88:77:66:55", tile_id="deadbeef", rotating=True))
    assert a.id != b.id


def test_adjacent_macs_link_across_bands():
    f = Fusion()
    ble_dev = f.ingest(obs("3C:6A:2C:11:22:33", band=Band.BLE))
    wifi_dev = f.ingest(obs("3C:6A:2C:11:22:34", band=Band.WIFI, ssid="test"))

    assert ble_dev.id == wifi_dev.id
    assert wifi_dev.attrs["multi_radio"] is True
    assert {Band.BLE, Band.WIFI} == set(wifi_dev.bands)


def test_distant_macs_in_the_same_oui_do_not_link():
    f = Fusion()
    a = f.ingest(obs("3C:6A:2C:11:22:33", band=Band.BLE))
    b = f.ingest(obs("3C:6A:2C:99:88:77", band=Band.WIFI, ssid="test"))
    assert a.id != b.id


def test_classification_survives_a_sparse_follow_up_packet():
    f = Fusion()
    f.ingest(obs("AA:BB:CC:00:11:22", device_class="camera", name="IPC-Front"))
    dev = f.ingest(obs("AA:BB:CC:00:11:22"))   # bare packet, no attributes
    assert dev.device_class is DeviceClass.CAMERA


def test_primary_track_follows_the_strongest_radio():
    f = Fusion()
    f.ingest(obs("3C:6A:2C:11:22:33", band=Band.BLE, rssi=-90.0))
    dev = f.ingest(obs("3C:6A:2C:11:22:34", band=Band.WIFI, rssi=-40.0, ssid="x"))
    assert dev.primary_track is not None
    assert dev.primary_track.band is Band.WIFI


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def test_separated_tracker_is_medium_not_critical_on_its_own():
    f = Fusion()
    dev = f.ingest(obs("42:11:22:33:44:55", device_class="tracker",
                       find_my_separated=True, tracker_network="Apple Find My"))
    findings = evaluate(dev, SweepContext())
    sep = [x for x in findings if x.rule == "tracker.separated"]
    assert sep and sep[0].severity == 2


def test_tracker_following_across_locations_is_critical():
    f = Fusion()
    ctx = SweepContext()
    dev = f.ingest(obs("42:11:22:33:44:55", device_class="tracker",
                       find_my_separated=True, tracker_network="Apple Find My"))
    for _ in range(3):
        ctx.note_seen(dev.id)
        ctx.new_epoch()

    findings = evaluate(dev, ctx)
    follow = [x for x in findings if x.rule == "tracker.following"]
    assert follow and follow[0].severity == 4
    assert follow[0].evidence["epochs"] >= 3


def test_marking_a_device_as_mine_silences_its_alerts():
    f = Fusion()
    ctx = SweepContext()
    dev = f.ingest(obs("42:11:22:33:44:55", device_class="tracker",
                       find_my_separated=True, tracker_network="Apple Find My"))
    for _ in range(4):
        ctx.note_seen(dev.id)
        ctx.new_epoch()

    evaluate(dev, ctx)
    assert dev.risk == 4

    dev.trust = Trust.MINE
    evaluate(dev, ctx)
    assert dev.risk == 0
    # The findings are kept for the record, only their urgency is removed.
    assert dev.findings


def test_unnamed_close_device_is_flagged():
    f = Fusion()
    dev = f.ingest(obs("C2:00:11:22:33:44", rssi=-38.0))
    findings = evaluate(dev, SweepContext())
    assert any(x.rule == "device.unnamed-close" for x in findings)


def test_a_named_close_device_is_not_flagged_as_unnamed():
    f = Fusion()
    dev = f.ingest(obs("C2:00:11:22:33:44", rssi=-38.0, name="Ricardo's iPhone"))
    findings = evaluate(dev, SweepContext())
    assert not any(x.rule == "device.unnamed-close" for x in findings)


def test_hidden_ssid_only_fires_when_close():
    f = Fusion()
    far = f.ingest(obs("00:11:22:33:44:55", band=Band.WIFI, rssi=-80.0,
                       hidden_ssid=True, ssid="<hidden>"))
    assert not any(x.rule == "wifi.hidden-ssid-close" for x in evaluate(far, SweepContext()))

    near = f.ingest(obs("00:11:22:33:44:66", band=Band.WIFI, rssi=-40.0,
                        hidden_ssid=True, ssid="<hidden>"))
    assert any(x.rule == "wifi.hidden-ssid-close" for x in evaluate(near, SweepContext()))


def test_ir_flood_is_critical():
    f = Fusion()
    dev = f.ingest(obs("ir:flood", band=Band.IR, rssi=-40.0,
                       ir_flood=True, ir_level_adc=880, ir_sustained_s=12))
    findings = evaluate(dev, SweepContext())
    flood = [x for x in findings if x.rule == "ir.illuminator"]
    assert flood and flood[0].severity == 4


def test_a_broken_rule_cannot_stop_evaluation():
    from sweep.threat import rules as rules_module

    def exploding_rule(dev, ctx):
        raise RuntimeError("boom")

    rules_module.RULES.append(("test.explodes", exploding_rule))
    try:
        f = Fusion()
        dev = f.ingest(obs("C2:00:11:22:33:44", rssi=-38.0))
        assert evaluate(dev, SweepContext())    # other rules still fired
    finally:
        rules_module.RULES.pop()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_trust_round_trips(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.set_trust("ble:aa:bb:cc:dd:ee:ff", Trust.MINE, "my phone")
        trust, label = store.get_trust(["ble:AA:BB:CC:DD:EE:FF"])
        assert trust is Trust.MINE
        assert label == "my phone"


def test_strongest_disposition_wins_across_aliases(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.set_trust("ble:a", Trust.KNOWN)
        store.set_trust("wifi:b", Trust.BLOCKED)
        trust, _ = store.get_trust(["ble:a", "wifi:b"])
        assert trust is Trust.BLOCKED


def test_sightings_and_history_persist(tmp_path):
    from sweep.core.fusion import Fusion as F

    with Store(tmp_path / "t.db") as store:
        f = F()
        dev = f.ingest(obs("AA:BB:CC:DD:EE:FF", name="thing"))
        store.save_device(dev, "sess1", 0)
        store.commit()

        assert store.history_for_address("AA:BB:CC:DD:EE:FF")
        assert store.seen_before("AA:BB:CC:DD:EE:FF", "sess2") == 1
        assert store.seen_before("AA:BB:CC:DD:EE:FF", "sess1") == 0


def test_attrs_with_unserialisable_values_still_save(tmp_path):
    from sweep.core.fusion import Fusion as F

    with Store(tmp_path / "t.db") as store:
        f = F()
        dev = f.ingest(obs("AA:BB:CC:DD:EE:FF"))
        dev.attrs["blob"] = b"\x00\x01"
        dev.attrs["set"] = {1, 2}
        store.save_device(dev, "s", 0)
        store.commit()


def test_decoder_class_hint_survives_fusion():
    """Regression: fusion used to drop `device_class` entirely, so every
    vendor decoder's self-reported class (Microsoft CDP form factor, Fast Pair
    audio, rtl_433 sensor, IR camera) was silently discarded."""
    f = Fusion()
    dev = f.ingest(obs("AA:BB:CC:11:22:33", band=Band.IR, device_class="camera",
                       ir_flood=True))
    assert dev.attrs["class_hint"] == "camera"
    assert dev.device_class is DeviceClass.CAMERA
    # The resolved class must never be readable off the raw attribute bag.
    assert "device_class" not in dev.attrs
