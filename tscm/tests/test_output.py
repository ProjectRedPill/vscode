"""What the CLI writes, and where it writes it.

Both of these cover bugs that every unit test missed and a Windows CI runner
found immediately — because on a runner, stdout is always a pipe, which is the
one case the terminal-shaped tests never exercise.
"""

from __future__ import annotations

import argparse
import io
import sys

import pytest

from sweep import cli
from sweep.ui import render

# Every non-ASCII glyph the UI can print: band markers, the selection arrow,
# the signal bar, the big finder digits and the QR module.
GLYPHS = "● ○ ✔ · ▸ ░ █ ← → ×"  # noqa: RUF001 — every one of these is deliberate


def test_a_legacy_code_page_cannot_take_the_ui_glyphs():
    """The bug itself, reproduced: this is what Windows does to a pipe."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        stream.write("●")
        stream.flush()


def test_ensure_unicode_output_makes_a_legacy_stream_safe(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(render.sys, "stdout", stream)
    monkeypatch.setattr(render.sys, "stderr", stream)

    render.ensure_unicode_output()

    stream.write(GLYPHS)        # must not raise
    stream.flush()
    assert stream.encoding.lower().replace("-", "") == "utf8"
    assert "●" in raw.getvalue().decode("utf-8")


def test_ensure_unicode_output_tolerates_a_stream_it_cannot_reconfigure(monkeypatch):
    """pytest's capture, a StringIO, a closed stream: none may raise."""
    monkeypatch.setattr(render.sys, "stdout", io.StringIO())
    monkeypatch.setattr(render.sys, "stderr", None)
    render.ensure_unicode_output()


@pytest.mark.parametrize("json_flag,out,expected", [
    (True, None, "stderr"),     # report goes to stdout, so chatter must not
    (True, "r.json", "stdout"),  # report goes to a file, stdout is free
    (False, None, "stdout"),
    (False, "r.md", "stdout"),
])
def test_progress_output_never_lands_in_a_json_report(json_flag, out, expected):
    args = argparse.Namespace(json=json_flag, out=out)
    stream = cli.chatter_stream(args)
    assert stream is (sys.stderr if expected == "stderr" else sys.stdout)


def test_chatter_stream_copes_with_a_command_that_has_no_such_flags():
    """`sweep doctor` has neither --json nor --out."""
    assert cli.chatter_stream(argparse.Namespace()) is sys.stdout
