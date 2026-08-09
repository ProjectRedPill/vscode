"""Browser tests for the web UI.

Skipped unless Playwright and a Chromium build are present, so the core suite
still runs anywhere. These exist because three of the defects found while
building this UI were invisible to HTTP-level tests:

  * `.sheet { display: flex }` silently defeated the `hidden` attribute, so the
    detail panel rendered open and empty on load;
  * filter chips were below the 44px touch minimum;
  * a missing favicon produced a console error on every page load.

All three are asserted here.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest

pytest.importorskip("playwright.async_api", reason="playwright not installed")

from playwright.async_api import async_playwright  # noqa: E402

from sweep.core.engine import Engine, EngineConfig  # noqa: E402
from sweep.core.models import Band, Observation  # noqa: E402
from sweep.web.server import WebServer  # noqa: E402


def _chromium() -> str | None:
    """Find a Chromium that Playwright can launch, or None to skip."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root), reverse=True):
            candidate = os.path.join(root, entry, "chrome-linux", "chrome")
            if os.path.exists(candidate):
                return candidate
    return shutil.which("chromium") or shutil.which("google-chrome")


CHROMIUM = _chromium()
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no chromium available")

IPHONE = {
    "viewport": {"width": 393, "height": 852},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}
DESKTOP = {"viewport": {"width": 1440, "height": 900}}


def adv(address, band=Band.BLE, rssi=-55.0, **attrs) -> Observation:
    return Observation(band=band, sensor="test", address=address, rssi=rssi,
                       attrs=attrs, name=attrs.get("name"))


@pytest.fixture
async def ui(tmp_path):
    """Engine + server + browser, with a couple of devices already present."""
    engine = Engine(EngineConfig(sensors=[], db_path=str(tmp_path / "ui.db")))
    await engine.probe()
    engine.handle(adv("AA:BB:CC:00:00:01", name="Test Phone", device_class="phone",
                      rssi=-44.0, summary="a phone"))
    engine.handle(adv("42:11:22:33:44:55", rssi=-51.0, device_class="tracker",
                      find_my_separated=True, tracker_network="Apple Find My",
                      vendor="Apple"))
    engine.evaluate_all()

    server = WebServer(engine, host="127.0.0.1", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    server.port = port

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROMIUM)
        yield engine, browser, f"http://127.0.0.1:{port}/"
        await browser.close()

    await server.stop()
    await engine.shutdown()


async def _open(browser, url, **ctx):
    context = await browser.new_context(**ctx)
    page = await context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("console",
            lambda m: problems.append(f"console.{m.type}: {m.text}")
            if m.type == "error" else None)
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(900)
    return context, page, problems


# ---------------------------------------------------------------------------

async def test_loads_clean_on_iphone(ui):
    _, browser, url = ui
    ctx, page, problems = await _open(browser, url, **IPHONE)
    try:
        assert problems == [], f"page reported errors: {problems}"
        assert await page.locator(".row").count() == 2
    finally:
        await ctx.close()


async def test_hidden_panels_are_actually_hidden_on_load(ui):
    """Regression: a class-level `display` beats the UA's `[hidden]` rule."""
    _, browser, url = ui
    ctx, page, _ = await _open(browser, url, **IPHONE)
    try:
        state = await page.evaluate("""() => ({
            sheet: !!document.querySelector('#sheet').offsetParent,
            find:  !!document.querySelector('#view-find').offsetParent,
            scrim: !!document.querySelector('#scrim').offsetParent,
            dock:  !!document.querySelector('.dock').offsetParent,
        })""")
        assert state == {"sheet": False, "find": False, "scrim": False, "dock": True}
    finally:
        await ctx.close()


async def test_no_horizontal_overflow_on_a_phone(ui):
    _, browser, url = ui
    ctx, page, _ = await _open(browser, url, **IPHONE)
    try:
        overflow = await page.evaluate(
            "() => document.documentElement.scrollWidth"
            " - document.documentElement.clientWidth")
        assert overflow == 0, f"page scrolls sideways by {overflow}px"
    finally:
        await ctx.close()


