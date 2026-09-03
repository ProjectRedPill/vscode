"""Command line interface.

    sweep serve                web UI — open on a laptop or a phone
    sweep scan                 live dashboard across every available band
    sweep find <device>        walk-around ranging on one device
    sweep sweep                timed room sweep, writes a report
    sweep devices              list what the last sweeps saw
    sweep show <device>        every decoded fact about one device
    sweep trust <device> mine  teach it which devices are yours
    sweep doctor               what hardware is usable and what is missing
    sweep hwscan               what your radios can do that sweep is not using
    sweep decode <hex>         offline: decode a captured advertisement
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from .core.engine import Engine, EngineConfig, device_dict
from .core.fusion import FusionConfig
from .core.models import Trust
from .core.rssi import path_loss_env_factor
from .core.store import Store, default_path
from .sensors import ALL_SENSORS, DEFAULT_SENSORS
from .ui import render, report
from .ui.render import Painter
from .ui.tui import Tui

BANNER = """\
sweep — passive multi-band device discovery and counter-surveillance sweep

Receive-only: it listens to what devices broadcast. It never connects, pairs,
deauthenticates or transmits. Use it on your own space and your own devices, or
where you have permission to sweep.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep",
        description=BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", help=f"database path (default: {default_path()})")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--json", action="store_true", help="machine-readable output")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_json_flag(p: argparse.ArgumentParser) -> None:
        """Accept `--json` after the subcommand as well as before it.

        SUPPRESS is what makes this work: without it, the subparser's own
        default would overwrite a `--json` given before the subcommand, so
        `sweep --json sweep` would silently produce markdown.
        """
        p.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS,
            help="machine-readable output",
        )

    def add_sensor_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--sensors", default=",".join(DEFAULT_SENSORS),
            help=f"comma-separated. available: {', '.join(ALL_SENSORS)}. "
                 "use 'all' for everything",
        )
        p.add_argument("--passive-ble", action="store_true",
                       help="passive BLE scanning — no SCAN_REQ, less detail, fully silent")
        p.add_argument("--rtl433-freq", action="append",
                       help="ISM frequency for rtl_433, repeatable (e.g. 433.92M)")
        p.add_argument("--spectrum-start", type=float, default=300e6)
        p.add_argument("--spectrum-stop", type=float, default=1700e6)
        p.add_argument("--spectrum-threshold", type=float, default=12.0,
                       help="dB above the rolling noise floor to report a carrier")
        p.add_argument("--ir-port", help="serial port of an IR probe")
        p.add_argument("--ir-fifo", help="file/FIFO of IR probe records")
        p.add_argument("--rf-port", help="serial port of a broadband RF power probe")
        p.add_argument("--rf-fifo", help="file/FIFO of RF probe records")
        p.add_argument("--env", default="office",
                       choices=["open", "room", "office", "home", "cluttered", "through-wall"],
                       help="environment, tunes the distance estimate")
        p.add_argument("--log-raw", action="store_true",
                       help="record every observation to the database (large)")

    p_scan = sub.add_parser("scan", help="live dashboard")
    add_sensor_args(p_scan)
    p_scan.add_argument("--duration", type=float, help="stop after N seconds")

    p_find = sub.add_parser("find", help="range in on one device")
    p_find.add_argument("device", help="id, address, or part of a name")
    add_sensor_args(p_find)
    p_find.add_argument("--duration", type=float)

    p_sweep = sub.add_parser("sweep", help="timed sweep, then write a report")
    add_sensor_args(p_sweep)
    p_sweep.add_argument("--duration", type=float, default=180.0)
    p_sweep.add_argument("--out", help="write the report here (.md or .json)")
    p_sweep.add_argument("--quiet", action="store_true", help="no live UI")
    p_sweep.add_argument("--all", action="store_true",
                         help="include devices you marked as your own")
    add_json_flag(p_sweep)

    p_devices = sub.add_parser("devices", help="devices from previous sweeps")
    p_devices.add_argument("--limit", type=int, default=50)
    add_json_flag(p_devices)

    p_show = sub.add_parser("show", help="everything known about one device")
    p_show.add_argument("device")
    add_json_flag(p_show)

    p_trust = sub.add_parser("trust", help="record your disposition on a device")
    p_trust.add_argument("device")
    p_trust.add_argument("disposition", choices=[t.value for t in Trust])
    p_trust.add_argument("--label", help="a name you will recognise")

    add_json_flag(sub.add_parser("trusted", help="list recorded dispositions"))

    p_untrust = sub.add_parser("untrust", help="forget a recorded disposition")
    p_untrust.add_argument("alias")

    add_json_flag(sub.add_parser("doctor", help="report what hardware is usable"))

    add_json_flag(sub.add_parser(
        "hwscan",
        help="inspect this machine's radios and what they could do but are not",
    ))

    p_decode = sub.add_parser("decode", help="decode a captured advertisement offline")
    p_decode.add_argument("hex", help="raw AD payload as hex")

    p_sessions = sub.add_parser("sessions", help="past sweep sessions")
    p_sessions.add_argument("--limit", type=int, default=20)
    add_json_flag(p_sessions)

    p_serve = sub.add_parser(
        "serve", help="web UI — open it in a browser, or on your phone"
    )
    add_sensor_args(p_serve)
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="bind address. 127.0.0.1 (default) is this machine only; "
             "0.0.0.0 exposes the UI to your network and mints an access token",
    )
    p_serve.add_argument("--duration", type=float, help="stop after N seconds")
    p_serve.add_argument("--open", action="store_true", help="open a browser")
    p_serve.add_argument("--no-qr", action="store_true",
                         help="do not print a QR code for the join URL")

    return parser


