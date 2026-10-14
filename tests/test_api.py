import asyncio
import csv
import io

import pytest
from fastapi.testclient import TestClient

from jkbms_pi import protocol as p
from jkbms_pi.api import create_app
from jkbms_pi.config import Config
from jkbms_pi.fake_source import FAKE_DEVICE_INFO, FAKE_SETTINGS, FakeSource, seed_rows, synth_cells
from jkbms_pi.service import Service
from jkbms_pi.state import LINK_CONNECTED, State, now_ms
from jkbms_pi.store import Store


@pytest.fixture
def env(tmp_path):
    cfg = Config(db_path=str(tmp_path / "api.db"))
    store = Store(cfg.db_path)
    state = State()
    service = Service(cfg, store, state, FakeSource())
    client = TestClient(create_app(cfg, state, store, static_dir=None))
    yield cfg, store, state, service, client
    store.close()


def feed(service, *frames):
    async def go():
        for f in frames:
            service.on_frame(f)
        await asyncio.gather(*service._bg)

    asyncio.run(go())


def test_current_before_any_data(env):
    *_, client = env
    r = client.get("/api/current")
    assert r.status_code == 200
    body = r.json()
    assert body["ts"] is None and body["link"] == "scanning" and body["stale"] is True
    assert body["voltage"] is None and body["cell_voltages"] is None


def test_current_with_fake_frames(env):
    _, store, state, service, client = env
    import random

    cells = synth_cells(1_760_000_000, 70, 41, random.Random(1))
    state.link_status = LINK_CONNECTED
    feed(service, p.encode_settings({**FAKE_SETTINGS, "cell_count": 8}), p.encode_cells(cells))
    body = client.get("/api/current").json()
    assert body["link"] == "connected" and body["stale"] is False
    assert body["voltage"] == cells.voltage
    assert body["soc"] == cells.soc
    assert len(body["cell_voltages"]) == 8 and len(body["wire_resist"]) == 8
    active = cells.cell_voltages[:8]
    assert body["min_cell_voltage"] == min(active)
    assert body["cell_voltages"][body["min_cell_index"] - 1] == min(active)
    assert body["cell_voltages"][body["max_cell_index"] - 1] == max(active)


def test_crc_failure_is_dropped_and_counted(env):
    _, store, state, service, client = env
    bad = bytearray(p.encode_settings(FAKE_SETTINGS))
    bad[299] ^= 0xFF
    feed(service, bytes(bad))
    assert state.latest_settings is None
    assert state.counters.crc_errors == 1
    assert store.events()[0]["type"] == "crc_error"


def test_settings_and_device(env):
    _, store, state, service, client = env
    assert client.get("/api/settings").json() == {"ts": None, "settings": None}
    feed(service, p.encode_settings(FAKE_SETTINGS), p.encode_device_info(FAKE_DEVICE_INFO))
    assert client.get("/api/settings").json()["settings"]["cell_count"] == 16
    dev = client.get("/api/device").json()["device"]
    assert dev["device_name"] == "FAKE-BMS"
    assert not any("passcode" in k for k in dev)
    assert [e["type"] for e in store.events()] == ["settings_changed"]


def test_health(env):
    *_, client = env
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"  # no frames yet
    assert body["db_ok"] is True
    assert body["version"] == "1.0.0"


def test_history_and_params(env):
    _, store, *_, client = env
    now = now_ms()
    store.insert_samples_bulk(seed_rows(now - 3_600_000, now, 3))
    r = client.get("/api/history", params={"from": now - 3_600_000, "to": now, "max_points": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "samples" and 0 < len(body["points"]) <= 100
    assert {"voltage", "voltage_min", "voltage_max", "current", "power", "soc"} <= set(body["points"][0])

    iso = client.get("/api/history", params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00"})
    assert iso.status_code == 200 and iso.json()["from"] == 1767225600000

    assert client.get("/api/history", params={"from": now, "to": now - 1}).status_code == 400
    bad = client.get("/api/history", params={"fields": "voltage,nope"})
    assert bad.status_code == 400 and "nope" in bad.json()["error"]
    assert client.get("/api/history", params={"max_points": 99999}).status_code == 400
    assert client.get("/api/history", params={"from": "yesterday"}).status_code == 400


def test_history_cells(env):
    _, store, *_, client = env
    now = now_ms()
    store.insert_samples_bulk(seed_rows(now - 600_000, now, 3))
    body = client.get("/api/history/cells", params={"from": now - 600_000, "to": now}).json()
    assert body["cell_count"] == 16 and body["points"]


def test_events_endpoint(env):
    _, store, *_, client = env
    store.add_event(1000, "service_start")
    store.add_event(2000, "ble_connected", "aa")
    body = client.get("/api/events", params={"limit": 1}).json()
    assert body["events"][0]["type"] == "ble_connected"


def test_export_csv(env):
    _, store, *_, client = env
    now = now_ms()
    store.insert_samples_bulk(seed_rows(now - 60_000, now, 3))
    r = client.get("/api/export.csv", params={"from": now - 120_000, "to": now})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "ts" and "cell_16" in rows[0]
    assert len(rows) == 21
    too_long = client.get("/api/export.csv", params={"from": 0, "to": 40 * 86_400_000})
    assert too_long.status_code == 400


def test_cors_open(env):
    *_, client = env
    r = client.get("/api/health", headers={"Origin": "http://example.lan"})
    assert r.headers["access-control-allow-origin"] == "*"


def test_system_endpoint(env):
    _, _, state, _, client = env
    state.ble.rssi = -72
    state.ble.rssi_ts = now_ms()
    body = client.get("/api/system").json()
    assert body["ble"]["rssi"] == -72 and body["ble"]["rssi_quality"] == "fair"
    assert body["ble"]["recovery_stage"] == "ok"
    host = body["host"]
    assert {"hostname", "cpu_temp_c", "memory", "disk", "power", "wifi", "load"} <= set(host)
    assert host["disk"]["total_gb"] > 0
    assert client.get("/api/current").json()["rssi"] == -72
