"""Web server: routing, auth, SSE framing and the action API.

Driven over real TCP against a real engine, because the parts most likely to
break — header parsing, SSE framing, token checks — are exactly the parts a
mocked transport would paper over.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sweep.core.engine import Engine, EngineConfig
from sweep.core.models import Band, Observation, Trust
from sweep.web.server import WebServer


def adv(address, **attrs) -> Observation:
    return Observation(
        band=Band.BLE, sensor="test", address=address,
        rssi=attrs.pop("rssi", -55.0), attrs=attrs, name=attrs.get("name"),
    )


class Client:
    """Minimal HTTP client — avoids adding a test-only dependency."""

    def __init__(self, port: int) -> None:
        self.port = port

    async def request(
        self, method: str, path: str, body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        lines = [f"{method} {path} HTTP/1.1", "Host: localhost"]
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        if body is not None:
            lines.append(f"Content-Length: {len(body)}")
            lines.append("Content-Type: application/json")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b""))
        await writer.drain()

        raw = await asyncio.wait_for(reader.read(-1), timeout=10)
        writer.close()

        head, _, payload = raw.partition(b"\r\n\r\n")
        head_lines = head.decode("latin-1").split("\r\n")
        status = int(head_lines[0].split()[1])
        resp_headers = {}
        for line in head_lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                resp_headers[k.strip().lower()] = v.strip()
        return status, resp_headers, payload

    async def json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        _, _, data = await self.request(method, path, body)
        return json.loads(data)


@pytest.fixture
async def served(tmp_path):
    """A running engine + web server on an ephemeral port."""
    engine = Engine(EngineConfig(sensors=[], db_path=str(tmp_path / "w.db")))
    await engine.probe()

    # Seed devices directly — this exercises the server, not the sensors.
    engine.handle(adv("AA:BB:CC:00:00:01", name="Test Phone", device_class="phone"))
    engine.handle(adv("42:11:22:33:44:55", name=None, device_class="tracker",
                      find_my_separated=True, tracker_network="Apple Find My"))
    engine.evaluate_all()

    server = WebServer(engine, host="127.0.0.1", port=0)
    await server.start()
    assert server._server is not None
    port = server._server.sockets[0].getsockname()[1]
    server.port = port

    yield engine, server, Client(port)

    await server.stop()
    await engine.shutdown()


# ---------------------------------------------------------------------------
# Static routes
# ---------------------------------------------------------------------------

async def test_index_is_served_with_a_strict_csp(served):
    _, _, client = served
    status, headers, body = await client.request("GET", "/")
    assert status == 200
    assert b"<title>sweep</title>" in body
    assert headers["content-type"].startswith("text/html")
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "default-src 'self'" in csp


async def test_assets_are_served_with_correct_types(served):
    _, _, client = served
    for path, expect in (
        ("/app.css", "text/css"),
        ("/app.js", "javascript"),
        ("/manifest.webmanifest", "application/manifest+json"),
    ):
        status, headers, body = await client.request("GET", path)
        assert status == 200, path
        assert expect in headers["content-type"], path
        assert body


async def test_the_page_references_nothing_external(served):
    """A counter-surveillance UI must not phone out for assets."""
    _, _, client = served
    _, _, body = await client.request("GET", "/")
    text = body.decode()
    assert "http://" not in text.replace("http://www.w3.org", "")
    assert "https://" not in text
    assert "//cdn" not in text


async def test_unknown_paths_404(served):
    _, _, client = served
    status, _, _ = await client.request("GET", "/nope")
    assert status == 404


async def test_static_route_rejects_traversal(served):
    _, _, client = served
    status, _, _ = await client.request("GET", "/../server.py")
    assert status == 404


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

async def test_state_contains_devices_sensors_and_coverage(served):
    _, _, client = served
    state = await client.json("GET", "/api/state")
    assert state["stats"]["devices"] == 2
    assert len(state["devices"]) == 2
    assert "sensors" in state and "range" in state
    device = state["devices"][0]
    for key in ("id", "name", "class", "risk", "bands", "findings", "attributes"):
        assert key in device


async def test_device_detail_and_404(served):
    engine, _, client = served
    dev = engine.fusion.get("Test Phone")
    assert dev is not None
    detail = await client.json("GET", f"/api/device/{dev.id}")
    assert detail["name"] == "Test Phone"
    assert "attribute_sources" in detail

    status, _, _ = await client.request("GET", "/api/device/nonexistent")
    assert status == 404


async def test_epoch_action_advances_the_location_counter(served):
    engine, _, client = served
    before = engine.context.epoch
    res = await client.json("POST", "/api/action", {"action": "epoch"})
    assert res["ok"] is True
    assert engine.context.epoch == before + 1


async def test_baseline_action_freezes_the_current_set(served):
    engine, _, client = served
    res = await client.json("POST", "/api/action", {"action": "baseline"})
    assert res["baseline"] == 2
    assert len(engine.context.baseline_ids) == 2


async def test_trust_action_persists_and_clears_risk(served):
    engine, _, client = served
    tracker = next(d for d in engine.fusion.devices.values() if d.risk >= 2)
    res = await client.json(
        "POST", "/api/action",
        {"action": "trust", "device": tracker.id, "trust": "mine", "label": "my tag"},
    )
    assert res["ok"] is True
    assert tracker.trust is Trust.MINE
    assert tracker.risk == 0


async def test_target_action_starts_and_stops_ranging(served):
    engine, _, client = served
    dev = engine.fusion.get("Test Phone")
    res = await client.json("POST", "/api/action", {"action": "target", "device": dev.id})
    assert res["target"] == dev.id
    assert engine.range_target == dev.id

    state = await client.json("GET", "/api/state")
    assert state["range"]["active"] is True
    assert "heat" in state["range"]

    await client.json("POST", "/api/action", {"action": "target", "device": None})
    assert engine.range_target is None


async def test_bad_action_and_bad_json_are_rejected_cleanly(served):
    _, _, client = served
    status, _, _ = await client.request(
        "POST", "/api/action", b'{"action":"nope"}')
    assert status == 400
    status, _, _ = await client.request("POST", "/api/action", b"{not json")
    assert status == 400


async def test_report_endpoint_returns_markdown_and_json(served):
    _, _, client = served
    status, headers, body = await client.request("GET", "/api/report")
    assert status == 200
    assert "markdown" in headers["content-type"]
    assert b"# Sweep report" in body
    assert b"## Coverage" in body

    status, headers, body = await client.request("GET", "/api/report?format=json")
    assert status == 200
    assert json.loads(body)["devices"]


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

async def test_event_stream_pushes_framed_state(served):
    _, server, _ = served
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()

    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    assert b"text/event-stream" in head
    # Without this, Safari and any buffering proxy hold the stream until it is
    # useless.
    assert b"X-Accel-Buffering: no" in head

    chunk = await asyncio.wait_for(reader.read(8192), timeout=5)
    assert b"event: state" in chunk
    payload = chunk.split(b"data: ", 1)[1].split(b"\n\n", 1)[0]
    assert json.loads(payload)["stats"]["devices"] == 2

    writer.close()


# ---------------------------------------------------------------------------
# Auth — only applies off loopback
# ---------------------------------------------------------------------------

async def test_loopback_needs_no_token(served):
    _, server, client = served
    assert server.token is None
    status, _, _ = await client.request("GET", "/api/state")
    assert status == 200


async def test_off_loopback_binding_mints_a_token_and_enforces_it(tmp_path):
    engine = Engine(EngineConfig(sensors=[], db_path=str(tmp_path / "w.db")))
    await engine.probe()
    server = WebServer(engine, host="0.0.0.0", port=0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    client = Client(port)
    try:
        assert server.token, "binding to a network interface must require a token"
        assert server.token in server.url

        status, _, _ = await client.request("GET", "/api/state")
        assert status == 401

        status, _, _ = await client.request("GET", "/api/state?t=wrong")
        assert status == 401

        status, _, _ = await client.request("GET", f"/api/state?t={server.token}")
        assert status == 200

        status, _, _ = await client.request(
            "GET", "/api/state", headers={"Authorization": f"Bearer {server.token}"})
        assert status == 200

        status, _, _ = await client.request(
            "GET", "/api/state", headers={"Cookie": f"sweep_token={server.token}"})
        assert status == 200
    finally:
        await server.stop()
        await engine.shutdown()


async def test_oversized_body_is_refused(served):
    _, _, client = served
    # Declared length beyond MAX_BODY: the server must not read it into memory.
    reader, writer = await asyncio.open_connection("127.0.0.1", client.port)
    writer.write(
        b"POST /api/action HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Length: 99999999\r\n\r\n"
    )
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(-1), timeout=10)
    writer.close()
    # Body is dropped, so the handler sees an empty payload and rejects it.
    assert b"400" in raw.split(b"\r\n", 1)[0] or b"error" in raw
