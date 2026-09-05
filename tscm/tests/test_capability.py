"""Capability catalogue and the QR encoder."""

from __future__ import annotations

import pytest

from sweep.core import capability, qr
from sweep.core.engine import Engine, EngineConfig
from sweep.core.models import Band
from sweep.sensors import ALL_SENSORS


# ---------------------------------------------------------------------------
# Catalogue integrity
# ---------------------------------------------------------------------------

def test_every_band_has_a_catalogue_entry():
    """A band with a sensor but no catalogue entry silently vanishes from
    Coverage, which is the one screen that must never under-report."""
    from sweep.sensors import REGISTRY

    sensor_bands = {cls.band.value for cls in REGISTRY.values()}
    documented = {b.band for b in capability.CATALOGUE}
    assert sensor_bands <= documented, f"undocumented bands: {sensor_bands - documented}"


def test_every_referenced_upgrade_exists():
    for band in capability.CATALOGUE:
        for upgrade_id in band.upgrades:
            assert upgrade_id in capability.UPGRADES, f"{band.band} -> {upgrade_id}"


def test_upgrades_only_claim_real_bands():
    valid = {b.value for b in Band}
    for upgrade in capability.UPGRADES.values():
        for band in upgrade.unlocks:
            assert band in valid, f"{upgrade.id} claims unknown band {band}"


def test_upgrade_actions_are_actionable_not_restatements():
    """`name` is the product, `action` is what to do. Conflating them produced
    "install install bluez" in the CLI."""
    for upgrade in capability.UPGRADES.values():
        assert upgrade.action and upgrade.action != upgrade.name
        if upgrade.kind == "software":
            assert upgrade.cost == "free"


def test_only_the_on_host_note_claims_the_viewer_can_sense():
    """A browser on the sensing machine really is the sensor; a remote one is
    never allowed to imply it is."""
    for key, note in capability.CLIENT_NOTES.items():
        assert note["headline"] and note["why"], key
        expected = key == "host_local"
        assert note["can_sense"] is expected, key


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------

async def test_assess_reports_every_band_even_when_unselected(tmp_path):
    """A band you did not enable is still a band you cannot see."""
    engine = Engine(EngineConfig(sensors=["ble"], db_path=str(tmp_path / "c.db")))
    await engine.probe()
    result = capability.assess(engine.sensors)
    engine.store.close()

    assert len(result["bands"]) == len(capability.CATALOGUE)
    statuses = {b["band"]: b["status"] for b in result["bands"]}
    assert statuses[Band.IR.value] == "off"
    assert "sensor not enabled" in next(
        b["reason"] for b in result["bands"] if b["band"] == Band.IR.value)


async def test_unavailable_bands_carry_upgrades_and_active_ones_do_not(tmp_path):
    engine = Engine(EngineConfig(sensors=list(ALL_SENSORS), db_path=str(tmp_path / "c.db")))
    await engine.probe()
    result = capability.assess(engine.sensors)
    engine.store.close()

    for band in result["bands"]:
        if band["status"] == "active":
            assert band["upgrades"] == []
        else:
            assert band["upgrades"], f"{band['band']} offers no way forward"
            for upgrade in band["upgrades"]:
                assert band["band"] in upgrade["unlocks"]


async def test_blind_spots_and_next_upgrade_are_consistent(tmp_path):
    engine = Engine(EngineConfig(sensors=list(ALL_SENSORS), db_path=str(tmp_path / "c.db")))
    await engine.probe()
    result = capability.assess(engine.sensors)
    engine.store.close()

    blind = {b["band"] for b in result["blind_spots"]}
    active = set(result["active_bands"])
    assert not (blind & active)
    assert result["active_count"] + len(blind) == result["total_count"]

    nxt = result["next_upgrade"]
    if blind:
        assert nxt is not None
        assert set(nxt["unlocks"]) & blind
        assert nxt["bands_gained"] >= 1


def test_next_upgrade_prefers_free_software_over_hardware():
    """Recommending a $40 dongle when a pip install would do loses trust."""
    missing = [
        {"band": Band.BLE.value}, {"band": Band.BT_CLASSIC.value},
        {"band": Band.ISM_SUB_GHZ.value}, {"band": Band.RF_BROADBAND.value},
    ]
    best = capability._best_next(missing)
    assert best is not None
    # bluez and rtlsdr both unlock two of these; the free one must win.
    assert best["kind"] == "software"
    assert best["cost"] == "free"


def test_no_blind_spots_means_no_next_upgrade():
    assert capability._best_next([]) is None


def test_host_info_is_populated():
    host = capability.host_info()
    for key in ("hostname", "system", "pretty", "machine", "python"):
        assert host[key]


def test_lan_addresses_excludes_loopback():
    for address in capability.lan_addresses():
        assert not address.startswith("127.")


# ---------------------------------------------------------------------------
# QR encoder
# ---------------------------------------------------------------------------

def test_qr_picks_the_smallest_version_that_fits():
    assert len(qr.encode("A", "L")) == 21              # version 1
    assert len(qr.encode("x" * 130, "L")) == 41        # version 6


def test_qr_rejects_payloads_that_do_not_fit():
    with pytest.raises(qr.QrError):
        qr.encode("x" * 500, "L")
    with pytest.raises(qr.QrError):
        qr.encode("hello", "H")


def test_qr_matrix_has_the_three_finder_patterns():
    m = qr.encode("http://192.168.1.42:8787/", "M")
    size = len(m)
    for row, col in ((0, 0), (0, size - 7), (size - 7, 0)):
        # Finder: 7x7 with a dark ring and a 3x3 dark centre.
        assert all(m[row][col + i] == 1 for i in range(7))
        assert m[row + 3][col + 3] == 1
        assert m[row + 1][col + 1] == 0


def test_qr_has_the_mandatory_dark_module():
    m = qr.encode("test", "M")
    assert m[len(m) - 8][8] == 1


def test_render_produces_lines_and_respects_no_color():
    plain = qr.render("http://10.0.0.5:8787/", color=False)
    assert "\033[" not in plain
    assert "██" in plain
    coloured = qr.render("http://10.0.0.5:8787/", color=True)
    assert "\033[" in coloured


# A real decode is the only test that proves a phone could scan this.
cv2 = pytest.importorskip("cv2", reason="opencv not installed")
np = pytest.importorskip("numpy")


@pytest.mark.parametrize("text,level", [
    ("A", "L"), ("A", "M"),
    ("http://192.168.1.42:8787/", "M"),
    ("http://192.168.1.42:8787/?t=MJQGMSlZCXSy4ee-EC9pxQ", "M"),
    ("http://192.168.1.42:8787/?t=MJQGMSlZCXSy4ee-EC9pxQ", "L"),
    ("x" * 100, "L"),
    ("x" * 130, "L"),
])
def test_generated_codes_decode_back_to_the_input(text, level):
    matrix = qr.encode(text, level)
    size = len(matrix)
    quiet, scale = 4, 8
    img = np.ones((size + quiet * 2, size + quiet * 2), dtype=np.uint8) * 255
    for r in range(size):
        for c in range(size):
            if matrix[r][c]:
                img[r + quiet, c + quiet] = 0
    big = np.kron(img, np.ones((scale, scale), dtype=np.uint8))
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(big)
    assert decoded == text