# ---------------------------------------------------------------------------


def engine_config(args: argparse.Namespace) -> EngineConfig:
    names = getattr(args, "sensors", ",".join(DEFAULT_SENSORS))
    sensors = list(ALL_SENSORS) if names == "all" else [
        s.strip() for s in names.split(",") if s.strip()
    ]
    unknown = [s for s in sensors if s not in ALL_SENSORS]
    if unknown:
        raise SystemExit(
            f"unknown sensor(s): {', '.join(unknown)}\navailable: {', '.join(ALL_SENSORS)}"
        )

    options: dict[str, Any] = {
        "ble_active_scan": not getattr(args, "passive_ble", False),
        "rtl433_frequencies": getattr(args, "rtl433_freq", None),
        "spectrum_start_hz": getattr(args, "spectrum_start", None),
        "spectrum_stop_hz": getattr(args, "spectrum_stop", None),
        "spectrum_threshold_db": getattr(args, "spectrum_threshold", None),
        "ir_port": getattr(args, "ir_port", None),
        "ir_fifo": getattr(args, "ir_fifo", None),
        "rf_port": getattr(args, "rf_port", None),
        "rf_fifo": getattr(args, "rf_fifo", None),
    }
    return EngineConfig(
        sensors=sensors,
        sensor_options={k: v for k, v in options.items() if v is not None},
        fusion=FusionConfig(),
        log_raw=getattr(args, "log_raw", False),
        db_path=args.db,
    )


async def cmd_scan(args: argparse.Namespace, painter: Painter) -> int:
    engine = Engine(engine_config(args))
    statuses = await engine.probe()
    _print_probe(statuses, painter)

    if not any(s.available for s in statuses):
        print(painter.c("\nNo sensors are usable. Run `sweep doctor` for details.", "red"))
        return 2

    env = path_loss_env_factor(getattr(args, "env", "office"))
    engine.ranger.env_factor = env

    await engine.start()
    tui = Tui(engine, color=painter.color)
    engine.on_finding(
        lambda dev, f: tui.notify(f"{f.severity_label.upper()}: {dev.display_name()} — {f.title}", 8.0)
    )

    ui_task = asyncio.create_task(tui.run())
    try:
        if args.duration:
            await asyncio.wait_for(engine.stop.wait(), timeout=args.duration)
        else:
            await engine.stop.wait()
    except asyncio.TimeoutError:
        engine.stop.set()
    except KeyboardInterrupt:
        engine.stop.set()
    finally:
        await asyncio.gather(ui_task, return_exceptions=True)
        await engine.shutdown()

    print(_closing_summary(engine, painter))
    return 0


