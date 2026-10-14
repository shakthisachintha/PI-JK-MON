"""SQLite storage: migrations, inserts, queries, rollup and retention (§7).

All methods are synchronous; async callers wrap them in asyncio.to_thread().
One writer connection, one reader connection, each guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .state import CellSnapshot

log = logging.getLogger(__name__)

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
RAW_SOURCE_MAX_SPAN_MS = 6 * HOUR_MS
ROLLUP_CHUNK_MS = 6 * HOUR_MS
EVENTS_RETENTION_DAYS = 90
VACUUM_INTERVAL_MS = 30 * DAY_MS
EXPORT_MAX_SPAN_MS = 31 * DAY_MS

HISTORY_FIELDS = (
    "voltage",
    "current",
    "power",
    "soc",
    "temp_t1",
    "temp_t2",
    "temp_mos",
    "delta_cell_voltage",
    "min_cell_voltage",
    "max_cell_voltage",
    "balance_current",
    "capacity_remain",
)
BAND_FIELDS = ("voltage", "current")  # also get _min/_max

# Column in samples_1m that backs each history field.
_ROLLUP_COLUMN = {
    "voltage": "voltage_avg",
    "current": "current_avg",
    "power": "power_avg",
    "soc": "soc_last",
    "temp_t1": "temp_t1_max",
    "temp_t2": "temp_t2_max",
    "temp_mos": "temp_mos_max",
    "delta_cell_voltage": "delta_cell_max",
    "min_cell_voltage": "cell_min",
    "max_cell_voltage": "cell_max",
    "balance_current": "balance_current_avg",
    "capacity_remain": "capacity_remain_last",
}

SAMPLE_COLUMNS = (
    "ts",
    "voltage",
    "current",
    "power",
    "soc",
    "capacity_remain",
    "nominal_capacity",
    "cycle_count",
    "cycle_capacity",
    "avg_cell_voltage",
    "delta_cell_voltage",
    "min_cell_voltage",
    "max_cell_voltage",
    "balance_current",
    "temp_t1",
    "temp_t2",
    "temp_mos",
    "charge_on",
    "discharge_on",
    "balance_on",
    "balancing_action",
    "bms_uptime_s",
    "cell_voltages",
    "wire_resist",
)


class QueryError(ValueError):
    """Bad query parameters; maps to HTTP 400."""


# --- Migrations ---------------------------------------------------------------


def _exec_script(c: sqlite3.Connection, script: str) -> None:
    """Run statements one by one (executescript would commit the migration transaction)."""
    stmt = ""
    for line in script.splitlines(keepends=True):
        stmt += line
        if sqlite3.complete_statement(stmt):
            c.execute(stmt)
            stmt = ""
    if stmt.strip():
        raise ValueError(f"incomplete SQL statement: {stmt!r}")


def _migration_1(c: sqlite3.Connection) -> None:
    _exec_script(
        c,
        """
        CREATE TABLE samples (
          ts                 INTEGER PRIMARY KEY,
          voltage            REAL NOT NULL,
          current            REAL NOT NULL,
          power              REAL NOT NULL,
          soc                INTEGER NOT NULL,
          capacity_remain    REAL,
          nominal_capacity   REAL,
          cycle_count        INTEGER,
          cycle_capacity     REAL,
          avg_cell_voltage   REAL,
          delta_cell_voltage REAL,
          min_cell_voltage   REAL,
          max_cell_voltage   REAL,
          balance_current    REAL,
          temp_t1            REAL,
          temp_t2            REAL,
          temp_mos           REAL,
          charge_on          INTEGER NOT NULL,
          discharge_on       INTEGER NOT NULL,
          balance_on         INTEGER NOT NULL,
          balancing_action   INTEGER,
          bms_uptime_s       INTEGER,
          cell_voltages      TEXT NOT NULL,
          wire_resist        TEXT NOT NULL
        );

        CREATE TABLE samples_1m (
          ts                 INTEGER PRIMARY KEY,
          n                  INTEGER NOT NULL,
          voltage_avg REAL, voltage_min REAL, voltage_max REAL,
          current_avg REAL, current_min REAL, current_max REAL,
          power_avg   REAL,
          soc_last    INTEGER,
          temp_t1_max REAL, temp_t2_max REAL, temp_mos_max REAL,
          cell_min    REAL, cell_max REAL,
          delta_cell_max REAL,
          cell_voltages_avg TEXT,
          -- additions beyond spec §7.1 so every /api/history field works on long ranges
          balance_current_avg  REAL,
          capacity_remain_last REAL
        );

        CREATE TABLE settings_history (
          ts   INTEGER PRIMARY KEY,
          data TEXT NOT NULL
        );

        CREATE TABLE device_info (
          id         INTEGER PRIMARY KEY CHECK (id = 1),
          updated_ts INTEGER NOT NULL,
          data       TEXT NOT NULL
        );

        CREATE TABLE events (
          id     INTEGER PRIMARY KEY AUTOINCREMENT,
          ts     INTEGER NOT NULL,
          type   TEXT NOT NULL,
          detail TEXT
        );
        CREATE INDEX events_ts ON events(ts);

        CREATE TABLE meta (
          key   TEXT PRIMARY KEY,
          value TEXT
        );
        """,
    )


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migration_1]


def _connect(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def migrate(c: sqlite3.Connection) -> int:
    version = c.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in enumerate(MIGRATIONS[version:], start=version + 1):
        log.info("applying DB migration %d", target)
        c.execute("BEGIN")
        try:
            fn(c)
            c.execute(f"PRAGMA user_version={target}")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


# --- Helpers ------------------------------------------------------------------


def _avg(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _max(xs: list):
    xs = [x for x in xs if x is not None]
    return max(xs) if xs else None


def _min(xs: list):
    xs = [x for x in xs if x is not None]
    return min(xs) if xs else None


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def _per_index_avg(arrays: list[list[float]], weights: list[int] | None = None) -> list[float]:
    """Average arrays element-wise; arrays may differ in length."""
    width = max((len(a) for a in arrays), default=0)
    sums = [0.0] * width
    counts = [0] * width
    for k, arr in enumerate(arrays):
        w = weights[k] if weights else 1
        for i, v in enumerate(arr):
            if v is not None:
                sums[i] += v * w
                counts[i] += w
    return [round(sums[i] / counts[i], 4) if counts[i] else None for i in range(width)]


# --- Store --------------------------------------------------------------------


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._w = _connect(path)
        self._wlock = threading.Lock()
        migrate(self._w)
        self._r = _connect(path)
        self._r.execute("PRAGMA query_only=ON")
        self._rlock = threading.Lock()
        self.write_ok = True

    def close(self) -> None:
        with self._wlock, self._rlock:
            self._r.close()
            self._w.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._wlock:
            try:
                self._w.execute("BEGIN")
                yield self._w
                self._w.execute("COMMIT")
                self.write_ok = True
            except sqlite3.Error:
                self.write_ok = False
                if self._w.in_transaction:
                    self._w.execute("ROLLBACK")
                raise
            except BaseException:
                if self._w.in_transaction:
                    self._w.execute("ROLLBACK")
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._rlock:
            yield self._r

    def check_writable(self) -> bool:
        try:
            with self._wlock:
                self._w.execute("BEGIN IMMEDIATE")
                self._w.execute("ROLLBACK")
            return self.write_ok
        except sqlite3.Error:
            return False

    # --- writes ---------------------------------------------------------------

    def insert_sample(self, ts: int, snap: CellSnapshot) -> None:
        c = snap.cells
        row = (
            ts,
            c.voltage,
            c.current,
            c.power,
            c.soc,
            c.capacity_remain,
            c.nominal_capacity,
            c.cycle_count,
            c.cycle_capacity,
            c.avg_cell_voltage,
            c.delta_cell_voltage,
            snap.min_cell_voltage,
            snap.max_cell_voltage,
            c.balance_current,
            c.temp_t1,
            c.temp_t2,
            c.temp_mos,
            int(c.charge_on),
            int(c.discharge_on),
            int(c.balance_on),
            c.balancing_action,
            c.bms_uptime_s,
            json.dumps(snap.cell_voltages),
            json.dumps(snap.wire_resist),
        )
        placeholders = ",".join("?" * len(SAMPLE_COLUMNS))
        with self._tx() as w:
            w.execute(f"INSERT OR IGNORE INTO samples ({','.join(SAMPLE_COLUMNS)}) VALUES ({placeholders})", row)

    def insert_samples_bulk(self, rows: list[tuple]) -> None:
        """Insert pre-built sample tuples (SAMPLE_COLUMNS order). Used by --seed-days."""
        placeholders = ",".join("?" * len(SAMPLE_COLUMNS))
        with self._tx() as w:
            w.executemany(f"INSERT OR IGNORE INTO samples ({','.join(SAMPLE_COLUMNS)}) VALUES ({placeholders})", rows)

    def insert_settings_if_changed(self, ts: int, data: dict) -> list[str] | None:
        """Insert settings when they differ from the last stored row.

        Returns the changed keys ([] for the very first row), or None if unchanged.
        """
        with self._tx() as w:
            prev = w.execute("SELECT data FROM settings_history ORDER BY ts DESC LIMIT 1").fetchone()
            prev_data = json.loads(prev["data"]) if prev else None
            if prev_data == data:
                return None
            if prev_data is None:
                changed: list[str] = []
            else:
                changed = sorted(k for k in set(data) | set(prev_data) if prev_data.get(k) != data.get(k))
            w.execute("INSERT OR REPLACE INTO settings_history (ts, data) VALUES (?, ?)", (ts, json.dumps(data)))
            return changed

    def upsert_device_info(self, ts: int, data: dict) -> None:
        with self._tx() as w:
            w.execute(
                "INSERT INTO device_info (id, updated_ts, data) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_ts=excluded.updated_ts, data=excluded.data",
                (ts, json.dumps(data)),
            )

    def add_event(self, ts: int, type_: str, detail: str | None = None) -> None:
        with self._tx() as w:
            w.execute("INSERT INTO events (ts, type, detail) VALUES (?, ?, ?)", (ts, type_, detail))

    def _meta_get(self, c: sqlite3.Connection, key: str) -> str | None:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # --- rollup (§7.2) --------------------------------------------------------

    def rollup(self, now_ms: int) -> int:
        """Aggregate complete minutes from samples into samples_1m. Idempotent.

        Starts at the last rolled-up minute (recomputing it) and catches up to the
        start of the current minute. Returns the number of minute rows written.
        """
        end = now_ms // MINUTE_MS * MINUTE_MS
        with self._read() as r:
            last = r.execute("SELECT MAX(ts) FROM samples_1m").fetchone()[0]
            if last is None:
                first = r.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
                if first is None:
                    return 0
                start = first // MINUTE_MS * MINUTE_MS
            else:
                start = last
        written = 0
        while start < end:
            chunk_end = min(start + ROLLUP_CHUNK_MS, end)
            written += self._rollup_range(start, chunk_end)
            start = chunk_end
        return written

    def _rollup_range(self, start: int, end: int) -> int:
        with self._read() as r:
            rows = r.execute(
                "SELECT ts, voltage, current, power, soc, temp_t1, temp_t2, temp_mos, "
                "min_cell_voltage, max_cell_voltage, delta_cell_voltage, balance_current, "
                "capacity_remain, cell_voltages FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
                (start, end),
            ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(row["ts"] // MINUTE_MS * MINUTE_MS, []).append(row)
        out = []
        for minute, g in groups.items():

            def col(name: str, g=g) -> list:
                return [x[name] for x in g]

            out.append(
                (
                    minute,
                    len(g),
                    _r(_avg(col("voltage"))),
                    _min(col("voltage")),
                    _max(col("voltage")),
                    _r(_avg(col("current"))),
                    _min(col("current")),
                    _max(col("current")),
                    _r(_avg(col("power")), 2),
                    g[-1]["soc"],
                    _max(col("temp_t1")),
                    _max(col("temp_t2")),
                    _max(col("temp_mos")),
                    _min(col("min_cell_voltage")),
                    _max(col("max_cell_voltage")),
                    _max(col("delta_cell_voltage")),
                    json.dumps(_per_index_avg([json.loads(x) for x in col("cell_voltages")])),
                    _r(_avg(col("balance_current"))),
                    g[-1]["capacity_remain"],
                )
            )
        if out:
            with self._tx() as w:
                w.executemany(
                    "INSERT OR REPLACE INTO samples_1m (ts, n, voltage_avg, voltage_min, voltage_max, "
                    "current_avg, current_min, current_max, power_avg, soc_last, temp_t1_max, temp_t2_max, "
                    "temp_mos_max, cell_min, cell_max, delta_cell_max, cell_voltages_avg, "
                    "balance_current_avg, capacity_remain_last) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    out,
                )
        return len(out)

    # --- retention (§7.4) -----------------------------------------------------

    def apply_retention(self, now_ms: int, raw_retention_days: int) -> dict:
        cutoff = now_ms - raw_retention_days * DAY_MS
        with self._read() as r:
            last_rolled = r.execute("SELECT MAX(ts) FROM samples_1m").fetchone()[0]
        # Never delete raw rows that have not been rolled up yet.
        raw_cutoff = min(cutoff, last_rolled) if last_rolled is not None else None
        events_cutoff = now_ms - EVENTS_RETENTION_DAYS * DAY_MS
        with self._tx() as w:
            raw_deleted = (
                w.execute("DELETE FROM samples WHERE ts < ?", (raw_cutoff,)).rowcount if raw_cutoff is not None else 0
            )
            events_deleted = w.execute("DELETE FROM events WHERE ts < ?", (events_cutoff,)).rowcount
            last_vacuum = self._meta_get(w, "last_vacuum_ms")
            if last_vacuum is None:
                w.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_vacuum_ms', ?)", (str(now_ms),))
        with self._wlock:
            self._w.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            vacuumed = False
            if last_vacuum is not None and now_ms - int(last_vacuum) >= VACUUM_INTERVAL_MS:
                log.info("running VACUUM")
                self._w.execute("VACUUM")
                self._w.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_vacuum_ms', ?)", (str(now_ms),))
                vacuumed = True
        return {"raw_deleted": raw_deleted, "events_deleted": events_deleted, "vacuumed": vacuumed}

    # --- reads ----------------------------------------------------------------

    def latest_settings(self) -> tuple[int, dict] | None:
        with self._read() as r:
            row = r.execute("SELECT ts, data FROM settings_history ORDER BY ts DESC LIMIT 1").fetchone()
        return (row["ts"], json.loads(row["data"])) if row else None

    def device_info(self) -> tuple[int, dict] | None:
        with self._read() as r:
            row = r.execute("SELECT updated_ts, data FROM device_info WHERE id=1").fetchone()
        return (row["updated_ts"], json.loads(row["data"])) if row else None

    def events(self, limit: int = 100, frm: int | None = None, to: int | None = None) -> list[dict]:
        sql = "SELECT id, ts, type, detail FROM events WHERE 1=1"
        args: list = []
        if frm is not None:
            sql += " AND ts >= ?"
            args.append(frm)
        if to is not None:
            sql += " AND ts <= ?"
            args.append(to)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._read() as r:
            return [dict(x) for x in r.execute(sql, args).fetchall()]

    @staticmethod
    def _bucket(frm: int, to: int, max_points: int, sample_interval_s: float) -> tuple[int, bool]:
        span = to - frm
        raw = span <= RAW_SOURCE_MAX_SPAN_MS
        floor_s = math.ceil(sample_interval_s) if raw else 60
        bucket_s = max(floor_s, math.ceil(span / 1000 / max_points))
        # Buckets are aligned to epoch multiples (stable across refreshes); widen until
        # the aligned buckets spanning [frm, to) fit within max_points.
        while (to - 1) // (bucket_s * 1000) - frm // (bucket_s * 1000) + 1 > max_points:
            bucket_s += 1
        return bucket_s, raw

    def history(self, frm: int, to: int, fields: list[str], max_points: int, sample_interval_s: float) -> dict:
        unknown = [f for f in fields if f not in HISTORY_FIELDS]
        if unknown:
            raise QueryError(f"unknown field(s): {', '.join(unknown)}")
        bucket_s, raw = self._bucket(frm, to, max_points, sample_interval_s)
        b = bucket_s * 1000
        selects = []
        for f in fields:
            nd = 1 if f == "soc" else 2 if f == "power" else 3
            if raw:
                selects.append(f"ROUND(AVG({f}), {nd}) AS {f}")
                if f in BAND_FIELDS:
                    selects.append(f"MIN({f}) AS {f}_min, MAX({f}) AS {f}_max")
            else:
                col = _ROLLUP_COLUMN[f]
                selects.append(f"ROUND(SUM({col} * n) / SUM(CASE WHEN {col} IS NULL THEN 0 ELSE n END), {nd}) AS {f}")
                if f in BAND_FIELDS:
                    selects.append(f"MIN({f}_min) AS {f}_min, MAX({f}_max) AS {f}_max")
        table = "samples" if raw else "samples_1m"
        sql = (
            f"SELECT (ts / {b}) * {b} AS bts, {', '.join(selects)} FROM {table} "
            "WHERE ts >= ? AND ts < ? GROUP BY bts ORDER BY bts"
        )
        with self._read() as r:
            rows = r.execute(sql, (frm, to)).fetchall()
        points = []
        for row in rows:
            p = dict(row)
            p["ts"] = p.pop("bts")
            points.append(p)
        return {
            "from": frm,
            "to": to,
            "bucket_s": bucket_s,
            "source": table,
            "fields": fields,
            "points": points,
        }

    def cell_history(self, frm: int, to: int, max_points: int, sample_interval_s: float) -> dict:
        bucket_s, raw = self._bucket(frm, to, max_points, sample_interval_s)
        b = bucket_s * 1000
        if raw:
            sql = "SELECT ts, cell_voltages AS cells, 1 AS n FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts"
        else:
            sql = "SELECT ts, cell_voltages_avg AS cells, n FROM samples_1m WHERE ts >= ? AND ts < ? ORDER BY ts"
        with self._read() as r:
            rows = r.execute(sql, (frm, to)).fetchall()
        buckets: dict[int, tuple[list, list]] = {}
        for row in rows:
            if not row["cells"]:
                continue
            arrays, weights = buckets.setdefault(row["ts"] // b * b, ([], []))
            arrays.append(json.loads(row["cells"]))
            weights.append(row["n"])
        points = [{"ts": ts, "cells": _per_index_avg(a, w)} for ts, (a, w) in buckets.items()]
        cell_count = max((len(p["cells"]) for p in points), default=0)
        return {
            "from": frm,
            "to": to,
            "bucket_s": bucket_s,
            "source": "samples" if raw else "samples_1m",
            "cell_count": cell_count,
            "points": points,
        }

    def export_rows(self, frm: int, to: int) -> Iterator[list]:
        """Yield a CSV header row, then one row per raw sample. Uses its own connection."""
        if to - frm > EXPORT_MAX_SPAN_MS:
            raise QueryError("export range is limited to 31 days")
        c = _connect(self.path)
        try:
            n = (
                c.execute(
                    "SELECT MAX(json_array_length(cell_voltages)) FROM samples WHERE ts >= ? AND ts <= ?", (frm, to)
                ).fetchone()[0]
                or 0
            )
            scalar_cols = [x for x in SAMPLE_COLUMNS if x not in ("cell_voltages", "wire_resist")]
            yield (
                scalar_cols + [f"cell_{i + 1:02d}" for i in range(n)] + [f"wire_resist_{i + 1:02d}" for i in range(n)]
            )
            cur = c.execute(
                f"SELECT {','.join(SAMPLE_COLUMNS)} FROM samples WHERE ts >= ? AND ts <= ? ORDER BY ts", (frm, to)
            )
            while batch := cur.fetchmany(500):
                for row in batch:
                    cells = json.loads(row["cell_voltages"])
                    wires = json.loads(row["wire_resist"])
                    out = [row[k] for k in scalar_cols]
                    out[0] = _iso(row["ts"])
                    yield out + (cells + [None] * n)[:n] + (wires + [None] * n)[:n]
        finally:
            c.close()


def _iso(ts_ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts_ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
