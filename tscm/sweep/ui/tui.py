"""Live terminal UI.

Three views on one screen budget:

  list    everything present, sorted by risk then signal
  detail  every decoded fact about one device
  find    the ranging view — one giant number and warmer/colder

`find` is the reason this tool is usable rather than merely informative. A table
of dBm values does not help you find a thing in a room; a number that gets
bigger as you walk toward it does.

Raw-mode key handling is POSIX-only. On Windows the same views render on a timer
without interactive keys, which is a real limitation and is stated in the footer
rather than hidden.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from ..core.engine import Engine
from ..core.models import Device, Trust
from ..core.rssi import Heat
from . import render
from .render import Painter

HEAT_COLOR = {
    Heat.HOT: "green",
    Heat.WARMER: "green",
    Heat.STEADY: "yellow",
    Heat.COOLER: "blue",
    Heat.COLD: "blue",
    Heat.LOST: "grey",
    Heat.CALIBRATING: "grey",
}

HEAT_WORD = {
    Heat.HOT: "MUCH WARMER",
    Heat.WARMER: "WARMER",
    Heat.STEADY: "SAME SPOT",
    Heat.COOLER: "COOLER",
    Heat.COLD: "MUCH COOLER",
    Heat.LOST: "SIGNAL LOST",
    Heat.CALIBRATING: "CALIBRATING",
}

CLASS_COLOR = {
    "tracker": "red", "camera": "red", "microphone": "red", "covert": "red",
    "jammer": "magenta", "beacon": "orange", "phone": "cyan",
    "computer": "cyan", "network": "blue", "audio": "green",
    "wearable": "green", "sensor": "grey", "unknown": "grey",
}


class Tui:
    def __init__(self, engine: Engine, *, refresh: float = 0.4, color: bool | None = None) -> None:
        self.engine = engine
        self.refresh = refresh
        self.paint = Painter(color)
        self.view = "list"
        self.cursor = 0
        self.rows: list[Device] = []
        self.selected: Device | None = None
        self.message = ""
        self.message_until = 0.0
        self.show_all = False
        self._keys: KeyReader | None = None

    # -- main loop -------------------------------------------------------

    async def run(self) -> None:
        self._keys = KeyReader()
        self._keys.start()
        sys.stdout.write(render.hide_cursor())
        try:
            while not self.engine.stop.is_set():
                self._handle_keys()
                self._draw()
                await asyncio.sleep(self.refresh)
        finally:
            sys.stdout.write(render.show_cursor() + render.RESET + "\n")
            sys.stdout.flush()
            if self._keys:
                self._keys.stop()

    def notify(self, text: str, seconds: float = 4.0) -> None:
        self.message = text
        self.message_until = time.time() + seconds

    # -- input -----------------------------------------------------------

    def _handle_keys(self) -> None:
        if self._keys is None:
            return
        for key in self._keys.drain():
            self._on_key(key)

    def _on_key(self, key: str) -> None:
        if key in ("q", "\x03"):
            self.engine.stop.set()
            return

        if key == "m":
            epoch = self.engine.new_epoch()
            self.notify(
                f"Location {epoch} marked. Devices that follow you across "
                "locations will be flagged."
            )
            return
        if key == "b":
            n = self.engine.mark_baseline()
            self.notify(f"Baseline set: {n} devices marked as already present.")
            return
        if key == "a":
            self.show_all = not self.show_all
            self.notify("Showing all devices." if self.show_all else "Showing unresolved devices only.")
            return

        if self.view == "find":
            if key in ("\x1b", "escape", "f", "\r", "\n"):
                self.engine.target(None)
                self.view = "detail" if self.selected else "list"
            return

        if key in ("j", "\x1b[B"):
            self.cursor = min(self.cursor + 1, max(0, len(self.rows) - 1))
        elif key in ("k", "\x1b[A"):
            self.cursor = max(0, self.cursor - 1)
        elif key in ("\r", "\n", "d"):
            if self.rows:
                self.selected = self.rows[min(self.cursor, len(self.rows) - 1)]
                self.view = "detail"
        elif key == "\x1b":
            self.view = "list"
            self.selected = None
        elif key == "f":
            target = self.selected or (self.rows[self.cursor] if self.rows else None)
            if target:
                self.selected = target
                self.engine.target(target.id)
                self.view = "find"
        elif key in ("y", "n", "s"):
            target = self.selected or (self.rows[self.cursor] if self.rows else None)
            if target:
                trust = {"y": Trust.MINE, "n": Trust.KNOWN, "s": Trust.SUSPECT}[key]
                self.engine.set_trust(target.id, trust)
                self.notify(f"{target.display_name()} marked as {trust.value}.")

    # -- drawing ---------------------------------------------------------

    def _draw(self) -> None:
        width = render.term_width()
        if self.view == "find":
            lines = self._find_view(width)
        elif self.view == "detail" and self.selected:
            lines = self._detail_view(self.selected, width)
        else:
            lines = self._list_view(width)

        out = [render.clear_screen(), *lines]
        if time.time() < self.message_until and self.message:
            out.append("")
            out.append(self.paint.c("  " + self.message, "cyan"))
        out.append("")
        out.append(self._footer())
        sys.stdout.write("\n".join(out))
        sys.stdout.flush()

    def _header(self, width: int) -> list[str]:
        e = self.engine
        stats = e.fusion.stats()
        live = [s for s in e.sensors if s.status.available]
        alerts = sum(1 for d in e.fusion.present() if d.risk >= 3)

        title = self.paint.c(" SWEEP ", "white", bold=True)
        bands = " ".join(
            self.paint.c(s.status.name, "green" if s.status.observations else "grey")
            for s in live
        ) or self.paint.c("no sensors available", "red")

        alert_txt = (
            self.paint.c(f"{alerts} alerts", "red", bold=True) if alerts
            else self.paint.c("no alerts", "green")
        )
        return [
            f"{title} {bands}",
            self.paint.dim(
                f"  {stats['present']} present / {stats['devices']} seen · "
                f"{stats['observations']} packets · location {e.context.epoch} · "
                f"{time.time() - e.started:.0f}s · "
            ) + alert_txt,
            self.paint.dim("─" * min(width, 120)),
        ]

    def _visible_devices(self) -> list[Device]:
        devices = self.engine.fusion.present()
        if not self.show_all:
            devices = [d for d in devices if d.trust is not Trust.BLOCKED]
        return sorted(devices, key=lambda d: (-d.risk, -(d.rssi if d.rssi is not None else -999)))

    def _list_view(self, width: int) -> list[str]:
        self.rows = self._visible_devices()
        lines = self._header(width)

        name_w = max(18, min(34, width - 66))
        header = (
            f"  {'':2}{'SIGNAL':<14} {'dBm':>5} {'~m':>6}  {'CLASS':<10} "
            f"{'NAME':<{name_w}} {'DETAIL'}"
        )
        lines.append(self.paint.dim(header))

        if not self.rows:
            lines.append("")
            lines.append(self.paint.dim("  nothing detected yet — sensors may still be starting"))
            return lines

        for i, dev in enumerate(self.rows[: max(4, _rows_available())]):
            marker = self.paint.c("▸", "white", bold=True) if i == self.cursor else " "
            risk = dev.risk
            bar = render.signal_bar(dev.rssi, 14)
            bar = self.paint.c(bar, "red" if risk >= 3 else "green" if (dev.rssi or -99) > -60 else "grey")
            dbm = f"{dev.rssi:.0f}" if dev.rssi is not None else "  ?"
            dist = dev.estimated_distance_m()
            dist_s = f"{dist:.1f}" if dist is not None and dist < 300 else "  ?"

            cls = dev.device_class.value
            cls_s = self.paint.c(cls[:10], CLASS_COLOR.get(cls, "grey"))
            name = render.truncate(dev.display_name(), name_w)
            if dev.trust is Trust.MINE:
                name = self.paint.dim(name)

            detail = dev.attrs.get("summary") or dev.attrs.get("class_reason") or ""
            if risk >= 2 and dev.findings:
                detail = self.paint.severity(dev.findings[0].title, risk)

            lines.append(
                f" {marker}{' '}{bar} {dbm:>5} {dist_s:>6}  "
                f"{render.pad(cls_s, 10)} {render.pad(name, name_w)} "
                f"{render.truncate(detail, max(10, width - name_w - 46))}"
            )
        return lines

    def _detail_view(self, dev: Device, width: int) -> list[str]:
        lines = self._header(width)
        lines.append("")
        lines.append("  " + self.paint.c(dev.display_name(), "white", bold=True) +
                     self.paint.dim(f"   id {dev.id}"))
        lines.append("")

        rows: list[tuple[str, str]] = [
            ("class", f"{dev.device_class.value}  ({dev.attrs.get('class_reason', '')})"),
            ("trust", dev.trust.value),
            ("vendor", dev.vendor or "unknown"),
            ("model", dev.model or "—"),
            ("os", dev.os_hint or "—"),
            ("bands", ", ".join(b.value for b in dev.bands)),
            ("signal", f"{dev.rssi:.1f} dBm" if dev.rssi is not None else "—"),
            ("distance", f"~{dev.estimated_distance_m()} m (rough, path-loss estimate)"),
            ("seen for", f"{(dev.last_seen - dev.first_seen) / 60:.1f} min"),
            ("locations", str(self.engine.context.epochs_for(dev.id))),
        ]
        for key, value in rows:
            lines.append(f"    {self.paint.dim(key.ljust(12))} {value}")

        lines.append("")
        lines.append("  " + self.paint.dim("radios"))
        for (band, address), t in dev.tracks.items():
            lines.append(
                f"    {self.paint.dim(band.ljust(12))} {address}  "
                f"{t.count} pkts  "
                f"{t.rssi_smoothed if t.rssi_smoothed is not None else '?'} dBm "
                f"(min {t.rssi_min} / max {t.rssi_max})"
            )

        interesting = _interesting_attrs(dev)
        if interesting:
            lines.append("")
            lines.append("  " + self.paint.dim("decoded"))
            for key, value in interesting:
                src = dev.attr_source.get(key, "")
                lines.append(
                    f"    {self.paint.dim(key.ljust(24))} {render.truncate(str(value), width - 46)} "
                    f"{self.paint.dim('(' + src + ')') if src else ''}"
                )

        links = self.engine.fusion.link_history(dev.id)
        if links:
            lines.append("")
            lines.append("  " + self.paint.dim("identity links (why we think these are one device)"))
            for link in links[-6:]:
                lines.append(
                    f"    {self.paint.dim(f'{link.confidence:.2f}')} "
                    f"{link.reason}  ← {link.previous_address}"
                )

        if dev.findings:
            lines.append("")
            lines.append("  " + self.paint.dim("findings"))
            for f in dev.findings:
                lines.append("    " + self.paint.severity(
                    f"[{f.severity_label.upper()}] {f.title}", f.severity))
                for chunk in _wrap(f.detail, width - 8):
                    lines.append("      " + self.paint.dim(chunk))
        return lines

    def _find_view(self, width: int) -> list[str]:
        dev = self.selected
        reading = self.engine.ranger.read(time.time())
        color = HEAT_COLOR[reading.heat]

        lines = [
            "",
            "  " + self.paint.c("FINDING", "white", bold=True) + "  " +
            (dev.display_name() if dev else "?"),
            "  " + self.paint.dim(
                f"{dev.address if dev else ''} · "
                f"{dev.device_class.value if dev else ''} · "
                f"{dev.vendor or 'unknown vendor' if dev else ''}"
            ),
            "",
        ]

        value = f"{reading.current_dbm:.0f}" if reading.current_dbm is not None else "?"
        for row in render.big_number(value):
            lines.append("   " + self.paint.c(row, color, bold=True))
        lines.append("   " + self.paint.dim("dBm"))
        lines.append("")

        bar_w = min(60, width - 12)
        frac = ((reading.current_dbm or -100) + 100) / 65.0
        lines.append("   " + self.paint.c(render.bar(frac, bar_w), color))
        lines.append("")
        lines.append("   " + self.paint.c(
            f"{reading.heat.arrow}  {HEAT_WORD[reading.heat]}", color, bold=True
        ))

        if reading.note:
            lines.append("   " + self.paint.dim(reading.note))
        else:
            direction = "closer" if reading.delta_db > 0 else "farther"
            lines.append("   " + self.paint.dim(
                f"{reading.delta_db:+.1f} dB vs the last {self.engine.ranger.baseline_window:.0f}s "
                f"→ roughly {reading.distance_ratio:.2f}× the distance ({direction})"
            ))

        lines.append("")
        lines.append("   " + self.paint.dim(
            f"~{reading.distance_m} m estimated · {reading.samples_recent} packets in the "
            f"last {self.engine.ranger.recent_window:.0f}s · {reading.samples_total} total · "
            f"{reading.age_s:.1f}s since last"
        ))
        lines.append("")
        lines.append("   " + self.paint.dim(
            "Walk a few metres, then stand still for ~10s and read the arrow. "
            "Signal bounces off walls and bodies — trust the trend, not one reading."
        ))
        return lines

    def _footer(self) -> str:
        if self.view == "find":
            keys = "[esc] back   [m] mark location   [q] quit"
        elif self.view == "detail":
            keys = "[f] find it   [y] mine  [n] known  [s] suspect   [esc] back   [q] quit"
        else:
            keys = ("[↑↓/jk] select   [enter] details   [f] find it   "
                    "[m] mark new location   [b] baseline   [a] all   [q] quit")
        if self._keys and not self._keys.interactive:
            keys = f"keys disabled — {self._keys.reason}. ctrl-c to stop"
        return self.paint.dim("  " + keys)


def _rows_available() -> int:
    import shutil

    return max(6, shutil.get_terminal_size((100, 30)).lines - 12)


#: Attributes not worth screen space in the detail view.
_BORING = {
    "summary", "class_reason", "signatures", "signature_labels", "mac",
    "rotating", "device_class", "class_hint", "services", "service_uuids",
}


def _interesting_attrs(dev: Device) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    # Named services first — the most human-meaningful field on BLE.
    if dev.attrs.get("services"):
        out.append(("services", ", ".join(dev.attrs["services"])))
    for key in sorted(dev.attrs):
        if key in _BORING or key.startswith(("mfr_data_", "svc_data_")):
            continue
        value = dev.attrs[key]
        if value in (None, "", [], {}):
            continue
        out.append((key, value))
    return out


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, max(20, width)) or [""]


class KeyReader:
    """Non-blocking single-key reader.

    POSIX only. Windows would need msvcrt; rather than half-implement it, the
    UI reports that keys are unavailable and keeps rendering.
    """

    def __init__(self) -> None:
        self.interactive = False
        #: Why keys are unavailable, so the footer can say something true.
        self.reason = ""
        self._old: Any = None

    def start(self) -> None:
        if not sys.stdin.isatty():
            self.reason = "stdin is not a terminal (output is being piped)"
            return
        try:
            import termios
            import tty
        except ImportError:
            self.reason = "termios is unavailable on this platform"
            return
        try:
            self._old = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
            self.interactive = True
        except Exception as exc:
            self.reason = f"could not put the terminal in raw mode: {exc}"
            self.interactive = False

    def stop(self) -> None:
        if self._old is not None:
            try:
                import termios

                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)
            except Exception:
                pass

    def drain(self) -> list[str]:
        if not self.interactive:
            return []
        import select

        keys: list[str] = []
        while select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch == "\x1b":
                # Arrow keys arrive as a 3-byte CSI sequence.
                seq = ch
                while select.select([sys.stdin], [], [], 0.01)[0] and len(seq) < 3:
                    seq += sys.stdin.read(1)
                keys.append(seq)
            else:
                keys.append(ch)
        return keys
