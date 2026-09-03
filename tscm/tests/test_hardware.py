"""Hardware capability probe, against real captured tool output.

Written fixture-first on purpose. The Wi-Fi sensor shipped completely broken
because its parsers had never seen real `nmcli` output; these parsers get real
`hciconfig`, `iw list` and `netsh` output before they are trusted with anything.
"""

from __future__ import annotations

import pytest

from sweep.core import hardware

# ---------------------------------------------------------------------------
# Captured from real machines
# ---------------------------------------------------------------------------

HCICONFIG_BT52 = """\
hci0:	Type: Primary  Bus: USB
	BD Address: AC:12:03:F1:22:9B  ACL MTU: 1021:4  SCO MTU: 96:6
	UP RUNNING
	RX bytes:11934 acl:0 sco:0 events:1653 errors:0
	TX bytes:44031 acl:0 sco:0 commands:1614 errors:0
	Features: 0xbf 0xfe 0xcf 0xfe 0xdb 0xff 0x7b 0x87
	Packet type: DM1 DM3 DM5 DH1 DH3 DH5 HV1 HV2 HV3
	Link mode: SLAVE ACCEPT
	Name: 'thinkpad'
	Class: 0x6c010c
	Service Classes: Rendering, Capturing, Object Transfer, Audio
	Device Class: Computer, Laptop
	HCI Version: 5.2 (0xb)  Revision: 0x8723
	LMP Version: 5.2 (0xb)  Subversion: 0x8723
	Manufacturer: Realtek Semiconductor Corporation (93)
"""

HCICONFIG_BT42 = HCICONFIG_BT52.replace("HCI Version: 5.2 (0xb)",
                                        "HCI Version: 4.2 (0x8)")

IW_LIST_MONITOR = """\
Wiphy phy0
	max # scan SSIDs: 4
	Band 1:
		Capabilities: 0x1062
		Frequencies:
			* 2412 MHz [1] (20.0 dBm)
	Band 2:
		Capabilities: 0x1062
		Frequencies:
			* 5180 MHz [36] (20.0 dBm)
	Supported interface modes:
		 * IBSS
		 * managed
		 * AP
		 * P2P-client
		 * monitor
	software interface modes (can always be added):
		 * monitor
"""

IW_LIST_NO_MONITOR = """\
Wiphy phy0
	Band 1:
		Frequencies:
			* 2412 MHz [1] (20.0 dBm)
	Supported interface modes:
		 * managed
		 * AP
"""

IW_LIST_6E = IW_LIST_MONITOR.replace(
    "\tSupported interface modes:",
    "\tBand 4:\n\t\tFrequencies:\n\t\t\t* 5955 MHz [1]\n\tSupported interface modes:",
)

NETSH_DRIVERS_MONITOR = """\
Interface name: Wi-Fi
    Driver                    : Intel(R) Wi-Fi 6 AX201 160MHz
    Vendor                    : Intel Corporation
    Radio types supported     : 802.11b 802.11g 802.11n 802.11a 802.11ac 802.11ax
    FIPS 140-2 mode supported : Yes
    Network monitor mode supported : Yes
    Number of supported PHYs  : 4
"""

NETSH_DRIVERS_NO_MONITOR = NETSH_DRIVERS_MONITOR.replace(
    "Network monitor mode supported : Yes",
    "Network monitor mode supported : No",
)


@pytest.fixture
def fake_run(monkeypatch):
    """Route hardware._run through a table of canned outputs."""
    table: dict[str, str] = {}

    def run(argv, timeout=8.0):
        return table.get(argv[0], "")

    monkeypatch.setattr(hardware, "_run", run)
    return table


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------

def test_bluetooth_5_reports_extended_advertising_as_unused(fake_run, monkeypatch):
    fake_run["hciconfig"] = HCICONFIG_BT52
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    info, findings = hardware.probe_bluetooth()
    assert info["spec_version"] == "5.2"
    assert info["address"] == "AC:12:03:F1:22:9B"
    assert "Realtek" in info["manufacturer"]
    assert info["adapters"] == ["hci0"]

    details = " ".join(f.detail for f in findings)
    assert "Extended Advertising" in details
    assert "Coded PHY" in details
    # The whole point: these must be flagged as available-but-unused.
    unused = [f for f in findings if f.exploited is False]
    assert len(unused) == 2
    assert any("blind spot" in f.note for f in unused)


def test_pre_bluetooth_5_does_not_claim_unused_capability(fake_run, monkeypatch):
    fake_run["hciconfig"] = HCICONFIG_BT42
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    info, findings = hardware.probe_bluetooth()
    assert info["spec_version"] == "4.2"
    assert [f for f in findings if f.exploited is False] == []
    assert any("USB Bluetooth 5 adapter" in f.note for f in findings)