async def cmd_find(args: argparse.Namespace, painter: Painter) -> int:
    engine = Engine(engine_config(args))
    statuses = await engine.probe()
    _print_probe(statuses, painter)
    engine.ranger.env_factor = path_loss_env_factor(args.env)

    await engine.start()
    print(painter.dim(f"\nlooking for '{args.device}' …"))

    # The target may not have advertised yet, so wait for it rather than
    # failing immediately — a sleeping tag can take a minute to speak.
    deadline = time.time() + 60
    target = None
    while time.time() < deadline and not engine.stop.is_set():
        target = engine.target(args.device)
        if target is not None:
            break
        await asyncio.sleep(1.0)

    if target is None:
        await engine.shutdown()
        print(painter.c(
            f"no device matching {args.device!r} appeared in 60s. "
            "Run `sweep scan` to see what is around.", "red"))
        return 1

    print(painter.c(f"tracking {target.display_name()} ({target.address})", "green"))
    tui = Tui(engine, color=painter.color)
    tui.selected = target
    tui.view = "find"

    ui_task = asyncio.create_task(tui.run())
    try:
        if args.duration:
            await asyncio.wait_for(engine.stop.wait(), timeout=args.duration)
        else:
            await engine.stop.wait()
    except (asyncio.TimeoutError, KeyboardInterrupt):
        engine.stop.set()
    finally:
        await asyncio.gather(ui_task, return_exceptions=True)
        await engine.shutdown()
    return 0


async def cmd_sweep(args: argparse.Namespace, painter: Painter) -> int:
    engine = Engine(engine_config(args))
    statuses = await engine.probe()
    _print_probe(statuses, painter)

    if not any(s.available for s in statuses):
        print(painter.c("No sensors are usable — nothing to sweep.", "red"))
        return 2

    await engine.start()
    tui: Tui | None = None
    ui_task: asyncio.Task[Any] | None = None
    if not args.quiet:
        tui = Tui(engine, color=painter.color)
        ui_task = asyncio.create_task(tui.run())
    else:
        print(painter.dim(f"sweeping for {args.duration:.0f}s …"))

    # Give the sensors a moment to populate, then freeze the baseline so
    # "arrived during the sweep" means something.
    await asyncio.sleep(min(15.0, args.duration / 4))
    engine.mark_baseline()

    try:
        await asyncio.wait_for(engine.stop.wait(), timeout=args.duration)
    except asyncio.TimeoutError:
        engine.stop.set()
    except KeyboardInterrupt:
        engine.stop.set()
    finally:
        if ui_task:
            await asyncio.gather(ui_task, return_exceptions=True)
        engine.evaluate_all()
        text = (
            report.json_report(engine) if args.json
            else report.markdown_report(engine, include_all=args.all)
        )
        await engine.shutdown()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(painter.c(f"report written to {args.out}", "green"))
    else:
        print(text)
    return 0


async def cmd_serve(args: argparse.Namespace, painter: Painter) -> int:
    from .web import WebServer

    engine = Engine(engine_config(args))
    statuses = await engine.probe()
    _print_probe(statuses, painter)
    engine.ranger.env_factor = path_loss_env_factor(args.env)

    server = WebServer(engine, host=args.host, port=args.port)
    try:
        await server.start()
    except OSError as exc:
        print(painter.c(f"cannot bind {args.host}:{args.port} — {exc}", "red"))
        await engine.shutdown()
        return 2

    await engine.start()
    _print_serve_banner(server, statuses, painter, qr=not args.no_qr)

    if args.open:
        import webbrowser

        webbrowser.open(server.url)

    try:
        if args.duration:
            await asyncio.wait_for(engine.stop.wait(), timeout=args.duration)
        else:
            await engine.stop.wait()
    except (asyncio.TimeoutError, KeyboardInterrupt):
        engine.stop.set()
    finally:
        await server.stop()
        await engine.shutdown()

    print(_closing_summary(engine, painter))
    return 0


def cmd_devices(args: argparse.Namespace, painter: Painter) -> int:
    with Store(args.db) as store:
        rows = store.db.execute(
            """SELECT id, label, device_class, trust, vendor, model,
                      first_seen, last_seen, attrs
               FROM devices ORDER BY last_seen DESC LIMIT ?""",
            (args.limit,),
        ).fetchall()

    if args.json:
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return 0

    if not rows:
        print(painter.dim("no devices recorded yet — run `sweep scan` first"))
        return 0

    print(painter.dim(f"{'LAST SEEN':<20} {'CLASS':<12} {'TRUST':<8} {'VENDOR':<20} NAME"))
    for row in rows:
        attrs = json.loads(row["attrs"] or "{}")
        name = row["label"] or attrs.get("name") or attrs.get("ssid") or row["id"]
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["last_seen"] or 0))
        print(
            f"{when:<20} {(row['device_class'] or '?'):<12} "
            f"{(row['trust'] or '-'):<8} {(row['vendor'] or '-')[:20]:<20} {name}"
        )
    return 0


