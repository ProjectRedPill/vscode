"""HTTP + Server-Sent Events server for the web UI.

Written directly on asyncio streams rather than on `http.server`, for one
reason that matters: the engine's fusion table is single-threaded by design and
has no locks. A threaded HTTP server would need them everywhere. Sharing the
engine's event loop means request handlers touch device state on the same task
that mutates it, and the whole class of race conditions never exists.

The protocol surface is small enough to hand-roll safely:

    GET  /                    the app shell
    GET  /app.css /app.js     static assets
    GET  /api/state           full snapshot (JSON)
    GET  /api/device/<id>     one device, everything known
    GET  /api/range           current ranging reading
    GET  /api/events          SSE stream: state pushed ~2/s, findings immediately
    POST /api/action          epoch / baseline / trust / target

Transport is plain HTTP. That is fine on loopback and *not* fine across a
network, which is why binding off-loopback requires an explicit flag and mints
a bearer token — see `_authorised`.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from ..core.engine import Engine, device_dict
from ..core.models import Trust
from ..ui import report

STATIC = Path(__file__).parent / "static"

#: Requests larger than this are refused outright. Nothing we accept is big.
MAX_BODY = 64 * 1024
MAX_HEADER = 16 * 1024


class WebServer:
    def __init__(
        self,
        engine: Engine,
        host: str = "127.0.0.1",
        port: int = 8787,
        token: str | None = None,
        push_interval: float = 0.5,
    ) -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self.push_interval = push_interval
        self.loopback = host in ("127.0.0.1", "::1", "localhost")
        # A token is only minted when it is actually needed. Forcing one on
        # loopback would just train people to paste tokens without reading.
        self.token = token or (None if self.loopback else secrets.token_urlsafe(16))
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._pending: list[dict[str, Any]] = []

    # -- lifecycle -------------------------------------------------------

    @property
    def url(self) -> str:
        host = "localhost" if self.host in ("127.0.0.1", "0.0.0.0") else self.host
        base = f"http://{host}:{self.port}/"
        return f"{base}?t={self.token}" if self.token else base

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.engine.on_finding(self._queue_finding)

    async def stop(self) -> None:
        for writer in list(self._clients):
            try:
                writer.close()
            except Exception:
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    def _queue_finding(self, device: Any, finding: Any) -> None:
        """Findings are pushed the moment they fire, not on the next poll."""
        self._pending.append({
            "type": "finding",
            "device_id": device.id,
            "device": device.display_name(),
            "severity": finding.severity,
            "severity_label": finding.severity_label,
            "title": finding.title,
            "detail": finding.detail,
            "ts": finding.ts,
        })
        del self._pending[:-32]

    # -- request handling ------------------------------------------------

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await self._read_request(reader)
            if request is None:
                return
            method, target, headers, body = request
            parsed = urlparse(target)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)

            if not self._authorised(headers, query):
                await self._send(writer, 401, b"unauthorised\n", "text/plain")
                return

            if path == "/api/events" and method == "GET":
                await self._stream_events(writer)
                return

            status, payload, ctype, extra = await self._route(method, path, query, body)
            await self._send(writer, status, payload, ctype, extra)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # a bad request must not take down the server
            try:
                await self._send(
                    writer, 500,
                    json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode(),
                    "application/json",
                )
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes] | None:
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=15.0
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, ConnectionResetError):
            return None
        if len(head) > MAX_HEADER:
            return None

        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        if len(parts) < 2:
            return None
        method, target = parts[0].upper(), parts[1]

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        body = b""
        length = int(headers.get("content-length") or 0)
        if 0 < length <= MAX_BODY:
            try:
                body = await asyncio.wait_for(reader.readexactly(length), timeout=15.0)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return None
        return method, target, headers, body

    def _authorised(self, headers: dict[str, str], query: dict[str, list[str]]) -> bool:
        if self.token is None:
            return True
        supplied = (
            (query.get("t") or [""])[0]
            or headers.get("authorization", "").removeprefix("Bearer ").strip()
            or _cookie(headers.get("cookie", ""), "sweep_token")
        )
        # Constant-time: the token is short and an attacker on the LAN can make
        # a great many guesses.
        return secrets.compare_digest(supplied, self.token)

    # -- routes ----------------------------------------------------------

    async def _route(
        self, method: str, path: str, query: dict[str, list[str]], body: bytes
    ) -> tuple[int, bytes, str, dict[str, str]]:
        extra: dict[str, str] = {}
        if self.token:
            # Set once so in-app fetches work without threading the token
            # through every URL.
            extra["Set-Cookie"] = (
                f"sweep_token={self.token}; Path=/; SameSite=Strict; Max-Age=86400"
            )

        if method == "GET" and path in ("/", "/index.html"):
            return 200, _static("index.html"), "text/html; charset=utf-8", extra
        if method == "GET" and path.lstrip("/") in ("app.css", "app.js", "manifest.webmanifest"):
            name = path.lstrip("/")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if name.endswith(".webmanifest"):
                ctype = "application/manifest+json"
            return 200, _static(name), ctype, extra

        if method == "GET" and path == "/api/state":
            return 200, self._state_bytes(), "application/json", extra

        if method == "GET" and path.startswith("/api/device/"):
            needle = path[len("/api/device/"):]
            device = self.engine.fusion.get(needle)
            if device is None:
                return 404, b'{"error":"no such device"}', "application/json", extra
            payload = device_dict(device, self.engine)
            return 200, _json(payload), "application/json", extra

        if method == "GET" and path == "/api/range":
            return 200, _json(self._range_payload()), "application/json", extra

        if method == "GET" and path == "/api/report":
            fmt = (query.get("format") or ["markdown"])[0]
            self.engine.evaluate_all()
            if fmt == "json":
                return 200, report.json_report(self.engine).encode(), "application/json", extra
            text = report.markdown_report(self.engine).encode()
            extra["Content-Disposition"] = 'attachment; filename="sweep-report.md"'
            return 200, text, "text/markdown; charset=utf-8", extra

        if method == "POST" and path == "/api/action":
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return 400, b'{"error":"bad json"}', "application/json", extra
            return (*self._action(payload), extra)

        if method == "OPTIONS":
            return 204, b"", "text/plain", extra

        return 404, b'{"error":"not found"}', "application/json", extra

    def _action(self, payload: dict[str, Any]) -> tuple[int, bytes, str]:
        action = str(payload.get("action") or "")
        engine = self.engine

        if action == "epoch":
            epoch = engine.new_epoch()
            return 200, _json({"ok": True, "epoch": epoch}), "application/json"

        if action == "baseline":
            count = engine.mark_baseline()
            return 200, _json({"ok": True, "baseline": count}), "application/json"

        if action == "trust":
            try:
                trust = Trust(str(payload.get("trust")))
            except ValueError:
                return 400, b'{"error":"bad trust value"}', "application/json"
            device = engine.set_trust(
                str(payload.get("device") or ""), trust, payload.get("label")
            )
            if device is None:
                return 404, b'{"error":"no such device"}', "application/json"
            return 200, _json({"ok": True, "device": device_dict(device, engine)}), "application/json"

        if action == "target":
            needle = payload.get("device")
            device = engine.target(str(needle) if needle else None)
            if needle and device is None:
                return 404, b'{"error":"no such device"}', "application/json"
            return 200, _json({
                "ok": True,
                "target": device.id if device else None,
                "name": device.display_name() if device else None,
            }), "application/json"

        if action == "env":
            from ..core.rssi import path_loss_env_factor

            engine.ranger.env_factor = path_loss_env_factor(str(payload.get("env") or "office"))
            return 200, _json({"ok": True, "env_factor": engine.ranger.env_factor}), "application/json"

        if action == "stop":
            engine.stop.set()
            return 200, b'{"ok":true}', "application/json"

        return 400, b'{"error":"unknown action"}', "application/json"

    # -- payloads --------------------------------------------------------

    def _state_bytes(self) -> bytes:
        return _json(self._state())

    def _state(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
        snap["range"] = self._range_payload()
        snap["baseline_set"] = bool(self.engine.context.baseline_ids)
        return snap

    def _range_payload(self) -> dict[str, Any]:
        target_id = self.engine.range_target
        if not target_id:
            return {"active": False}
        reading = self.engine.ranger.read(time.time())
        device = self.engine.fusion.devices.get(target_id)
        return {
            "active": True,
            "device_id": target_id,
            "name": device.display_name() if device else "?",
            "address": device.address if device else None,
            "vendor": device.vendor if device else None,
            "class": device.device_class.value if device else None,
            "heat": reading.heat.value,
            "arrow": reading.heat.arrow,
            "current_dbm": reading.current_dbm,
            "baseline_dbm": reading.baseline_dbm,
            "delta_db": reading.delta_db,
            "distance_m": reading.distance_m,
            "distance_ratio": reading.distance_ratio,
            "samples_recent": reading.samples_recent,
            "samples_total": reading.samples_total,
            "age_s": reading.age_s,
            "note": reading.note,
            # The sparkline the UI draws: recent smoothed RSSI, oldest first.
            "history": _range_history(self.engine),
        }

    # -- SSE -------------------------------------------------------------

    async def _stream_events(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache, no-transform\r\n"
            b"Connection: keep-alive\r\n"
            # Safari and any intermediary proxy will buffer an event stream
            # into uselessness without this.
            b"X-Accel-Buffering: no\r\n"
            b"\r\n"
            b"retry: 2000\n\n"
        )
        await writer.drain()
        self._clients.add(writer)

        last_beat = time.time()
        try:
            while not self.engine.stop.is_set():
                while self._pending:
                    event = self._pending.pop(0)
                    writer.write(
                        f"event: finding\ndata: {json.dumps(event, default=str)}\n\n".encode()
                    )

                writer.write(b"event: state\ndata: " + self._state_bytes() + b"\n\n")

                now = time.time()
                if now - last_beat > 15:
                    # Comment frames keep iOS Safari from dropping an idle
                    # connection when the screen locks.
                    writer.write(b": keepalive\n\n")
                    last_beat = now

                await writer.drain()
                await asyncio.sleep(self.push_interval)
        except (ConnectionResetError, BrokenPipeError, RuntimeError):
            pass
        finally:
            self._clients.discard(writer)

    # -- response --------------------------------------------------------

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        reason = {
            200: "OK", 204: "No Content", 400: "Bad Request",
            401: "Unauthorized", 404: "Not Found", 500: "Internal Server Error",
        }.get(status, "OK")
        headers = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            # The UI is entirely self-contained — no CDN, no external font, no
            # remote anything — so a tight CSP costs nothing and removes script
            # injection as a category. `style-src` allows inline because the UI
            # sets bar widths and heat colours through style properties; the
            # value of the policy here is `script-src`, which stays strict.
            "Content-Security-Policy: default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options: nosniff",
            "Referrer-Policy: no-referrer",
            "Cache-Control: no-store",
        ]
        for key, value in (extra or {}).items():
            headers.append(f"{key}: {value}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        await writer.drain()


# ---------------------------------------------------------------------------

_STATIC_CACHE: dict[str, bytes] = {}


def _static(name: str) -> bytes:
    if name not in _STATIC_CACHE:
        path = (STATIC / name).resolve()
        # Defensive: `name` is matched against a fixed allow-list upstream, but
        # a traversal check next to the file read is where it belongs.
        if not str(path).startswith(str(STATIC.resolve())) or not path.is_file():
            return b"not found"
        _STATIC_CACHE[name] = path.read_bytes()
    return _STATIC_CACHE[name]


def _json(payload: Any) -> bytes:
    return json.dumps(payload, default=str).encode()


def _cookie(header: str, name: str) -> str:
    for chunk in header.split(";"):
        key, _, value = chunk.strip().partition("=")
        if key == name:
            return value
    return ""


def _range_history(engine: Engine, points: int = 60) -> list[float]:
    target = engine.range_target
    if not target:
        return []
    device = engine.fusion.devices.get(target)
    track = device.primary_track if device else None
    if track is None:
        return []
    return [round(rssi, 1) for _, rssi in track.history[-points:]]