def test_missing_bluetooth_is_reported_not_crashed(fake_run, monkeypatch):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    info, findings = hardware.probe_bluetooth()
    assert info == {}
    assert any("No Bluetooth adapter" in f.detail for f in findings)


@pytest.mark.parametrize("spec,expect_unused", [
    ("5.0", 2), ("5.3", 2), ("4.0", 0), ("4.2", 0),
])
def test_version_findings_track_the_spec_level(spec, expect_unused):
    findings = hardware._bt_version_findings(spec)
    assert len([f for f in findings if f.exploited is False]) == expect_unused


def test_unparseable_version_yields_no_claims():
    assert hardware._bt_version_findings("unknown") == []
    assert hardware._bt_version_findings(None) == []


def test_lmp_version_table_covers_the_modern_range():
    # 9 -> 5.0 is the boundary that drives every "unused capability" claim.
    assert hardware.LMP_VERSION[9] == "5.0"
    assert hardware.LMP_VERSION[11] == "5.2"


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

def test_monitor_mode_is_detected_and_flagged_unused(fake_run, monkeypatch):
    fake_run["iw"] = IW_LIST_MONITOR
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    info, findings = hardware.probe_wifi()
    assert info["monitor_mode"] is True
    assert "monitor" in info["interface_modes"]
    assert info["bands"] == ["2.4 GHz", "5 GHz"]

    unused = [f for f in findings if f.exploited is False]
    assert any("monitor mode" in f.detail for f in unused)
    assert any("hidden camera is a client" in f.note for f in unused)


def test_no_monitor_mode_suggests_an_adapter(fake_run, monkeypatch):
    fake_run["iw"] = IW_LIST_NO_MONITOR
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    info, findings = hardware.probe_wifi()
    assert info["monitor_mode"] is False
    assert [f for f in findings if f.exploited is False] == []
    assert any("AWUS036ACM" in f.note for f in findings)


def test_six_gigahertz_band_is_recognised(fake_run, monkeypatch):
    fake_run["iw"] = IW_LIST_6E
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    info, _ = hardware.probe_wifi()
    assert "6 GHz (Wi-Fi 6E)" in info["bands"]


def test_windows_netsh_monitor_mode_parsing(fake_run, monkeypatch):
    fake_run["netsh"] = NETSH_DRIVERS_MONITOR
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")

    info, findings = hardware.probe_wifi()
    assert info["monitor_mode"] is True
    assert "AX201" in info["driver"]
    assert "802.11ax" in info["radio_types"]
    assert any(f.exploited is False for f in findings)


def test_windows_netsh_without_monitor_mode(fake_run, monkeypatch):
    fake_run["netsh"] = NETSH_DRIVERS_NO_MONITOR
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")

    info, findings = hardware.probe_wifi()
    assert info["monitor_mode"] is False
    assert [f for f in findings if f.exploited is False] == []


# ---------------------------------------------------------------------------
# USB
# ---------------------------------------------------------------------------

LSUSB = """\
Bus 001 Device 004: ID 0bda:2838 Realtek Semiconductor Corp. RTL2838 DVB-T
Bus 001 Device 005: ID 10c4:ea60 Silicon Labs CP210x UART Bridge
Bus 001 Device 006: ID 1fc9:000c NXP Semiconductors Ubertooth One
"""


def test_known_radios_are_recognised_and_marked_by_support(fake_run, monkeypatch):
    fake_run["lsusb"] = LSUSB
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    _, findings = hardware.probe_usb()
    detail = {f.detail: f.exploited for f in findings}
    assert any("RTL-SDR" in d and s is True for d, s in detail.items())
    assert any("CP2102" in d and s is True for d, s in detail.items())
    # Ubertooth is real hardware sweep cannot yet drive — it must not claim it.
    assert any("Ubertooth" in d and s is False for d, s in detail.items())


# ---------------------------------------------------------------------------
# Whole report
# ---------------------------------------------------------------------------

def test_scan_survives_a_probe_that_raises(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(hardware, "probe_wifi", boom)
    report = hardware.scan()
    assert any("probe exploded" in e for e in report.errors)
    assert report.system, "the rest of the report must still be produced"


def test_scan_is_serialisable():
    import json

    payload = hardware.as_dict(hardware.scan())
    json.dumps(payload)     # must not raise
    for key in ("system", "bluetooth", "wifi", "usb", "findings", "errors"):
        assert key in payload


def test_unexploited_filters_to_actionable_items(fake_run, monkeypatch):
    fake_run["hciconfig"] = HCICONFIG_BT52
    fake_run["iw"] = IW_LIST_MONITOR
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")

    report = hardware.scan()
    unused = report.unexploited()
    assert len(unused) >= 3, "BT extended adv, BT coded PHY, Wi-Fi monitor mode"
    assert all(f.exploited is False for f in unused)
    assert all(f.note for f in unused), "every gap must say why it matters"
