"""SQLite storage: connections, migrations, events, settings history, device info, retention.

Time-series tables (samples per device) are handled by core/timeseries.py on top of this class.
All methods are synchronous; async callers wrap them in asyncio.to_thread().
One writer connection, one reader connection, each guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
EVENTS_RETENTION_DAYS = 90
VACUUM_INTERVAL_MS = 30 * DAY_MS


class QueryError(ValueError):
    """Bad query parameters; maps to HTTP 400."""


# --- Migrations (frozen: never edit an existing migration, add a new one) -------


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


def _migration_2(c: sqlite3.Connection) -> None:
    """Multi-device: source columns on events/settings/device info, and the inverter time series."""
    _exec_script(
        c,
        """
        CREATE TABLE settings_history_v2 (
          source TEXT NOT NULL,
          ts     INTEGER NOT NULL,
          data   TEXT NOT NULL,
          PRIMARY KEY (source, ts)
        );
        INSERT INTO settings_history_v2 (source, ts, data) SELECT 'bms', ts, data FROM settings_history;
        DROP TABLE settings_history;
        ALTER TABLE settings_history_v2 RENAME TO settings_history;

        CREATE TABLE device_info_v2 (
          source     TEXT PRIMARY KEY,
          updated_ts INTEGER NOT NULL,
          data       TEXT NOT NULL
        );
        INSERT INTO device_info_v2 (source, updated_ts, data) SELECT 'bms', updated_ts, data FROM device_info;
        DROP TABLE device_info;
        ALTER TABLE device_info_v2 RENAME TO device_info;

        ALTER TABLE events ADD COLUMN source TEXT NOT NULL DEFAULT 'bms';
        CREATE INDEX events_source_ts ON events(source, ts);

        CREATE TABLE inverter_samples (
          ts                  INTEGER PRIMARY KEY,
          battery_voltage     REAL,
          battery_current     REAL,   -- A, + charging / - discharging
          battery_power       REAL,   -- W, derived
          soc                 INTEGER,
          pv_voltage          REAL,
          pv_current          REAL,
          pv_power            REAL,
          grid_voltage        REAL,
          grid_current        REAL,
          grid_frequency      REAL,
          grid_charge_current REAL,
          output_voltage      REAL,
          output_current      REAL,
          output_frequency    REAL,
          load_current        REAL,
          load_power          REAL,
          load_apparent_power REAL,
          load_percent        REAL,
          temp_dcdc           REAL,
          temp_dcac           REAL,
          temp_transformer    REAL,
          temp_ambient        REAL,
          machine_state       INTEGER,
          fault_bits          TEXT     -- hex of the 4 fault-bit registers
        );

        CREATE TABLE inverter_samples_1m (
          ts INTEGER PRIMARY KEY,
          n  INTEGER NOT NULL,
          battery_voltage_avg REAL, battery_voltage_min REAL, battery_voltage_max REAL,
          battery_current_avg REAL,
          battery_power_avg   REAL,
          soc_last            INTEGER,
          pv_voltage_avg      REAL,
          pv_power_avg        REAL, pv_power_min REAL, pv_power_max REAL,
          grid_voltage_avg    REAL,
          grid_frequency_avg  REAL,
          grid_charge_current_avg REAL,
          output_voltage_avg  REAL,
          load_power_avg      REAL, load_power_min REAL, load_power_max REAL,
          load_percent_avg    REAL,
          temp_dcdc_max       REAL,
          temp_dcac_max       REAL,
          temp_transformer_max REAL,
          temp_ambient_max    REAL
        );
        """,
    )


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migration_1, _migration_2]


def connect(path: str) -> sqlite3.Connection:
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


# --- Store --------------------------------------------------------------------


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._w = connect(path)
        self._wlock = threading.Lock()
        migrate(self._w)
        self._r = connect(path)
        self._r.execute("PRAGMA query_only=ON")
        self._rlock = threading.Lock()
        self.write_ok = True

    def close(self) -> None:
        with self._wlock, self._rlock:
            self._r.close()
            self._w.close()

    def connect_new(self) -> sqlite3.Connection:
        """A separate connection for long reads (e.g. CSV export) that shouldn't hold the shared reader."""
        return connect(self.path)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
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
    def read(self) -> Iterator[sqlite3.Connection]:
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

    # --- events ---------------------------------------------------------------

    def add_event(self, ts: int, type_: str, detail: str | None = None, source: str = "bms") -> None:
        with self.tx() as w:
            w.execute("INSERT INTO events (ts, source, type, detail) VALUES (?, ?, ?, ?)", (ts, source, type_, detail))

    def events(
        self, limit: int = 100, frm: int | None = None, to: int | None = None, source: str | None = None
    ) -> list[dict]:
        sql = "SELECT id, ts, source, type, detail FROM events WHERE 1=1"
        args: list = []
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        if frm is not None:
            sql += " AND ts >= ?"
            args.append(frm)
        if to is not None:
            sql += " AND ts <= ?"
            args.append(to)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        with self.read() as r:
            return [dict(x) for x in r.execute(sql, args).fetchall()]

    # --- settings & device info -------------------------------------------------

    def insert_settings_if_changed(self, ts: int, data: dict, source: str = "bms") -> list[str] | None:
        """Insert settings when they differ from the last stored row for this source.

        Returns the changed keys ([] for the very first row), or None if unchanged.
        """
        with self.tx() as w:
            prev = w.execute(
                "SELECT data FROM settings_history WHERE source = ? ORDER BY ts DESC LIMIT 1", (source,)
            ).fetchone()
            prev_data = json.loads(prev["data"]) if prev else None
            if prev_data == data:
                return None
            if prev_data is None:
                changed: list[str] = []
            else:
                changed = sorted(k for k in set(data) | set(prev_data) if prev_data.get(k) != data.get(k))
            w.execute(
                "INSERT OR REPLACE INTO settings_history (source, ts, data) VALUES (?, ?, ?)",
                (source, ts, json.dumps(data)),
            )
            return changed

    def latest_settings(self, source: str = "bms") -> tuple[int, dict] | None:
        with self.read() as r:
            row = r.execute(
                "SELECT ts, data FROM settings_history WHERE source = ? ORDER BY ts DESC LIMIT 1", (source,)
            ).fetchone()
        return (row["ts"], json.loads(row["data"])) if row else None

    def upsert_device_info(self, ts: int, data: dict, source: str = "bms") -> None:
        with self.tx() as w:
            w.execute(
                "INSERT INTO device_info (source, updated_ts, data) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET updated_ts=excluded.updated_ts, data=excluded.data",
                (source, ts, json.dumps(data)),
            )

    def device_info(self, source: str = "bms") -> tuple[int, dict] | None:
        with self.read() as r:
            row = r.execute("SELECT updated_ts, data FROM device_info WHERE source = ?", (source,)).fetchone()
        return (row["updated_ts"], json.loads(row["data"])) if row else None

    # --- maintenance --------------------------------------------------------------

    def _meta_get(self, c: sqlite3.Connection, key: str) -> str | None:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def apply_retention(self, now_ms: int) -> dict:
        """Delete old events, checkpoint the WAL, VACUUM at most monthly. Series retention is per TimeSeries."""
        events_cutoff = now_ms - EVENTS_RETENTION_DAYS * DAY_MS
        with self.tx() as w:
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
        return {"events_deleted": events_deleted, "vacuumed": vacuumed}