def cmd_show(args: argparse.Namespace, painter: Painter) -> int:
    with Store(args.db) as store:
        rows = store.db.execute(
            "SELECT * FROM devices WHERE id LIKE ? OR label LIKE ? OR attrs LIKE ?",
            (f"{args.device}%", f"%{args.device}%", f"%{args.device}%"),
        ).fetchall()
        if not rows:
            print(painter.c(f"no stored device matching {args.device!r}", "red"))
            return 1
        row = dict(rows[0])
        row["attrs"] = json.loads(row.get("attrs") or "{}")
        row["history"] = store.history_for_address(
            row["attrs"].get("mac") or row["id"]
        )
        row["findings"] = [
            dict(r) for r in store.db.execute(
                "SELECT ts, rule, severity, title, detail FROM findings "
                "WHERE device_id = ? ORDER BY ts DESC LIMIT 50",
                (row["id"],),
            )
        ]
    print(json.dumps(row, indent=2, default=str))
    return 0


def cmd_trust(args: argparse.Namespace, painter: Painter) -> int:
    """Record a disposition without needing a live scan.

    Aliases are stored verbatim, so `sweep trust AA:BB:CC:DD:EE:FF mine` works
    on an address you copied out of a report.
    """
    with Store(args.db) as store:
        alias = args.device
        if ":" not in alias and len(alias) == 12:
            alias = ":".join(alias[i : i + 2] for i in range(0, 12, 2))
        candidates = [alias, f"ble:{alias}", f"wifi:{alias}", f"bt_classic:{alias}"]
        for candidate in candidates:
            store.set_trust(candidate, Trust(args.disposition), args.label)
    print(painter.c(
        f"{args.device} recorded as '{args.disposition}'"
        + (f" ({args.label})" if args.label else ""), "green"))
    return 0


def cmd_trusted(args: argparse.Namespace, painter: Painter) -> int:
    with Store(args.db) as store:
        entries = store.list_trust()
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    if not entries:
        print(painter.dim("nothing recorded"))
        return 0
    for entry in entries:
        print(f"{entry['trust']:<8} {entry['alias']:<28} {entry['label'] or ''}")
    return 0


def cmd_untrust(args: argparse.Namespace, painter: Painter) -> int:
    with Store(args.db) as store:
        removed = any(
            store.clear_trust(a)
            for a in (args.alias, f"ble:{args.alias}", f"wifi:{args.alias}",
                      f"bt_classic:{args.alias}")
        )
    print(painter.c("removed" if removed else "no such alias",
                    "green" if removed else "red"))
    return 0 if removed else 1


async def cmd_doctor(args: argparse.Namespace, painter: Painter) -> int:
    """What can this machine detect, and what would change that.

    Shares the capability catalogue with the web UI's Coverage tab and the
    sweep report, so the three cannot give different answers.
    """
    from .core import capability
    from .intel import oui, sig

    engine = Engine(EngineConfig(sensors=list(ALL_SENSORS), db_path=args.db))
    await engine.probe()
    report_data = capability.assess(engine.sensors)
    engine.store.close()

    if args.json:
        print(json.dumps(report_data, indent=2, default=str))
        return 0

    host = report_data["host"]
    print()
    print(painter.c(
        f"  Host: {host['hostname']} ({host['pretty']} {host['machine']})",
        "white", bold=True))
    print(painter.dim(
        "  This machine's radios decide what can be detected. A phone or browser "
        "viewing the UI contributes nothing."))
    print()

    for band in report_data["bands"]:
        mark, colour = {
            "active": ("●", "green"),
            "unavailable": ("○", "orange"),
            "off": ("○", "grey"),
        }[band["status"]]
        label = {"active": "sensing", "unavailable": "not available", "off": "off"}[band["status"]]
        print(f"  {painter.c(mark, colour)} {painter.c(band['title'], 'white', bold=True)}"
              f"  {painter.dim(label)}")
        print(f"      {painter.dim(band['reason'])}")

        if band["status"] == "active":
            for item in band["detects"][:2]:
                print(f"      {painter.dim('· ' + item)}")
        else:
            print(f"      {painter.c('blind to: ' + band['blind_without'], 'orange')}")
            for upgrade in band["upgrades"]:
                print("      " + painter.dim(
                    f"→ {upgrade['action']}  ({upgrade['cost']})"))
        print()

    nxt = report_data["next_upgrade"]
    if nxt:
        print(painter.c("  Best next step", "white", bold=True))
        print(f"    {painter.c(nxt['action'], 'green', bold=True)} "
              f"{painter.dim('(' + nxt['cost'] + ')')} "
              f"{painter.dim('— opens ' + str(nxt['bands_gained']) + ' more band(s)')}")
        for line in _wrap_text(nxt["detail"], 74):
            print(painter.dim("    " + line))
        print()

    added = sig.load_external()
    print(painter.c("  Intelligence", "white", bold=True))
    print(f"    OUI table       {oui.table_size():,} entries"
          + (f" (+{added:,} from host files)" if added else " (bundled only)"))
    from .intel.signatures import SIGNATURES
    from .threat.rules import RULES
    print(f"    company IDs     {len(sig.COMPANY_IDS):,}   "
          f"GATT services {len(sig.SERVICE_UUIDS):,}   "
          f"signatures {len(SIGNATURES):,}   rules {len(RULES):,}")
    print(f"    database        {args.db or default_path()}")
    print()

    active = report_data["active_count"]
    total = report_data["total_count"]
    if not active:
        print(painter.c(
            "  Nothing is usable. Install bluez or NetworkManager to start.", "red"))
        return 2
    print(painter.c(f"  Sensing {active} of {total} bands.", "green", bold=True))
    if report_data["blind_spots"]:
        print(painter.dim("  Blind to: " + ", ".join(
            b["title"] for b in report_data["blind_spots"])))
    print()
    return 0


