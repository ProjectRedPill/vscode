"""Deep hardware capability probe.

`sweep doctor` answers "does this radio work?". This module answers the more
useful question: **"what is this radio actually capable of, and how much of
that am I using?"**

The distinction matters because the default answer is often "much less than you
think". A 2020-or-later laptop almost certainly has a Bluetooth 5 controller,
which can hear two things a legacy scan cannot:

  Extended Advertising (BT 5.0)  Adverts on the 37 secondary channels with up
                                 to 254 bytes of payload. A legacy scanner sees
                                 only the 3 primary channels and 31 bytes — so
                                 a device using extended advertising can be
                                 *completely invisible* to it, not merely
                                 truncated.
  LE Coded PHY (BT 5.0)          Forward error correction for roughly 2-4x
                                 range at the same transmit power.

Neither is exotic and neither costs money. They are the highest-value unused
capability on a typical laptop, which is exactly the sort of thing a coverage
report should be telling you about.

Everything here is read-only inspection of the host's own hardware. It touches
no radio and transmits nothing.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

#: LMP/HCI version byte -> Bluetooth core specification version.
#: From the Bluetooth SIG's assigned numbers for the Link Manager Protocol.
LMP_VERSION = {
    0: "1.0b", 1: "1.1", 2: "1.2", 3: "2.0", 4: "2.1", 5: "3.0",
    6: "4.0", 7: "4.1", 8: "4.2", 9: "5.0", 10: "5.1", 11: "5.2",
    12: "5.3", 13: "5.4", 14: "6.0",
}


@dataclass
class Finding:
    """One capability observation, and whether sweep is exploiting it."""

    subject: str            # "Bluetooth", "Wi-Fi", "USB", "System"
    detail: str
    exploited: bool | None  # True used, False available-but-unused, None = n/a
    note: str = ""


@dataclass
class HardwareReport:
    system: dict[str, Any] = field(default_factory=dict)
    bluetooth: dict[str, Any] = field(default_factory=dict)
    wifi: dict[str, Any] = field(default_factory=dict)
    usb: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def unexploited(self) -> list[Finding]:
        return [f for f in self.findings if f.exploited is False]


def _run(argv: list[str], timeout: float = 8.0) -> str:
    """Run a command, return stdout. Never raises; missing tools yield ''."""
    if not shutil.which(argv[0]):
        return ""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

def probe_system() -> dict[str, Any]:
    import os

    info: dict[str, Any] = {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }

    # RAM, best-effort per platform.
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        info["ram_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
                        break
        except OSError:
            pass
        model = ""
        try:
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
        if model:
            info["cpu"] = model
    elif platform.system() == "Darwin":
        mem = _run(["sysctl", "-n", "hw.memsize"]).strip()
        if mem.isdigit():
            info["ram_gb"] = round(int(mem) / 1024**3, 1)
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if cpu:
            info["cpu"] = cpu
    elif platform.system() == "Windows":
        # CIM rather than wmic: wmic is deprecated and absent from Windows 11
        # 24H2 onward, so the old call silently returned nothing on new machines.
        out = _run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
        ], timeout=20)
        digits = re.search(r"(\d{9,})", out)
        if digits:
            info["ram_gb"] = round(int(digits.group(1)) / 1024**3, 1)
        cpu = _run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name",
        ], timeout=20).strip()
        if cpu:
            info["cpu"] = cpu

    return info


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------

def probe_bluetooth() -> tuple[dict[str, Any], list[Finding]]:
    system = platform.system()
    if system == "Linux":
        return _bluetooth_linux()
    if system == "Darwin":
        return _bluetooth_macos()
    if system == "Windows":
        return _bluetooth_windows()
    return {}, []


def _bt_version_findings(spec: str | None) -> list[Finding]:
    """The capability story that follows from a controller's spec version."""
    if not spec:
        return []

    try:
        major, minor = (int(p) for p in spec.split(".")[:2])
    except ValueError:
        return []

    out: list[Finding] = []
    if (major, minor) >= (5, 0):
        out.append(Finding(
            "Bluetooth",
            f"Controller is Bluetooth {spec} — supports Extended Advertising",
            exploited=False,
            note=(
                "Legacy scanning sees only the 3 primary channels and 31 bytes "
                "per advert. Devices using extended advertising put their "
                "payload on secondary channels, where a legacy scan cannot see "
                "them at all. This is a blind spot, not just reduced detail."
            ),
        ))
        out.append(Finding(
            "Bluetooth",
            f"Controller is Bluetooth {spec} — supports LE Coded PHY (long range)",
            exploited=False,
            note=(
                "Forward error correction buys roughly 2-4x range at the same "
                "transmit power. For a sweep this is the difference between "
                "hearing a tracker in the next room and not."
            ),
        ))
    else:
        out.append(Finding(
            "Bluetooth",
            f"Controller is Bluetooth {spec} — legacy advertising only",
            exploited=True,
            note=(
                "Pre-5.0 controllers cannot see extended advertising or use "
                "Coded PHY. A ~£12 USB Bluetooth 5 adapter would add both."
            ),
        ))
    return out


