import asyncio

import bleak
import pytest

from ray_vault.bms import ble_client
from ray_vault.bms import protocol as p
from ray_vault.bms.ble_client import BleSource, RecoveryLadder
from ray_vault.bms.fake import FAKE_SETTINGS
from ray_vault.core.system import decode_throttled

# --- recovery ladder ------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_ladder_escalates_with_backoff_then_restarts():
    clock = Clock()
    ladder = RecoveryLadder(reset_after_s=120, max_interval_s=1800, restart_after_resets=3, clock=clock)
    clock.t = 119
    assert ladder.next_action() is None
    clock.t = 120
    assert ladder.next_action() == "reset"
    ladder.did_reset()  # next after +240 s
    clock.t = 359
    assert ladder.next_action() is None
    clock.t = 360
    assert ladder.next_action() == "reset"
    ladder.did_reset()  # +480
    clock.t = 840
    assert ladder.next_action() == "reset"
    ladder.did_reset()  # 3 resets done; +960
    clock.t = 1799
    assert ladder.next_action() is None
    clock.t = 1800
    assert ladder.next_action() == "restart"


def test_ladder_resets_on_good_data_and_caps_interval():
    clock = Clock()
    ladder = RecoveryLadder(reset_after_s=100, max_interval_s=150, restart_after_resets=0, clock=clock)
    clock.t = 100
    ladder.did_reset()
    assert ladder.next_reset_at == 100 + 150  # capped
    for _ in range(10):
        clock.t = ladder.last_good + ladder.next_reset_at
        assert ladder.next_action() == "reset"  # restart disabled with 0
        ladder.did_reset()
    ladder.good()
    assert ladder.resets == 0 and ladder.next_action() is None


# --- session supervision with a fake bleak client --------------------------------

ACK = bytes([0xAA, 0x55, 0x90, 0xEB, 0xC8, 0x01, 0x01] + [0] * 12 + [0x44])


class FakeClient:
    """Acks every command like a real JK BMS. Optionally streams cell frames."""

    stream_cells = False

    def __init__(self, device, disconnected_callback=None, bluez=None):
        self.writes = []

    async def connect(self):
        pass

    async def start_notify(self, _uuid, cb):
        self.cb = cb
        if self.stream_cells:
            self._task = asyncio.create_task(self._stream())

    async def _stream(self):
        import random

        from ray_vault.bms.fake import synth_cells

        frame = p.encode_cells(synth_cells(0, 50, 1, random.Random(0)))
        while True:
            for i in range(0, len(frame), 128):
                self.cb(None, bytearray(frame[i : i + 128]))
            await asyncio.sleep(0.1)

    async def write_gatt_char(self, _uuid, data, response=None):
        self.writes.append(bytes(data))
        self.cb(None, bytearray(ACK))
        if data[4] == p.COMMAND_CELL_INFO:
            self.cb(None, bytearray(p.encode_settings(FAKE_SETTINGS)))

    async def disconnect(self):
        if hasattr(self, "_task"):
            self._task.cancel()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(bleak, "BleakClient", FakeClient)
    monkeypatch.setattr(ble_client, "COMMAND_DELAY_S", 0)
    monkeypatch.setattr(ble_client, "SUPERVISE_TICK_S", 0.05)

    async def no_rssi(*_a):
        return None

    monkeypatch.setattr(ble_client, "connection_rssi", no_rssi)


def run_session(src, seconds):
    events, frames = [], []

    async def go():
        try:
            await asyncio.wait_for(
                src._session(object(), frames.append, lambda _s: None, lambda t, d=None: events.append(t)), seconds
            )
        except TimeoutError:
            events.append("still_running")

    asyncio.run(go())
    return events, frames


def test_acks_and_settings_do_not_keep_a_dead_stream_alive(fast):
    FakeClient.stream_cells = False
    src = BleSource("aa:bb:cc:dd:ee:ff", cell_rerequest_s=0.2, link_timeout_s=0.6)
    events, frames = run_session(src, 3)
    assert "cell_info_rerequest" in events
    assert "ble_timeout" in events  # reconnects instead of re-requesting forever
    assert events[-1] == "ble_disconnected"
    assert src.stats.recovery_stage == "reconnecting"
    assert any(p.frame_type(f) == p.FRAME_TYPE_SETTINGS for f in frames)


def test_healthy_stream_stays_connected(fast):
    FakeClient.stream_cells = True
    src = BleSource("aa:bb:cc:dd:ee:ff", cell_rerequest_s=0.2, link_timeout_s=0.6)
    events, frames = run_session(src, 1.5)
    assert events[-1] == "still_running"
    assert "ble_timeout" not in events and "cell_info_rerequest" not in events
    assert src.stats.recovery_stage == "ok"
    assert any(p.frame_type(f) == p.FRAME_TYPE_CELLS for f in frames)