async def test_touch_targets_meet_the_44px_minimum(ui):
    _, browser, url = ui
    ctx, page, _ = await _open(browser, url, **IPHONE)
    try:
        undersized = await page.evaluate("""() => {
            const bad = [];
            for (const b of document.querySelectorAll('button, .dock-btn')) {
                if (b.offsetParent === null) continue;
                const r = b.getBoundingClientRect();
                if (r.height > 0 && r.height < 44) bad.push(b.className);
            }
            return bad;
        }""")
        assert undersized == [], f"targets below 44px: {undersized}"
    finally:
        await ctx.close()


async def test_opening_a_device_then_ranging(ui):
    engine, browser, url = ui
    ctx, page, problems = await _open(browser, url, **IPHONE)
    try:
        await page.locator(".row").first.click()
        await page.wait_for_timeout(400)
        assert await page.locator("#sheet").is_visible()
        assert await page.locator("#d-name").text_content()

        await page.locator("#d-find").click()
        await page.wait_for_timeout(800)
        assert await page.locator("#view-find").is_visible()
        assert engine.range_target is not None

        await page.locator("#find-back").click()
        await page.wait_for_timeout(600)
        assert engine.range_target is None
        assert problems == []
    finally:
        await ctx.close()


async def test_mark_location_reaches_the_engine(ui):
    engine, browser, url = ui
    ctx, page, _ = await _open(browser, url, **IPHONE)
    try:
        before = engine.context.epoch
        await page.locator("#btn-epoch").click()
        await page.wait_for_timeout(500)
        assert engine.context.epoch == before + 1
        assert await page.locator("#toast").is_visible()
    finally:
        await ctx.close()


async def test_filters_narrow_the_list(ui):
    _, browser, url = ui
    ctx, page, _ = await _open(browser, url, **IPHONE)
    try:
        assert await page.locator(".row").count() == 2
        await page.locator('.chip[data-filter="trackers"]').click()
        await page.wait_for_timeout(300)
        assert await page.locator(".row").count() == 1
        await page.locator('.chip[data-filter="cameras"]').click()
        await page.wait_for_timeout(300)
        assert await page.locator(".row").count() == 0
        assert await page.locator("#empty").is_visible()
    finally:
        await ctx.close()


async def test_desktop_uses_a_two_pane_layout(ui):
    _, browser, url = ui
    ctx, page, _ = await _open(browser, url, **DESKTOP)
    try:
        columns = await page.evaluate(
            "() => getComputedStyle(document.querySelector('.app')).gridTemplateColumns")
        assert len(columns.split()) == 2, f"expected two columns, got {columns!r}"
    finally:
        await ctx.close()


async def test_light_and_dark_both_paint_a_background(ui):
    """A transparent body inherits the host page's colour and looks broken."""
    _, browser, url = ui
    for scheme, expect in (("dark", "rgb(11, 15, 20)"), ("light", "rgb(246, 248, 250)")):
        ctx, page, _ = await _open(browser, url, color_scheme=scheme, **DESKTOP)
        try:
            bg = await page.evaluate("() => getComputedStyle(document.body).backgroundColor")
            assert bg == expect, f"{scheme}: expected {expect}, got {bg}"
        finally:
            await ctx.close()


async def test_live_updates_arrive_over_sse(ui):
    """The list must grow without a reload when a new device appears."""
    engine, browser, url = ui
    ctx, page, _ = await _open(browser, url, **DESKTOP)
    try:
        assert await page.locator(".row").count() == 2
        engine.handle(adv("11:22:33:44:55:66", name="Late Arrival", rssi=-60.0))
        engine.evaluate_all()
        await page.wait_for_function(
            "() => document.querySelectorAll('.row').length === 3", timeout=8000)
        assert await page.locator(".row", has_text="Late Arrival").count() == 1
    finally:
        await ctx.close()