def _bluetooth_linux() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []

    # `hciconfig -a` is deprecated but still the most widely present source of
    # the HCI version, which is what the spec level is derived from.
    out = _run(["hciconfig", "-a"])
    if out:
        info["raw_source"] = "hciconfig"
        m = re.search(r"HCI Version:\s*([\d.]+)", out)
        if m:
            info["spec_version"] = m.group(1)
        m = re.search(r"Manufacturer:\s*(.+)", out)
        if m:
            info["manufacturer"] = m.group(1).strip()
        m = re.search(r"BD Address:\s*([0-9A-F:]{17})", out, re.I)
        if m:
            info["address"] = m.group(1)
        info["adapters"] = re.findall(r"^(hci\d+):", out, re.M)

    # btmgmt reports the controller's settings and supported feature bits, and
    # unlike hciconfig it is current. Needs privileges for some fields.
    mgmt = _run(["btmgmt", "info"])
    if mgmt:
        info.setdefault("raw_source", "btmgmt")
        supported = re.search(r"supported settings:\s*(.+)", mgmt, re.I)
        if supported:
            flags = supported.group(1).split()
            info["supported_settings"] = flags
            if "le" in flags:
                findings.append(Finding(
                    "Bluetooth", "LE supported by the controller", exploited=True))
        m = re.search(r"version\s+([\d.]+)", mgmt, re.I)
        if m and "spec_version" not in info:
            info["spec_version"] = m.group(1)

    # The kernel exposes the raw LMP version, which is the most reliable of the
    # three and needs no external tool at all.
    try:
        import glob

        for path in sorted(glob.glob("/sys/class/bluetooth/hci*")):
            try:
                with open(f"{path}/../lmp_ver") as fh:
                    raw = int(fh.read().strip())
                    info.setdefault("spec_version", LMP_VERSION.get(raw))
                    break
            except (OSError, ValueError):
                continue
    except Exception:
        pass

    if not info:
        findings.append(Finding(
            "Bluetooth", "No Bluetooth adapter detected", exploited=None,
            note="Install bluez (`apt install bluez`) or check the adapter is enabled.",
        ))
        return info, findings

    findings += _bt_version_findings(info.get("spec_version"))
    return info, findings


def _bluetooth_macos() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []

    out = _run(["system_profiler", "-json", "SPBluetoothDataType"], timeout=25)
    if not out:
        return info, findings
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return info, findings

    for block in data.get("SPBluetoothDataType", []):
        ctrl = block.get("controller_properties") or {}
        if not ctrl:
            continue
        info["chipset"] = ctrl.get("controller_chipset")
        info["address"] = ctrl.get("controller_address")
        info["firmware"] = ctrl.get("controller_firmwareVersion")
        info["transport"] = ctrl.get("controller_transport")
        # macOS reports "attrib_le_supported" style keys rather than a spec
        # version; the chipset string is the best available proxy.
        info["state"] = ctrl.get("controller_state")
        break

    if info:
        findings.append(Finding(
            "Bluetooth",
            f"Apple controller {info.get('chipset') or 'present'}",
            exploited=True,
            note=(
                "macOS does not expose the HCI spec version to userspace, and "
                "CoreBluetooth hides raw HCI entirely — so extended-advertising "
                "scanning is not reachable on macOS regardless of the hardware. "
                "A Linux host or a USB sniffer is the way to get it."
            ),
        ))
    return info, findings


def _bluetooth_windows() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []

    out = _run([
        "powershell", "-NoProfile", "-Command",
        "Get-PnpDevice -Class Bluetooth -Status OK "
        "| Select-Object -First 5 FriendlyName | Format-List",
    ], timeout=25)
    names = re.findall(r"FriendlyName\s*:\s*(.+)", out)
    if names:
        info["devices"] = [n.strip() for n in names]
        radio = next((n for n in names if "radio" in n.lower()
                      or "adapter" in n.lower()), names[0])
        info["adapter"] = radio.strip()
        findings.append(Finding(
            "Bluetooth", f"Adapter: {info['adapter']}", exploited=True,
            note=(
                "Windows exposes no HCI spec version through WinRT, and the "
                "WinRT BLE API does not surface extended advertising to "
                "third-party code the way BlueZ does."
            ),
        ))
    return info, findings


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

