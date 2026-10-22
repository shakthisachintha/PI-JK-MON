import csv
import io
import random

import ray_vault
from ray_vault.bms import protocol as p
from ray_vault.bms.fake import FAKE_DEVICE_INFO, FAKE_SETTINGS, seed_rows, synth_cells
from ray_vault.bms.state import LINK_CONNECTED
from ray_vault.core.clock import now_ms
from tests.conftest import feed_bms


def test_current_before_any_data(env):
    client = env.client
    r = client.get("/api/current")
    assert r.status_code == 200
    body = r.json()
    assert body["ts"] is None and body["link"] == "scanning" and body["stale"] is True
    assert body["voltage"] is None and body["cell_voltages"] is None


def test_current_with_fake_frames(env):
    state, client = env.bms.state, env.client
    cells = synth_cells(1_760_000_000, 70, 41, random.Random(1))
    state.link_status = LINK_CONNECTED
    feed_bms(env, p.encode_settings({**FAKE_SETTINGS, "cell_count": 8}), p.encode_cells(cells))
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
    store, state = env.store, env.bms.state
    bad = bytearray(p.encode_settings(FAKE_SETTINGS))
    bad[299] ^= 0xFF
    feed_bms(env, bytes(bad))
    assert state.latest_settings is None
    assert state.counters.crc_errors == 1
    assert store.events()[0]["type"] == "crc_error"


def test_settings_and_device(env):
    store, client = env.store, env.client
    assert client.get("/api/settings").json() == {"ts": None, "settings": None}
    feed_bms(env, p.encode_settings(FAKE_SETTINGS), p.encode_device_info(FAKE_DEVICE_INFO))
    assert client.get("/api/settings").json()["settings"]["cell_count"] == 8
    dev = client.get("/api/device").json()["device"]
    assert dev["device_name"] == "FAKE-BMS"
    assert not any("passcode" in k for k in dev)
    assert [e["type"] for e in store.events()] == ["settings_changed"]


def test_health(env):
    client = env.client
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"  # no frames yet
    assert body["db_ok"] is True
    assert set(body["devices"]) == {"bms", "inverter"}
    assert body["version"] == ray_vault.__version__


def test_history_and_params(env):
    client = env.client
    now = now_ms()
    env.bms.series.insert(seed_rows(now - 3_600_000, now, 3))
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
    client = env.client
    now = now_ms()
    env.bms.series.insert(seed_rows(now - 600_000, now, 3))
    body = client.get("/api/history/cells", params={"from": now - 600_000, "to": now}).json()
    assert body["cell_count"] == 8 and body["points"]


def test_events_endpoint(env):
    store, client = env.store, env.client
    store.add_event(1000, "service_start", None, "system")
    store.add_event(2000, "ble_connected", "aa", "bms")
    store.add_event(3000, "inverter_connected", None, "inverter")
    body = client.get("/api/events", params={"limit": 1}).json()
    assert body["events"][0]["type"] == "inverter_connected"
    bms_only = client.get("/api/events", params={"source": "bms"}).json()["events"]
    assert [e["type"] for e in bms_only] == ["ble_connected"]
    assert client.get("/api/events", params={"source": "nope"}).status_code == 400


def test_export_csv(env):
    client = env.client
    now = now_ms()
    env.bms.series.insert(seed_rows(now - 60_000, now, 3))
    r = client.get("/api/export.csv", params={"from": now - 120_000, "to": now})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "ts" and "cell_08" in rows[0] and "cell_09" not in rows[0]
    assert len(rows) == 21
    too_long = client.get("/api/export.csv", params={"from": 0, "to": 40 * 86_400_000})
    assert too_long.status_code == 400


def test_cors_open(env):
    client = env.client
    r = client.get("/api/health", headers={"Origin": "http://example.lan"})
    assert r.headers["access-control-allow-origin"] == "*"


def test_system_endpoint(env):
    state, client = env.bms.state, env.client
    state.ble.rssi = -72
    state.ble.rssi_ts = now_ms()
    body = client.get("/api/system").json()
    assert body["ble"]["rssi"] == -72 and body["ble"]["rssi_quality"] == "fair"
    assert body["ble"]["recovery_stage"] == "ok"
    host = body["host"]
    assert {"hostname", "cpu_temp_c", "memory", "disk", "power", "wifi", "load"} <= set(host)
    assert host["disk"]["total_gb"] > 0
    assert client.get("/api/current").json()["rssi"] == -72
    assert body["inverter"]["link"] == "connecting"


def test_new_bms_paths_match_aliases(env):
    client = env.client
    for path in ("current", "settings", "device"):
        assert client.get(f"/api/bms/{path}").json() == client.get(f"/api/{path}").json()
    assert client.get("/api/bms/link").json()["recovery_stage"] == "ok"
