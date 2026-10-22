"""Generic time series on top of the Store: insert, per-minute rollup, downsampled history, CSV export, retention.

Each device describes its tables once in a SeriesSpec; the same code then serves BMS and inverter data, so
history, export and retention behave identically for both. Tables themselves are created by migrations in
store.py (frozen DDL), not from these specs.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .store import DAY_MS, HOUR_MS, MINUTE_MS, QueryError, Store

RAW_SOURCE_MAX_SPAN_MS = 6 * HOUR_MS  # longer ranges are served from the 1-minute rollups
ROLLUP_CHUNK_MS = 6 * HOUR_MS
EXPORT_MAX_SPAN_MS = 31 * DAY_MS


@dataclass(frozen=True)
class Agg:
    """How one raw column rolls up into one column of the 1-minute table."""

    column: str  # raw column
    how: str  # "avg" | "min" | "max" | "last"
    target: str  # column in the rollup table
    digits: int = 3


@dataclass(frozen=True)
class Field:
    """A field /history can return. Backed by `column` (raw) and `rollup` (1-minute table)."""

    name: str
    rollup: str
    digits: int = 3
    band: bool = False  # also return {name}_min / {name}_max (rollup table needs those columns)


@dataclass(frozen=True)
class SeriesSpec:
    raw_table: str
    rollup_table: str
    columns: tuple[str, ...]  # raw columns in insert order, starting with "ts"
    aggs: tuple[Agg, ...]
    fields: tuple[Field, ...]
    default_fields: tuple[str, ...]
    arrays: dict[str, str] = field(default_factory=dict)  # JSON-array raw column -> per-index-avg rollup column
    export_prefix: dict[str, str] = field(default_factory=dict)  # JSON-array column -> CSV column prefix

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise QueryError(f"unknown field: {name}")


def _clean(xs):
    return [x for x in xs if x is not None]


def _aggregate(how: str, xs: list, digits: int):
    xs = _clean(xs)
    if not xs:
        return None
    if how == "avg":
        return round(sum(xs) / len(xs), digits)
    if how == "min":
        return min(xs)
    if how == "max":
        return max(xs)
    if how == "last":
        return xs[-1]
    raise ValueError(how)


def per_index_avg(arrays: list[list], weights: list[int] | None = None) -> list:
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


def bucket_for(frm: int, to: int, max_points: int, sample_interval_s: float) -> tuple[int, bool]:
    """Bucket size (s) for a range, and whether to read raw samples (True) or 1-minute rollups.

    Buckets are aligned to epoch multiples so they stay stable across refreshes, and widened until the
    aligned buckets spanning the half-open range [frm, to) fit within max_points.
    """
    span = to - frm
    raw = span <= RAW_SOURCE_MAX_SPAN_MS
    floor_s = math.ceil(sample_interval_s) if raw else 60
    bucket_s = max(floor_s, math.ceil(span / 1000 / max_points))
    while (to - 1) // (bucket_s * 1000) - frm // (bucket_s * 1000) + 1 > max_points:
        bucket_s += 1
    return bucket_s, raw


def iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TimeSeries:
    def __init__(self, store: Store, spec: SeriesSpec) -> None:
        self.store = store
        self.spec = spec

    # --- writes ---

    def insert(self, rows: list[tuple]) -> None:
        s = self.spec
        sql = f"INSERT OR IGNORE INTO {s.raw_table} ({','.join(s.columns)}) VALUES ({','.join('?' * len(s.columns))})"
        with self.store.tx() as w:
            w.executemany(sql, rows)

    # --- rollup ---

    def rollup(self, now_ms: int) -> int:
        """Aggregate complete minutes into the rollup table. Idempotent; catches up after downtime.

        Starts at the last rolled-up minute (recomputing it) and stops at the start of the current minute.
        Returns the number of minute rows written.
        """
        s = self.spec
        end = now_ms // MINUTE_MS * MINUTE_MS
        with self.store.read() as r:
            last = r.execute(f"SELECT MAX(ts) FROM {s.rollup_table}").fetchone()[0]
            if last is None:
                first = r.execute(f"SELECT MIN(ts) FROM {s.raw_table}").fetchone()[0]
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
        s = self.spec
        cols = sorted({a.column for a in s.aggs} | set(s.arrays))
        with self.store.read() as r:
            rows = r.execute(
                f"SELECT ts, {', '.join(cols)} FROM {s.raw_table} WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)
            ).fetchall()
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(row["ts"] // MINUTE_MS * MINUTE_MS, []).append(row)
        targets = [a.target for a in s.aggs] + list(s.arrays.values())
        out = []
        for minute, g in groups.items():
            vals = [_aggregate(a.how, [x[a.column] for x in g], a.digits) for a in s.aggs]
            for col in s.arrays:
                arrays = [json.loads(x[col]) for x in g if x[col]]
                vals.append(json.dumps(per_index_avg(arrays)))
            out.append((minute, len(g), *vals))
        if out:
            with self.store.tx() as w:
                w.executemany(
                    f"INSERT OR REPLACE INTO {s.rollup_table} (ts, n, {', '.join(targets)}) "
                    f"VALUES ({','.join('?' * (len(targets) + 2))})",
                    out,
                )
        return len(out)

    # --- retention ---

    def delete_raw_before(self, cutoff_ms: int) -> int:
        """Delete raw rows older than cutoff, but never rows that haven't been rolled up yet."""
        s = self.spec
        with self.store.read() as r:
            last_rolled = r.execute(f"SELECT MAX(ts) FROM {s.rollup_table}").fetchone()[0]
        if last_rolled is None:
            return 0
        with self.store.tx() as w:
            return w.execute(f"DELETE FROM {s.raw_table} WHERE ts < ?", (min(cutoff_ms, last_rolled),)).rowcount

    # --- queries ---

    def history(self, frm: int, to: int, fields: list[str], max_points: int, sample_interval_s: float) -> dict:
        s = self.spec
        specs = [s.field(f) for f in fields]
        bucket_s, raw = bucket_for(frm, to, max_points, sample_interval_s)
        b = bucket_s * 1000
        selects = []
        for f in specs:
            if raw:
                selects.append(f"ROUND(AVG({f.name}), {f.digits}) AS {f.name}")
                if f.band:
                    selects.append(f"MIN({f.name}) AS {f.name}_min, MAX({f.name}) AS {f.name}_max")
            else:
                c = f.rollup
                selects.append(
                    f"ROUND(SUM({c} * n) / SUM(CASE WHEN {c} IS NULL THEN 0 ELSE n END), {f.digits}) AS {f.name}"
                )
                if f.band:
                    selects.append(f"MIN({f.name}_min) AS {f.name}_min, MAX({f.name}_max) AS {f.name}_max")
        table = s.raw_table if raw else s.rollup_table
        sql = (
            f"SELECT (ts / {b}) * {b} AS bts, {', '.join(selects)} FROM {table} "
            "WHERE ts >= ? AND ts < ? GROUP BY bts ORDER BY bts"
        )
        with self.store.read() as r:
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
            "source": "samples" if raw else "samples_1m",
            "fields": fields,
            "points": points,
        }

    def array_history(self, column: str, frm: int, to: int, max_points: int, sample_interval_s: float) -> dict:
        """Per-index averages of a JSON-array column per bucket (e.g. BMS cell voltages)."""
        s = self.spec
        if column not in s.arrays:
            raise QueryError(f"{column} is not an array column")
        bucket_s, raw = bucket_for(frm, to, max_points, sample_interval_s)
        b = bucket_s * 1000
        if raw:
            sql = f"SELECT ts, {column} AS arr, 1 AS n FROM {s.raw_table} WHERE ts >= ? AND ts < ? ORDER BY ts"
        else:
            col = s.arrays[column]
            sql = f"SELECT ts, {col} AS arr, n FROM {s.rollup_table} WHERE ts >= ? AND ts < ? ORDER BY ts"
        with self.store.read() as r:
            rows = r.execute(sql, (frm, to)).fetchall()
        buckets: dict[int, tuple[list, list]] = {}
        for row in rows:
            if not row["arr"]:
                continue
            arrays, weights = buckets.setdefault(row["ts"] // b * b, ([], []))
            arrays.append(json.loads(row["arr"]))
            weights.append(row["n"])
        points = [{"ts": ts, "values": per_index_avg(a, w)} for ts, (a, w) in buckets.items()]
        return {
            "from": frm,
            "to": to,
            "bucket_s": bucket_s,
            "source": "samples" if raw else "samples_1m",
            "width": max((len(p["values"]) for p in points), default=0),
            "points": points,
        }

    def export_rows(self, frm: int, to: int) -> Iterator[list]:
        """Yield a CSV header row, then one row per raw sample. Uses its own connection."""
        s = self.spec
        if to - frm > EXPORT_MAX_SPAN_MS:
            raise QueryError("export range is limited to 31 days")
        c = self.store.connect_new()
        try:
            widths = {
                col: (
                    c.execute(
                        f"SELECT MAX(json_array_length({col})) FROM {s.raw_table} WHERE ts >= ? AND ts <= ?", (frm, to)
                    ).fetchone()[0]
                    or 0
                )
                for col in s.export_prefix
            }
            scalar = [x for x in s.columns if x not in s.export_prefix]
            header = list(scalar)
            for col, prefix in s.export_prefix.items():
                header += [f"{prefix}_{i + 1:02d}" for i in range(widths[col])]
            yield header
            cur = c.execute(
                f"SELECT {','.join(s.columns)} FROM {s.raw_table} WHERE ts >= ? AND ts <= ? ORDER BY ts", (frm, to)
            )
            while batch := cur.fetchmany(500):
                for row in batch:
                    out = [row[k] for k in scalar]
                    out[0] = iso(row["ts"])
                    for col, n in widths.items():
                        arr = json.loads(row[col]) if row[col] else []
                        out += (arr + [None] * n)[:n]
                    yield out
        finally:
            c.close()
