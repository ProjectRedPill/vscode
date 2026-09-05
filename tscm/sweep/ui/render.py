"""Terminal rendering primitives — no third-party dependencies.

The finder view exists to be read at arm's length while you are walking around
holding a laptop, so it uses oversized digits and one colour that means one
thing. Everything else is a conventional table.
"""

from __future__ import annotations

import os
import shutil

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

FG = {
    "red": "\033[38;5;196m",
    "orange": "\033[38;5;208m",
    "yellow": "\033[38;5;226m",
    "green": "\033[38;5;46m",
    "cyan": "\033[38;5;51m",
    "blue": "\033[38;5;39m",
    "grey": "\033[38;5;245m",
    "white": "\033[38;5;255m",
    "magenta": "\033[38;5;201m",
}

SEVERITY_COLOR = {0: "grey", 1: "cyan", 2: "yellow", 3: "orange", 4: "red"}

#: 5-row block digits for the ranging readout.
_BIG = {
    "0": ("█████", "█   █", "█   █", "█   █", "█████"),
    "1": ("   ██", "    █", "    █", "    █", "    █"),
    "2": ("█████", "    █", "█████", "█    ", "█████"),
    "3": ("█████", "    █", " ████", "    █", "█████"),
    "4": ("█   █", "█   █", "█████", "    █", "    █"),
    "5": ("█████", "█    ", "█████", "    █", "█████"),
    "6": ("█████", "█    ", "█████", "█   █", "█████"),
    "7": ("█████", "    █", "   █ ", "  █  ", "  █  "),
    "8": ("█████", "█   █", "█████", "█   █", "█████"),
    "9": ("█████", "█   █", "█████", "    █", "█████"),
    "-": ("     ", "     ", "█████", "     ", "     "),
    ".": ("     ", "     ", "     ", "     ", "  ██ "),
    " ": ("     ", "     ", "     ", "     ", "     "),
    "?": ("█████", "    █", "  ██ ", "     ", "  █  "),
}


def _enable_windows_vt() -> bool:
    """Turn on ANSI escape processing in a Windows console.

    Windows Terminal handles VT sequences natively, but the legacy conhost that
    still backs plain `cmd.exe` and older PowerShell windows does not — without
    this call the UI prints raw escape codes like `<-[38;5;46m` over everything,
    and the QR code becomes unreadable noise. Returns whether VT is usable.
    """
    import ctypes

    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    STD_OUTPUT_HANDLE = -11
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not (hasattr(os.sys.stdout, "isatty") and os.sys.stdout.isatty()):  # type: ignore[attr-defined]
        return False
    if os.sys.platform.startswith("win"):  # type: ignore[attr-defined]
        # Windows Terminal sets WT_SESSION and always supports VT; otherwise ask
        # the console to enable it and believe the answer.
        return bool(os.environ.get("WT_SESSION")) or _enable_windows_vt()
    return True


class Painter:
    """Wraps colour so a redirected stdout produces clean plain text."""

    def __init__(self, color: bool | None = None) -> None:
        self.color = supports_color() if color is None else color

    def c(self, text: str, color: str, bold: bool = False) -> str:
        if not self.color:
            return text
        return f"{BOLD if bold else ''}{FG.get(color, '')}{text}{RESET}"

    def dim(self, text: str) -> str:
        return f"{DIM}{text}{RESET}" if self.color else text

    def severity(self, text: str, level: int) -> str:
        return self.c(text, SEVERITY_COLOR.get(level, "grey"), bold=level >= 3)


def term_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def big_number(text: str) -> list[str]:
    """Render a short string as five rows of block glyphs."""
    rows = ["", "", "", "", ""]
    for ch in text:
        glyph = _BIG.get(ch, _BIG["?"])
        for i in range(5):
            rows[i] += glyph[i] + " "
    return rows


def bar(fraction: float, width: int, *, filled: str = "█", empty: str = "░") -> str:
    fraction = max(0.0, min(1.0, fraction))
    n = int(round(fraction * width))
    return filled * n + empty * (width - n)


def signal_bar(rssi: float | None, width: int = 20) -> str:
    """RSSI as a proportion of the useful -100..-35 dBm range."""
    if rssi is None:
        return "░" * width
    return bar((rssi + 100.0) / 65.0, width)


def truncate(text: str, width: int) -> str:
    if width <= 1:
        return text[:width]
    return text if len(text) <= width else text[: width - 1] + "…"


def table(
    headers: list[str],
    rows: list[list[str]],
    widths: list[int] | None = None,
    painter: Painter | None = None,
) -> list[str]:
    """Fixed-width table. Widths are hints; the last column absorbs slack."""
    p = painter or Painter()
    if widths is None:
        widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
                  for i, h in enumerate(headers)]

    out = [
        "  ".join(p.dim(h.ljust(w)[:w]) for h, w in zip(headers, widths)),
        p.dim("─" * (sum(widths) + 2 * (len(widths) - 1))),
    ]
    for row in rows:
        out.append("  ".join(
            truncate(cell, w).ljust(w + (len(cell) - len(_strip(cell))))
            for cell, w in zip(row, widths)
        ))
    return out


def _strip(text: str) -> str:
    """Length of `text` ignoring ANSI codes, for correct padding."""
    import re

    return re.sub(r"\033\[[0-9;]*m", "", text)


def visible_len(text: str) -> int:
    return len(_strip(text))


def pad(text: str, width: int) -> str:
    """Left-justify accounting for ANSI escapes."""
    deficit = width - visible_len(text)
    return text + " " * max(0, deficit)


def clear_screen() -> str:
    return "\033[2J\033[H"


def hide_cursor() -> str:
    return "\033[?25l"


def show_cursor() -> str:
    return "\033[?25h"