def probe_wifi() -> tuple[dict[str, Any], list[Finding]]:
    system = platform.system()
    if system == "Linux":
        return _wifi_linux()
    if system == "Darwin":
        return _wifi_macos()
    if system == "Windows":
        return _wifi_windows()
    return {}, []


def _wifi_linux() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []

    out = _run(["iw", "list"], timeout=15)
    if not out:
        findings.append(Finding(
            "Wi-Fi", "`iw` not available — cannot inspect adapter capability",
            exploited=None, note="`apt install iw` for a capability read-out.",
        ))
        return info, findings

    modes_block = re.search(
        r"Supported interface modes:\s*((?:\s*\*.*\n)+)", out)
    modes = re.findall(r"\*\s*(.+)", modes_block.group(1)) if modes_block else []
    info["interface_modes"] = [m.strip() for m in modes]

    monitor = any("monitor" in m.lower() for m in modes)
    info["monitor_mode"] = monitor
    if monitor:
        findings.append(Finding(
            "Wi-Fi", "Adapter supports monitor mode", exploited=False,
            note=(
                "sweep uses ordinary scan APIs, which see access points only. "
                "Monitor mode also sees *client* devices — and a hidden camera "
                "is a client, not an AP. This is the single largest unused "
                "capability on most laptops. Needs root."
            ),
        ))
    else:
        findings.append(Finding(
            "Wi-Fi", "Adapter does not report monitor mode", exploited=None,
            note=(
                "An Alfa AWUS036ACM (~$35) adds monitor mode and would reveal "
                "client devices, not just access points."
            ),
        ))

    bands: list[str] = []
    if re.search(r"Band 1:", out):
        bands.append("2.4 GHz")
    if re.search(r"Band 2:", out):
        bands.append("5 GHz")
    if re.search(r"Band 4:", out):
        bands.append("6 GHz (Wi-Fi 6E)")
    info["bands"] = bands
    if "6 GHz (Wi-Fi 6E)" in bands:
        findings.append(Finding(
            "Wi-Fi", "Adapter covers the 6 GHz band", exploited=True,
            note="6 GHz APs are invisible to older adapters entirely.",
        ))
    elif bands:
        findings.append(Finding(
            "Wi-Fi", f"Adapter covers {', '.join(bands)}", exploited=True,
            note="No 6 GHz — a 6E access point would not be seen at all.",
        ))

    ifaces = re.findall(r"Interface\s+(\S+)", _run(["iw", "dev"]))
    if ifaces:
        info["interfaces"] = ifaces
    return info, findings


def _wifi_macos() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings = [Finding(
        "Wi-Fi", "macOS exposes limited Wi-Fi capability information",
        exploited=None,
        note=(
            "macOS can capture in monitor mode via `airport --sniff` or "
            "Wireshark, but recent releases removed the `airport` tool and "
            "sweep does not drive the replacement."
        ),
    )]
    out = _run(["system_profiler", "-json", "SPAirPortDataType"], timeout=25)
    if out:
        try:
            data = json.loads(out)
            info["raw"] = "SPAirPortDataType present"
            blocks = data.get("SPAirPortDataType", [])
            if blocks:
                ifaces = blocks[0].get("spairport_airport_interfaces", [])
                if ifaces:
                    info["interface"] = ifaces[0].get("_name")
                    caps = ifaces[0].get("spairport_supported_phymodes")
                    if caps:
                        info["phy_modes"] = caps
        except (json.JSONDecodeError, AttributeError, IndexError):
            pass
    return info, findings


def _wifi_windows() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []

    out = _run(["netsh", "wlan", "show", "drivers"], timeout=20)
    if not out:
        return info, findings

    m = re.search(r"Network monitor mode supported\s*:\s*(\w+)", out, re.I)
    if m:
        supported = m.group(1).lower().startswith("y")
        info["monitor_mode"] = supported
        if supported:
            findings.append(Finding(
                "Wi-Fi", "Driver reports monitor mode support", exploited=False,
                note=(
                    "Windows drivers rarely expose this usefully to third-party "
                    "tools even when they report it. A Linux host or an external "
                    "adapter is the practical route."
                ),
            ))
        else:
            findings.append(Finding(
                "Wi-Fi", "Driver does not support monitor mode", exploited=None,
                note="Access-point scanning only; client devices stay invisible.",
            ))

    m = re.search(r"Radio types supported\s*:\s*(.+)", out, re.I)
    if m:
        info["radio_types"] = m.group(1).strip()
    m = re.search(r"Driver\s*:\s*(.+)", out)
    if m:
        info["driver"] = m.group(1).strip()
    return info, findings