def test_stalled_source_resets_adapter_then_gives_up(monkeypatch):
    resets = []

    async def fake_reset(adapter):
        resets.append(adapter)
        return "test"

    async def restart_fails():
        raise RuntimeError("Access denied")

    monkeypatch.setattr(ble_client, "power_cycle_adapter", fake_reset)
    monkeypatch.setattr(ble_client, "restart_bluetoothd", restart_fails)
    src = BleSource("aa:bb:cc:dd:ee:ff", adapter_reset_after_s=0, restart_after_resets=2)
    events = []

    async def go():
        for _ in range(3):
            await src._recover_if_stalled(lambda _s: None, lambda t, d=None: events.append(t))
            src.ladder.next_reset_at = 0  # skip the backoff wait

    with pytest.raises(ble_client.LinkRecoveryExhausted):
        asyncio.run(go())
    assert resets == ["hci0", "hci0"]
    assert events == ["adapter_reset", "adapter_reset", "service_restart"]
    assert src.stats.adapter_resets == 2


def test_stalled_source_restarts_bluetoothd_in_process(monkeypatch):
    """With polkit allowing it, the last resort restarts bluetoothd and keeps the process (and inverter) running."""
    calls = []

    async def ok(*_a):
        calls.append("x")
        return "test"

    monkeypatch.setattr(ble_client, "power_cycle_adapter", ok)
    monkeypatch.setattr(ble_client, "restart_bluetoothd", ok)
    src = BleSource(
        "aa:bb:cc:dd:ee:ff", adapter_reset_after_s=0, adapter_reset_max_interval_s=0, restart_after_resets=1
    )
    events = []

    async def go():
        for _ in range(4):
            await src._recover_if_stalled(lambda _s: None, lambda t, d=None: events.append(t))
            src.ladder.next_reset_at = 0

    asyncio.run(go())  # no exception
    assert events == ["adapter_reset", "bluetoothd_restart", "adapter_reset", "bluetoothd_restart"]
    assert src.stats.bluetoothd_restarts == 2


# --- Pi throttle flags -------------------------------------------------------------


def test_decode_throttled():
    d = decode_throttled(0x50005)
    assert d["raw"] == "0x50005"
    assert d["under_voltage_now"] and d["throttled_now"]
    assert d["under_voltage_occurred"] and d["throttled_occurred"]
    assert not d["freq_capped_now"] and not d["soft_temp_limit_occurred"]
    assert not any(v for k, v in decode_throttled(0).items() if k != "raw")


# --- Pi /proc and /sys parsing (sample text from a Pi 3 B) --------------------------

PI_FILES = {
    "/proc/meminfo": "MemTotal:         921876 kB\nMemFree:          301234 kB\nMemAvailable:     614400 kB\n"
    "SwapTotal:        102396 kB\nSwapFree:          92156 kB\n",
    "/proc/net/wireless": "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
    " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
    " wlan0: 0000   52.  -58.  -256        0      0      0      0     12        0\n",
    "/sys/class/thermal/thermal_zone0/temp": "52612",
    "/sys/devices/platform/soc/soc:firmware/get_throttled": "50000",
    "/proc/uptime": "93784.12 360000.00",
    "/proc/device-tree/model": "Raspberry Pi 3 Model B Rev 1.2\x00",
}


def test_pi_readers(monkeypatch, tmp_path):
    from ray_vault.core import system

    stat = iter(["cpu  100 0 100 800 0 0 0 0 0 0", "cpu  150 0 150 850 50 0 0 0 0 0"])
    files = dict(PI_FILES)

    def fake_read(path):
        return next(stat) if path == "/proc/stat" else files.get(path)

    monkeypatch.setattr(system, "_read", fake_read)
    monkeypatch.setattr(system, "_run", lambda *a: None)
    mon = system.SystemMonitor(str(tmp_path / "x.db"))
    d = mon._collect()
    assert d["model"] == "Raspberry Pi 3 Model B Rev 1.2"
    assert d["cpu_temp_c"] == 52.6
    assert d["uptime_s"] == 93784
    assert d["memory"] == {"total_mb": 900, "used_mb": 300, "available_mb": 600, "percent": 33.4, "swap_used_mb": 10}
    assert d["wifi"] == {"interface": "wlan0", "link_quality": 52.0, "signal_dbm": -58.0}
    assert d["power"]["under_voltage_occurred"] and not d["power"]["under_voltage_now"]
    assert d["cpu_percent"] is None  # first sample has nothing to diff against
    assert mon._collect()["cpu_percent"] == 50.0  # 100 busy of 200 jiffies (iowait counts as idle)