def cmd_hwscan(args: argparse.Namespace, painter: Painter) -> int:
    """What this machine's radios can do, versus what sweep uses.

    Distinct from `doctor`, which answers "does it work?". This answers "how
    much of it am I leaving on the table?" — usually more than people expect.
    """
    from .core import hardware

    report = hardware.scan()

    if args.json:
        print(json.dumps(hardware.as_dict(report), indent=2, default=str))
        return 0

    sysinfo = report.system
    print()
    print("  " + painter.c(
        f"{sysinfo.get('os')} {sysinfo.get('release')} · {sysinfo.get('machine')}",
        "white", bold=True))
    if sysinfo.get("cpu"):
        print(painter.dim(f"  {sysinfo['cpu']}"))
    print()

    for subject, data in (
        ("Bluetooth", report.bluetooth),
        ("Wi-Fi", report.wifi),
        ("USB", report.usb),
    ):
        print("  " + painter.c(subject, "white", bold=True))
        if not data:
            print(painter.dim("    nothing detected"))
        for key, value in data.items():
            if key.startswith("raw") or key == "devices":
                continue
            shown = ", ".join(str(v) for v in value) if isinstance(value, list) else value
            print(f"    {painter.dim(key.replace('_', ' ').ljust(20))} {shown}")
        print()

    used = [f for f in report.findings if f.exploited is True]
    unused = report.unexploited()
    info = [f for f in report.findings if f.exploited is None]

    if unused:
        print("  " + painter.c("Capability you have but are not using", "orange", bold=True))
        for f in unused:
            print("    " + painter.c(f"○ {f.detail}", "orange"))
            for line in _wrap_text(f.note, 72):
                print(painter.dim("      " + line))
        print()

    if used:
        print("  " + painter.c("In use", "green", bold=True))
        for f in used:
            print("    " + painter.c("●", "green") + f" {f.detail}")
        print()

    if info:
        print("  " + painter.c("Notes", "white", bold=True))
        for f in info:
            print(f"    {painter.dim('·')} {f.detail}")
            for line in _wrap_text(f.note, 72):
                print(painter.dim("      " + line))
        print()

    for err in report.errors:
        print(painter.c(f"  probe error: {err}", "red"))

    if unused:
        noun = "capability" if len(unused) == 1 else "capabilities"
        print(painter.c(
            f"  {len(unused)} unused {noun} — hardware you already own that "
            "sweep does not yet", "orange"))
        print(painter.c(
            "  drive. Every one is software work rather than a purchase.", "orange"))
    else:
        print(painter.c("  Everything detected is already in use.", "green"))
    print()
    return 0


def _wrap_text(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width)


def cmd_decode(args: argparse.Namespace, painter: Painter) -> int:
    """Offline decode of a captured advertisement. No hardware needed."""
    from .intel.ble import parse_ad_structures

    raw = args.hex.replace(":", "").replace(" ", "").strip()
    try:
        data = bytes.fromhex(raw)
    except ValueError as exc:
        print(painter.c(f"not valid hex: {exc}", "red"))
        return 1
    decoded = parse_ad_structures(data)
    print(json.dumps(decoded, indent=2, default=str))
    return 0


