import json
import random
import sqlite3

import pytest

from jkbms_pi.fake_source import synth_cells
from jkbms_pi.state import snapshot
from jkbms_pi.store import DAY_MS, MIGRATIONS, MINUTE_MS, QueryError, Store

T0 = 1_760_000_000_000 // MINUTE_MS * MINUTE_MS  # minute-aligned


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


def add_samples(store, start, count, step_ms=3000, cell_count=16):
    rng = random.Random(0)
    for i in range(count):
        c = synth_cells(start / 1000 + i * step_ms / 1000, 60, 1, rng)
        store.insert_sample(start + i * step_ms, snapshot(c, cell_count, True))


def count(store, table):
    return store._w.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_migrations_and_pragmas(tmp_path):
    path = str(tmp_path / "m.db")
    Store(path).close()
    c = sqlite3.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"samples", "samples_1m", "settings_history", "device_info", "events"} <= tables
    c.close()
    Store(path).close()  # re-open is a no-op migration


def test_sample_insert_trims_cells(store):
    add_samples(store, T0, 1, cell_count=8)
    row = store._w.execute("SELECT cell_voltages, wire_resist FROM samples").fetchone()
    assert len(json.loads(row[0])) == 8 and len(json.loads(row[1])) == 8


def test_settings_only_inserted_when_changed(store):
    assert store.insert_settings_if_changed(T0, {"a": 1, "b": 2}) == []
    assert store.insert_settings_if_changed(T0 + 1, {"a": 1, "b": 2}) is None
    assert store.insert_settings_if_changed(T0 + 2, {"a": 1, "b": 3}) == ["b"]
    assert count(store, "settings_history") == 2
    assert store.latest_settings() == (T0 + 2, {"a": 1, "b": 3})


def test_device_info_upsert(store):
    store.upsert_device_info(T0, {"x": 1})
    store.upsert_device_info(T0 + 5, {"x": 2})
    assert store.device_info() == (T0 + 5, {"x": 2})


def test_rollup_is_idempotent_and_skips_current_minute(store):
    add_samples(store, T0, 60)  # 3 minutes of samples at 3 s
    now = T0 + 2 * MINUTE_MS + 30_000  # mid third minute
    assert store.rollup(now) == 2
    assert count(store, "samples_1m") == 2
    before = store._w.execute("SELECT * FROM samples_1m ORDER BY ts").fetchall()
    store.rollup(now)
    after = store._w.execute("SELECT * FROM samples_1m ORDER BY ts").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]

    row = store._w.execute("SELECT * FROM samples_1m ORDER BY ts LIMIT 1").fetchone()
    assert row["ts"] == T0 and row["n"] == 20
    assert row["voltage_min"] <= row["voltage_avg"] <= row["voltage_max"]
    assert len(json.loads(row["cell_voltages_avg"])) == 16

    # catch up later
    assert store.rollup(T0 + 10 * MINUTE_MS) >= 1
    assert count(store, "samples_1m") == 3


def test_rollup_catches_up_after_long_downtime(store):
    add_samples(store, T0, 5)
    add_samples(store, T0 + 2 * DAY_MS, 5)
    store.rollup(T0 + 3 * DAY_MS)
    assert count(store, "samples_1m") == 2


def test_retention(store):
    now = T0 + 40 * DAY_MS
    add_samples(store, T0, 3)  # 40 days old
    add_samples(store, now - DAY_MS, 3)
    store.add_event(T0, "service_start")
    store.add_event(now - 100 * DAY_MS, "service_start")
    store.add_event(now, "service_start")
    store.rollup(now)
    result = store.apply_retention(now, raw_retention_days=30)
    assert result["raw_deleted"] == 3
    assert count(store, "samples") == 3
    assert count(store, "samples_1m") == 2  # rollups kept forever
    assert count(store, "events") == 2


def test_retention_never_deletes_unrolled_samples(store):
    now = T0 + 40 * DAY_MS
    add_samples(store, T0, 3)
    store.apply_retention(now, raw_retention_days=30)  # no rollup yet
    assert count(store, "samples") == 3


def test_history_raw_downsampling(store):
    add_samples(store, T0, 1200)  # 1 hour
    h = store.history(T0, T0 + 3_600_000, ["voltage", "current", "soc"], 60, 3)
    assert h["source"] == "samples"
    assert h["bucket_s"] == 60
    assert len(h["points"]) == 60
    p = h["points"][0]
    assert set(p) == {"ts", "voltage", "voltage_min", "voltage_max", "current", "current_min", "current_max", "soc"}
    assert p["voltage_min"] <= p["voltage"] <= p["voltage_max"]


def test_history_minimum_bucket_is_sample_interval(store):
    add_samples(store, T0, 20)
    h = store.history(T0, T0 + 60_000, ["voltage"], 5000, 3)
    assert h["bucket_s"] == 3


def test_history_long_range_uses_rollup(store):
    add_samples(store, T0, 2400)  # 2 hours
    store.rollup(T0 + 3 * 3_600_000)
    h = store.history(T0, T0 + 24 * 3_600_000, ["voltage", "power", "balance_current", "capacity_remain"], 600, 3)
    assert h["source"] == "samples_1m"
    assert h["bucket_s"] in (144, 145)  # widened by one if epoch alignment adds a bucket
    assert 0 < len(h["points"]) <= 600
    assert all(p["voltage"] is not None for p in h["points"])


def test_history_omits_empty_buckets(store):
    add_samples(store, T0, 20)
    add_samples(store, T0 + 30 * MINUTE_MS, 20)
    h = store.history(T0, T0 + 3_600_000, ["voltage"], 60, 3)
    assert len(h["points"]) == 2


def test_history_rejects_unknown_field(store):
    with pytest.raises(QueryError):
        store.history(T0, T0 + 1000, ["voltage", "bogus"], 10, 3)


def test_cell_history(store):
    add_samples(store, T0, 200)
    h = store.cell_history(T0, T0 + 600_000, 10, 3)
    assert h["cell_count"] == 16
    assert h["bucket_s"] == 60
    assert len(h["points"]) == 10
    assert all(3.0 < v < 3.6 for v in h["points"][0]["cells"])


def test_events_newest_first(store):
    for i in range(5):
        store.add_event(T0 + i, "ble_connected", str(i))
    ev = store.events(limit=3)
    assert [e["detail"] for e in ev] == ["4", "3", "2"]
    assert [e["detail"] for e in store.events(frm=T0 + 1, to=T0 + 2)] == ["2", "1"]


def test_export_rows(store):
    add_samples(store, T0, 3, cell_count=4)
    rows = list(store.export_rows(T0, T0 + 60_000))
    header = rows[0]
    assert header[0] == "ts"
    assert "cell_01" in header and "cell_04" in header and "cell_05" not in header
    assert rows[1][0].endswith("Z")
    assert len(rows) == 4
    with pytest.raises(QueryError):
        next(store.export_rows(T0, T0 + 32 * DAY_MS))
