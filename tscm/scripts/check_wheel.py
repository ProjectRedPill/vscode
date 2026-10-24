#!/usr/bin/env python3
"""Verify a built wheel actually contains everything sweep needs at runtime.

The web UI is read from disk at runtime rather than embedded in the Python
source, which means the assets have to be declared as package data. When that
declaration is wrong the wheel still builds, still installs and still starts —
and then every page of the UI 404s. That failure is invisible to the test suite,
which imports from the source tree where the files are simply there.

So: inspect the archive, not the checkout.

    python scripts/check_wheel.py dist/sweep_tscm-0.1.0-py3-none-any.whl

Exit status is 0 only if every required member is present.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Every file the installed package reads from disk at runtime.
REQUIRED = [
    "sweep/web/static/index.html",
    "sweep/web/static/app.css",
    "sweep/web/static/app.js",
    "sweep/web/static/manifest.webmanifest",
    "sweep/cli.py",
    "sweep/web/server.py",
    "sweep/core/qr.py",
]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    wheel = Path(argv[1])
    if not wheel.is_file():
        print(f"no such wheel: {wheel}")
        return 2

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())

    print(f"{wheel.name}: {len(names)} members")
    missing = [path for path in REQUIRED if path not in names]
    for path in REQUIRED:
        print(("  ok    " if path in names else "  MISSING  ") + path)

    # The console script is what `sweep` on the PATH resolves to.
    entry = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
    if entry:
        with zipfile.ZipFile(wheel) as zf:
            text = zf.read(entry[0]).decode("utf-8")
        ok = "sweep.cli:main" in text
        print(("  ok    " if ok else "  MISSING  ") + "console script entry point")
        if not ok:
            missing.append("entry_points: sweep = sweep.cli:main")
    else:
        print("  MISSING  entry_points.txt")
        missing.append("entry_points.txt")

    if missing:
        print(f"\n{len(missing)} required member(s) missing from the wheel")
        return 1
    print("\nwheel contains everything needed at runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