def cmd_sessions(args: argparse.Namespace, painter: Painter) -> int:
    with Store(args.db) as store:
        rows = store.sessions(args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print(painter.dim("no sessions recorded"))
        return 0
    print(painter.dim(f"{'SESSION':<12} {'STARTED':<20} {'MINUTES':>8} {'DEVICES':>8}"))
    for row in rows:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["started"] or 0))
        minutes = ((row["ended"] or 0) - (row["started"] or 0)) / 60
        print(f"{row['session']:<12} {started:<20} {minutes:>8.1f} {row['devices']:>8}")
    return 0


# ---------------------------------------------------------------------------


def _print_serve_banner(
    server: Any, statuses: list[Any], painter: Painter, qr: bool = True
) -> None:
    """Everything you need to get the UI onto another device, in one screen."""
    from .core import capability

    print()
    if server.loopback:
        print("  " + painter.c(server.url, "green", bold=True))
        print(painter.dim(
            "  This machine only. To use it from your phone, stop and re-run with"))
        print(painter.dim("      sweep serve --host 0.0.0.0"))
    else:
        addresses = capability.lan_addresses()
        primary = None
        for address in addresses:
            url = f"http://{address}:{server.port}/"
            if server.token:
                url += f"?t={server.token}"
            primary = primary or url
            print("  " + painter.c(url, "green", bold=True))
        if not addresses:
            print("  " + painter.c(server.url, "green", bold=True))
            print(painter.dim("  (could not determine a LAN address — check `ip addr`)"))

        if qr and primary:
            try:
                from .core.qr import render

                print()
                print(painter.dim("  Scan this with your phone's camera:"))
                print()
                for line in render(primary, level="M", quiet=3,
                                   color=painter.color).splitlines():
                    print("   " + line)
            except Exception:
                # A QR failure must never stop the server from being usable.
                pass

        print()
        print(painter.c(
            "  Reachable from your network. That URL carries an access token, and "
            "traffic is plain HTTP —", "orange"))
        print(painter.c(
            "  anyone on this network who sees it can read your sweep. Use a "
            "network you trust.", "orange"))

    active = {s.band.value for s in statuses if s.available}
    total = len(capability.CATALOGUE)
    print()
    print(painter.dim(
        f"  Sensing {len(active)} of {total} bands. "
        "Open the Coverage tab in the UI to see what is missing and why."))
    print(painter.dim("  ctrl-c to stop"))
    print()


def _print_probe(statuses: list[Any], painter: Painter) -> None:
    for s in statuses:
        mark = painter.c("✔", "green") if s.available else painter.c("·", "grey")
        line = f"  {mark} {s.name:<12} {s.reason}"
        print(line if s.available else painter.dim(line))


def _closing_summary(engine: Engine, painter: Painter) -> str:
    stats = engine.fusion.stats()
    flagged = [d for d in engine.fusion.devices.values() if d.risk >= 2]
    lines = [
        "",
        painter.c("sweep ended", "white", bold=True),
        f"  {stats['devices']} devices, {stats['observations']:,} packets, "
        f"{engine.context.epoch + 1} location(s)",
    ]
    if flagged:
        lines.append(painter.c(f"  {len(flagged)} device(s) flagged:", "orange"))
        for dev in sorted(flagged, key=lambda d: -d.risk)[:10]:
            lines.append(
                "    " + painter.severity(
                    f"[{dev.findings[0].severity_label}] {dev.display_name()} — "
                    f"{dev.findings[0].title}", dev.risk)
            )
    else:
        lines.append(painter.c("  nothing flagged", "green"))
    lines.append(painter.dim(
        "  `sweep sweep --out report.md` writes a full report with coverage and limits."))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    painter = Painter(False if args.no_color else None)

    handlers_sync = {
        "devices": cmd_devices,
        "show": cmd_show,
        "trust": cmd_trust,
        "trusted": cmd_trusted,
        "untrust": cmd_untrust,
        "decode": cmd_decode,
        "sessions": cmd_sessions,
        "hwscan": cmd_hwscan,
    }
    handlers_async = {
        "scan": cmd_scan,
        "find": cmd_find,
        "sweep": cmd_sweep,
        "doctor": cmd_doctor,
        "serve": cmd_serve,
    }

    try:
        if args.command in handlers_sync:
            return handlers_sync[args.command](args, painter)
        return asyncio.run(handlers_async[args.command](args, painter))
    except KeyboardInterrupt:
        sys.stdout.write(render.show_cursor())
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
