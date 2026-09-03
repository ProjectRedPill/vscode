"""Persistence.

Everything is local SQLite in the user's own directory. Nothing leaves the
machine — a counter-surveillance tool that phones home is a contradiction, and
the device inventory of a place you live is exactly the kind of data that should
never be uploaded anywhere.

Two things need to survive between runs:

  trust     your disposition on a device ("this is my phone"), keyed by alias so
            it survives across sessions and across MAC rotation of the *other*
            identifiers we linked to it.
  sightings a coarse history (device, epoch, first/last seen, best RSSI) that
            lets a later sweep answer "have I seen this before, and where?".

Raw observations are written only when explicitly asked for (`--log-raw`), since
a busy room produces tens of thousands of adverts an hour.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .models import Device, Finding, Trust

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust (
    alias TEXT PRIMARY KEY,          -- "band:address" or "linkkey:value"
    trust TEXT NOT NULL,
    label TEXT,
    updated REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    label TEXT,
    device_class TEXT,
    trust TEXT,
    vendor TEXT,
    model TEXT,
    os_hint TEXT,
    first_seen REAL,
    last_seen REAL,
    attrs TEXT
);

CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    session TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    band TEXT,
    address TEXT,
    first_seen REAL,
    last_seen REAL,
    best_rssi REAL,
    packets INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sightings_device ON sightings(device_id);
CREATE INDEX IF NOT EXISTS ix_sightings_address ON sightings(address);
-- One row per (device, session, epoch, radio). The periodic persist loop
-- UPSERTs into this; without the constraint it appended a fresh copy of every
-- track every 20 seconds, which ballooned the database and double-counted
-- history. The dedupe for databases created before the constraint existed
-- lives in _migrate().

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    session TEXT NOT NULL,
    ts REAL NOT NULL,
    rule TEXT NOT NULL,
    severity INTEGER NOT NULL,
    title TEXT,
    detail TEXT,
    evidence TEXT
);
CREATE INDEX IF NOT EXISTS ix_findings_device ON findings(device_id);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    ts REAL NOT NULL,
    band TEXT, sensor TEXT, address TEXT,
    rssi REAL, frequency_hz REAL,
    attrs TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_session ON observations(session);
"""