# ---------------------------------------------------------------------------
# USB — matters for SDR bandwidth and for spotting hardware already attached
# ---------------------------------------------------------------------------

#: USB IDs of radios sweep can or could drive.
KNOWN_RADIOS = {
    "0bda:2838": ("RTL-SDR (RTL2832U)", True),
    "0bda:2832": ("RTL-SDR (RTL2832U)", True),
    "1d50:6089": ("HackRF One", True),
    "1d50:60a1": ("Airspy", False),
    "1fc9:000c": ("Ubertooth One", False),
    "1915:520f": ("nRF52840 dongle", False),
    "0451:16ae": ("TI CC26x2/CC1352 (Sniffle)", False),
    "0403:6001": ("FTDI serial (probe board?)", True),
    "10c4:ea60": ("CP2102 serial (ESP32 probe?)", True),
    "1a86:7523": ("CH340 serial (ESP32 probe?)", True),
}


def probe_usb() -> tuple[dict[str, Any], list[Finding]]:
    info: dict[str, Any] = {}
    findings: list[Finding] = []
    system = platform.system()

    if system == "Linux":
        out = _run(["lsusb"])
        if out:
            info["devices"] = out.strip().splitlines()
            for vid_pid, (name, supported) in KNOWN_RADIOS.items():
                if vid_pid in out.lower():
                    findings.append(Finding(
                        "USB", f"{name} is plugged in", exploited=supported,
                        note=("sweep can drive this today." if supported else
                              "sweep does not drive this yet — see RESEARCH.md roadmap."),
                    ))
        # USB 3 controllers matter for wideband SDR capture.
        if "xhci" in _run(["lsmod"]) or _run(["lspci"]).lower().count("usb 3"):
            info["usb3"] = True
    elif system == "Darwin":
        out = _run(["system_profiler", "SPUSBDataType"], timeout=25)
        if out:
            info["raw_present"] = True
            for _vid, (name, supported) in KNOWN_RADIOS.items():
                short = name.split(" (")[0]
                if short.lower() in out.lower():
                    findings.append(Finding(
                        "USB", f"{name} appears attached", exploited=supported))
    elif system == "Windows":
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "Get-PnpDevice -Status OK | Select-Object FriendlyName | Format-List",
        ], timeout=25)
        if out:
            for _vid, (name, supported) in KNOWN_RADIOS.items():
                short = name.split(" (")[0]
                if short.lower() in out.lower():
                    findings.append(Finding(
                        "USB", f"{name} appears attached", exploited=supported))

    return info, findings


# ---------------------------------------------------------------------------

def scan() -> HardwareReport:
    """Full read-only hardware inventory."""
    report = HardwareReport()
    report.system = probe_system()

    for name, probe in (
        ("bluetooth", probe_bluetooth),
        ("wifi", probe_wifi),
        ("usb", probe_usb),
    ):
        try:
            info, findings = probe()
            setattr(report, name, info)
            report.findings.extend(findings)
        except Exception as exc:   # a probe must never break the report
            report.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    # Spectrum work is the one CPU-bound part of the tool.
    cores = report.system.get("cpu_count") or 0
    if cores:
        report.findings.append(Finding(
            "System",
            f"{cores} CPU cores, {report.system.get('ram_gb', '?')} GB RAM",
            exploited=True,
            note=(
                "Ample for every sensor here. Spectrum sweeping is the only "
                "CPU-bound part, and it is comfortable on 2 cores."
                if cores >= 2 else
                "Spectrum sweeping may struggle; the other sensors are fine."
            ),
        ))
    return report


def as_dict(report: HardwareReport) -> dict[str, Any]:
    return {
        "system": report.system,
        "bluetooth": report.bluetooth,
        "wifi": report.wifi,
        "usb": report.usb,
        "findings": [
            {"subject": f.subject, "detail": f.detail,
             "exploited": f.exploited, "note": f.note}
            for f in report.findings
        ],
        "errors": report.errors,
    }
