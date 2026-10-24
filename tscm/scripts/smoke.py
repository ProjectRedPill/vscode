#!/usr/bin/env python3
"""End-to-end smoke test of an *installed* sweep, on any platform.

This exists because the unit suite is green on a machine with no radios at all,
and two of the worst defects found while building sweep were invisible to it:

  * the Wi-Fi sensor returned an un-awaited coroutine, so the band was dead on
    every platform while 129 tests passed;
  * `default_path()` used `sys.platform` without importing `sys`, so the very
    first run of `sweep doctor` — with no `--db` and no `SWEEP_HOME` — raised
    NameError. Every test passed an explicit database path, so nothing noticed.

Both are the same class of bug: the code paths a real user hits first are the
ones the tests mock away. So this drives the installed console script the way a
person does, over HTTP, and asserts on the output.

It is deliberately dependency-free (standard library only) and makes no
assumptions about available hardware: a machine with no radios must still pass.
Run it directly, or via `python -m` from anywhere:

    python scripts/smoke.py

Exit status is 0 only if every check passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("SWEEP_SMOKE_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
        failures.append(label)


# `sweep` emits UTF-8 on every platform. Reading it back needs saying so
# explicitly: `text=True` alone decodes with the *locale* codec, which on
# Windows is cp1252, and the first ● in the output then raises
# UnicodeDecodeError inside subprocess's reader thread — which leaves
# `.stdout` as None and turns every later check into a TypeError. The child
# was right and this harness was wrong, both times in the same run.
TEXT = {"encoding": "utf-8", "errors": "replace"}


def run_sweep(args: list[str], env: dict[str, str],
              timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["sweep", "--no-color", *args],
        capture_output=True, text=True, timeout=timeout, env=env, **TEXT,
    )
    # Belt and braces: anything that leaves a stream as None must become a
    # failed check with a readable reason, never a TypeError three lines later.
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, proc.stdout or "", proc.stderr or "")


def get(path: str, timeout: float = 10.0) -> tuple[int, str]:
    """Status and body, or (0, reason) — never an exception.

    A refused connection has to be a failed *check*, not a crash: when the
    server dies early, the remaining checks are the ones that say so.
    """
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    # This script prints em dashes and reports on glyphs; give its own output
    # the same treatment the tool gives its own, for the same reason.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    home = Path(tempfile.mkdtemp(prefix="sweep-smoke-"))
    env = dict(os.environ, SWEEP_HOME=str(home))

    print(f"sweep smoke test — python {sys.version.split()[0]} on {sys.platform}")
    print(f"database: {home}")

    # ---- 1. doctor, on the *default* database path -------------------------
    # No --db here. That is the point: this is the call that a first-time user
    # makes, and the one that the NameError above broke.
    doctor = run_sweep(["doctor"], env)
    print("\n$ sweep doctor")
    # Exit 2 means "nothing on this machine can sense anything", which is a
    # true answer on a CI runner with no radios and the whole point of having
    # an exit code. A crash is the failure, not an honest refusal.
    check("doctor exits 0, or 2 when the machine is blind",
          doctor.returncode in (0, 2), f"exit {doctor.returncode}")
    check("doctor names this host", "Host:" in doctor.stdout)
    check(
        "doctor reports at least one band either way",
        "sensing" in doctor.stdout or "not available" in doctor.stdout,
    )
    check("doctor does not traceback", "Traceback" not in doctor.stderr)
    # The UI is built from ● ○ ✔ · ─ →, none of which a Windows pipe's legacy
    # code page can encode — that combination used to raise UnicodeEncodeError
    # before a single band printed. Any non-ASCII arriving here proves the
    # console setup ran; which glyphs appear depends on what this machine has.
    check("doctor's non-ASCII glyphs survived the pipe",
          any(ord(ch) > 127 for ch in doctor.stdout),
          "output was pure ASCII — did the encoding silently fall back?")
    blind = doctor.returncode == 2
    if blind:
        print("  note  this machine senses nothing; coverage checks are the point")

    # ---- 2. the database was actually created where it was asked to be -----
    check("default database honours SWEEP_HOME", (home / "sweep.db").exists(),
          f"nothing at {home / 'sweep.db'}")

    # ---- 3. hwscan ---------------------------------------------------------
    hw = run_sweep(["hwscan"], env)
    print("\n$ sweep hwscan")
    check("hwscan exits 0", hw.returncode == 0, hw.stderr.strip()[-400:])
    check("hwscan does not traceback", "Traceback" not in hw.stderr)

    # ---- 4. a real (if short) sweep, and a machine-readable report ---------
    run = run_sweep(["--json", "sweep", "--duration", "4", "--quiet"],
                    env, timeout=180)
    print("\n$ sweep --json sweep --duration 4")
    # A sweep with no usable band refuses to run and says so: a sweep that
    # senses nothing would be a false all-clear. On such a machine there is no
    # report to parse, and that is the correct outcome, not a failure.
    if blind:
        check("a blind sweep refuses rather than reporting an all-clear",
              run.returncode == 2, f"exit {run.returncode}")
        check("and says why on stderr, leaving stdout clean",
              "No sensors are usable" in (run.stdout + run.stderr))
    else:
        check("sweep exits 0", run.returncode == 0, run.stderr.strip()[-400:])
        report: dict = {}
        try:
            report = json.loads(run.stdout)
            check("sweep emits valid JSON on stdout alone", True)
        except json.JSONDecodeError as exc:
            # Regression: the probe banner and progress line used to go to
            # stdout ahead of the report, so `> report.json` parsed as nothing.
            check("sweep emits valid JSON on stdout alone", False, str(exc))
        if report:
            for key in ("session", "coverage", "devices", "stats"):
                check(f"report has '{key}'", key in report)
            check("report states its own coverage", bool(report.get("coverage")))

    # ---- 5. the web UI, served and fetched over real HTTP ------------------
    # Loopback is treated as the host, so no token is needed here.
    print(f"\n$ sweep serve --port {PORT} (background)")
    server = subprocess.Popen(
        ["sweep", "--no-color", "serve", "--port", str(PORT),
         "--no-qr", "--duration", "60"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        **TEXT,
    )
    try:
        status, body = 0, ""
        deadline = time.time() + 45
        while time.time() < deadline:
            if server.poll() is not None:
                break
            try:
                status, body = get("/api/state", timeout=2.0)
                if status:
                    break
            except Exception:
                time.sleep(0.5)

        check("server answers /api/state", status == 200, f"status {status}")
        state: dict = {}
        if status == 200:
            try:
                state = json.loads(body)
            except json.JSONDecodeError as exc:
                check("/api/state is JSON", False, str(exc))
        if state:
            for key in ("devices", "stats", "sensors", "epoch"):
                check(f"state has '{key}'", key in state)

        status, page = get("/")
        check("index page is served", status == 200, f"status {status}")
        check("index page is the sweep UI", "sweep" in page.lower())

        # Static assets travel inside the wheel via package-data. When that is
        # misconfigured the install succeeds and every asset 404s, which is
        # invisible until someone opens the page.
        for asset in ("/app.css", "/app.js", "/manifest.webmanifest"):
            status, _ = get(asset)
            check(f"asset {asset} is served", status == 200, f"status {status}")

        status, _ = get("/api/capability")
        check("capability endpoint answers", status == 200, f"status {status}")

        status, _ = get("/api/nonsense")
        check("unknown route 404s rather than 500s", status == 404, f"status {status}")
    finally:
        server.terminate()
        try:
            out, _ = server.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            out, _ = server.communicate(timeout=20)
        check("server shut down without a traceback", "Traceback" not in (out or ""),
              (out or "")[-400:])

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