def default_path() -> Path:
    """Where the database lives, following each platform's own convention.

    Windows and macOS get their native locations rather than a Unix-style
    `~/.local/share`, which on Windows would drop a dotted directory into the
    user profile where nothing else lives and no backup tool expects it.
    """
    root = os.environ.get("SWEEP_HOME")
    if root:
        return Path(root).expanduser() / "sweep.db"

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "sweep" / "sweep.db"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "sweep" / "sweep.db"
    return Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser() / "sweep" / "sweep.db"


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    def _migrate(self) -> None:
        """Bring a pre-existing database up to the current shape.

        The unique index on sightings arrived after databases were already in
        the wild, and `CREATE UNIQUE INDEX` refuses to build over duplicates —
        so the duplicates the old append-only persist loop created are folded
        into one row (keeping the widest time span, best RSSI and highest
        packet count) before the index is created.
        """
        self.db.execute(
            """DELETE FROM sightings WHERE id NOT IN (
                   SELECT MAX(id) FROM sightings
                   GROUP BY device_id, session, epoch, band, address
               )"""
        )
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_sightings "
            "ON sightings(device_id, session, epoch, band, address)"
        )

        # Findings had the same append-per-persist problem: the same rule on
        # the same device re-recorded every cycle. One row per rule firing per
        # device per session, updated in place as severity or detail evolves.
        self.db.execute(
            """DELETE FROM findings WHERE id NOT IN (
                   SELECT MAX(id) FROM findings
                   GROUP BY device_id, session, rule
               )"""
        )
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_findings "
            "ON findings(device_id, session, rule)"
        )

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- trust -----------------------------------------------------------

    def set_trust(self, alias: str, trust: Trust, label: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO trust(alias, trust, label, updated) VALUES(?,?,?,?)",
            (alias.lower(), trust.value, label, time.time()),
        )
        self.db.commit()

    def get_trust(self, aliases: Iterable[str]) -> tuple[Trust, str | None]:
        """Strongest disposition among a device's aliases wins."""
        rows = []
        for alias in aliases:
            row = self.db.execute(
                "SELECT trust, label FROM trust WHERE alias = ?", (alias.lower(),)
            ).fetchone()
            if row:
                rows.append(row)
        if not rows:
            return Trust.UNSET, None
        order = [Trust.BLOCKED, Trust.MINE, Trust.KNOWN, Trust.SUSPECT, Trust.UNSET]
        best = min(rows, key=lambda r: order.index(Trust(r["trust"])))
        return Trust(best["trust"]), best["label"]

    def list_trust(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT alias, trust, label, updated FROM trust ORDER BY updated DESC"
        )]

    def clear_trust(self, alias: str) -> bool:
        cur = self.db.execute("DELETE FROM trust WHERE alias = ?", (alias.lower(),))
        self.db.commit()
        return cur.rowcount > 0

    # -- devices and sightings -------------------------------------------

    def save_device(self, dev: Device, session: str, epoch: int) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO devices
               (id, label, device_class, trust, vendor, model, os_hint,
                first_seen, last_seen, attrs)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                dev.id, dev.label, dev.device_class.value, dev.trust.value,
                dev.vendor, dev.model, dev.os_hint,
                dev.first_seen, dev.last_seen, json.dumps(_jsonable(dev.attrs)),
            ),
        )
        for (band, _key_address), track in dev.tracks.items():
            # Store the address as the sensor reported it, not the lower-cased
            # lookup key, so a report can be pasted straight back into a query.
            # UPSERT, not INSERT: persist() runs on a timer, and appending a
            # fresh row per track every 20 seconds turned a day of `sweep serve`
            # into hundreds of thousands of duplicate rows.
            self.db.execute(
                """INSERT INTO sightings
                   (device_id, session, epoch, band, address,
                    first_seen, last_seen, best_rssi, packets)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_id, session, epoch, band, address)
                   DO UPDATE SET
                       first_seen = MIN(first_seen, excluded.first_seen),
                       last_seen  = MAX(last_seen, excluded.last_seen),
                       best_rssi  = CASE
                           WHEN best_rssi IS NULL THEN excluded.best_rssi
                           WHEN excluded.best_rssi IS NULL THEN best_rssi
                           ELSE MAX(best_rssi, excluded.best_rssi)
                       END,
                       packets    = MAX(packets, excluded.packets)""",
                (
                    dev.id, session, epoch, band, track.address,
                    track.first_seen, track.last_seen, track.rssi_max, track.count,
                ),
            )

    def save_findings(self, device_id: str, session: str, findings: list[Finding]) -> None:
        for f in findings:
            self.db.execute(
                """INSERT INTO findings
                   (device_id, session, ts, rule, severity, title, detail, evidence)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_id, session, rule) DO UPDATE SET
                       ts = excluded.ts,
                       severity = excluded.severity,
                       title = excluded.title,
                       detail = excluded.detail,
                       evidence = excluded.evidence""",
                (
                    device_id, session, f.ts, f.rule, f.severity,
                    f.title, f.detail, json.dumps(_jsonable(f.evidence)),
                ),
            )

    def log_observation(self, session: str, obs: Any) -> None:
        self.db.execute(
            """INSERT INTO observations
               (session, ts, band, sensor, address, rssi, frequency_hz, attrs)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                session, obs.ts, obs.band.value, obs.sensor, obs.address,
                obs.rssi, obs.frequency_hz, json.dumps(_jsonable(obs.attrs)),
            ),
        )

    def commit(self) -> None:
        self.db.commit()

    # -- history queries -------------------------------------------------

    def history_for_address(self, address: str) -> list[dict[str, Any]]:
        # MAC case varies by platform (BlueZ upper, CoreBluetooth mixed), so
        # every address lookup is case-insensitive.
        rows = self.db.execute(
            """SELECT session, epoch, band, address, first_seen, last_seen,
                      best_rssi, packets
               FROM sightings WHERE address = ? COLLATE NOCASE
               ORDER BY last_seen DESC LIMIT 200""",
            (address,),
        )
        return [dict(r) for r in rows]

    def seen_before(self, address: str, session: str) -> int:
        """How many *previous* sessions this address appeared in."""
        row = self.db.execute(
            "SELECT COUNT(DISTINCT session) AS n FROM sightings "
            "WHERE address = ? COLLATE NOCASE AND session != ?",
            (address, session),
        ).fetchone()
        return int(row["n"]) if row else 0

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT session, MIN(first_seen) AS started, MAX(last_seen) AS ended,
                      COUNT(DISTINCT device_id) AS devices
               FROM sightings GROUP BY session ORDER BY started DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def findings_for_session(self, session: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT f.*, d.label, d.vendor, d.device_class
               FROM findings f LEFT JOIN devices d ON d.id = f.device_id
               WHERE f.session = ? ORDER BY f.severity DESC, f.ts""",
            (session,),
        )
        return [dict(r) for r in rows]


def _jsonable(value: Any) -> Any:
    """Coerce anything a decoder produced into something json can hold."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)
