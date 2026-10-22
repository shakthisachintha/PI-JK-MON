"""Synthetic BMS for --fake: emits real encoded frames, so the decoder runs end to end."""

from __future__ import annotations

import asyncio
import json
import math
import random
import time

from ..core.clock import now_ms
from . import protocol as p
from .source import OnEvent, OnFrame, OnLinkChange
from .state import LINK_CONNECTED, LINK_CONNECTING, LINK_SCANNING, BleStats

CELLS = 8  # 8S LFP, 24 V nominal, like the real Ray Vault pack
NOMINAL_AH = 280.0
DAY_S = 86_400

FAKE_SETTINGS = {
    "cell_uvp": 2.8,
    "cell_uvpr": 3.0,
    "cell_ovp": 3.65,
    "cell_ovpr": 3.45,
    "balance_trigger_voltage": 0.005,
    "power_off_voltage": 2.6,
    "max_charge_current": 150.0,
    "charge_ocp_delay": 30,
    "charge_ocp_recovery": 60,
    "max_discharge_current": 200.0,
    "discharge_ocp_delay": 300,
    "discharge_ocp_recovery": 60,
    "short_circuit_recovery": 60,
    "max_balance_current": 2.0,
    "charge_otp": 70.0,
    "charge_otpr": 60.0,
    "discharge_otp": 70.0,
    "discharge_otpr": 60.0,
    "charge_utp": 0.0,
    "charge_utpr": 5.0,
    "mos_otp": 100.0,
    "mos_otpr": 80.0,
    "cell_count": CELLS,
    "charge_switch": 1,
    "discharge_switch": 1,
    "balancer_switch": 1,
    "total_battery_capacity": NOMINAL_AH,
    "short_circuit_delay": 1500,
    "balance_starting_voltage": 3.4,
}

FAKE_DEVICE_INFO = {
    "vendor_id": "JK_B2A16S20P",
    "hardware_version": "10.XW",
    "software_version": "10.07",
    "uptime_s": 1_234_567,
    "power_on_count": 12,
    "device_name": "FAKE-BMS",
    "manufacturing_date": "240101",
    "serial_number": "FAKE0000001",
    "user_data": "fake data",
}

# Small fixed per-cell offsets so cells look individual.
_CELL_OFFSETS = [(((i * 7919) % 13) - 6) / 1000 for i in range(CELLS)]
_WIRE_RESIST = [round(0.045 + ((i * 31) % 9) / 1000, 3) for i in range(CELLS)]


def _ocv(soc: float) -> float:
    """Rough LiFePO4 open-circuit cell voltage for a state of charge (0..100)."""
    s = max(0.0, min(100.0, soc)) / 100
    if s < 0.1:
        return 2.9 + 3.2 * s
    if s < 0.9:
        return 3.22 + 0.15 * (s - 0.1)
    return 3.34 + 2.0 * (s - 0.9)


def _current_at(t: float) -> float:
    """Solar-ish daily profile: charge around midday, a base load the rest of the time."""
    hour = (t % DAY_S) / 3600
    solar = max(0.0, math.sin((hour - 6) / 12 * math.pi)) * 60
    load = 12 + 6 * math.sin(t / 900) + (25 if 18 <= hour <= 22 else 0)
    return solar - load


def synth_cells(t: float, soc: float, cycles: int, rng: random.Random) -> p.CellData:
    current = round(_current_at(t) + rng.gauss(0, 0.4), 3)
    ir_drop = current * 0.0006
    base = _ocv(soc) + ir_drop
    cells = [round(base + _CELL_OFFSETS[i] + rng.gauss(0, 0.0008), 3) for i in range(CELLS)]
    voltage = round(sum(cells), 3)
    hour = (t % DAY_S) / 3600
    ambient = 20 + 5 * math.sin((hour - 9) / 24 * 2 * math.pi)
    delta = round(max(cells) - min(cells), 3)
    balancing = delta > 0.008 and current > 0 and soc > 85
    return p.CellData(
        cell_voltages=cells + [0.0] * (p.MAX_CELLS - CELLS),  # unused slots read 0 V, as on a real JK
        avg_cell_voltage=round(voltage / CELLS, 3),
        delta_cell_voltage=delta,
        wire_resist=list(_WIRE_RESIST) + [0.0] * (p.MAX_CELLS - CELLS),
        temp_mos=round(ambient + 4 + abs(current) * 0.05 + rng.gauss(0, 0.1), 1),
        voltage=voltage,
        current=current,
        temp_t1=round(ambient + abs(current) * 0.02 + rng.gauss(0, 0.1), 1),
        temp_t2=round(ambient - 0.3 + abs(current) * 0.02 + rng.gauss(0, 0.1), 1),
        balance_current=round(0.35 if balancing else 0.0, 3),
        balancing_action=1 if balancing else 0,
        soc=int(round(soc)),
        capacity_remain=round(NOMINAL_AH * soc / 100, 3),
        nominal_capacity=NOMINAL_AH,
        cycle_count=cycles,
        cycle_capacity=round(cycles * NOMINAL_AH * 0.97, 3),
        bms_uptime_s=int(t) % 0xFFFFFF,
        charge_on=True,
        discharge_on=True,
        balance_on=True,
    )


class FakeSource:
    """Same interface as BleSource. Emits a cell-data frame every `frame_interval_s`."""

    def __init__(self, frame_interval_s: float = 1.0, seed: int | None = None, stats: BleStats | None = None) -> None:
        self.frame_interval_s = frame_interval_s
        self.rng = random.Random(seed)
        self.stats = stats or BleStats()
        self.soc = 70.0
        self.cycles = 41

    async def run(self, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        on_link_change(LINK_SCANNING)
        await asyncio.sleep(0.5)
        on_link_change(LINK_CONNECTING)
        await asyncio.sleep(0.3)
        on_link_change(LINK_CONNECTED)
        on_event("ble_connected", "fake")
        on_frame(p.encode_device_info(FAKE_DEVICE_INFO))
        on_frame(p.encode_settings(FAKE_SETTINGS))
        last = time.time()
        while True:
            await asyncio.sleep(self.frame_interval_s)
            t = time.time()
            cells = synth_cells(t, self.soc, self.cycles, self.rng)
            self.soc = max(5.0, min(100.0, self.soc + cells.current * (t - last) / 3600 / NOMINAL_AH * 100))
            last = t
            on_frame(p.encode_cells(cells))
            if self.stats.rssi_ts is None or now_ms() - self.stats.rssi_ts > 30_000:
                self.stats.rssi = round(-68 + 6 * math.sin(t / 600) + self.rng.gauss(0, 2))
                self.stats.rssi_ts = now_ms()
                self.stats.rssi_source = "connection"


def seed_rows(start_ms: int, end_ms: int, interval_s: float, seed: int = 1) -> list[tuple]:
    """Synthetic samples rows (store.SAMPLE_COLUMNS order) for --seed-days."""
    from .state import snapshot

    rng = random.Random(seed)
    soc, cycles = 60.0, 30
    rows = []
    step = int(interval_s * 1000)
    for ts in range(start_ms, end_ms, step):
        t = ts / 1000
        c = synth_cells(t, soc, cycles, rng)
        soc = max(5.0, min(100.0, soc + c.current * interval_s / 3600 / NOMINAL_AH * 100))
        s = snapshot(c, CELLS, True)
        rows.append(
            (
                ts, c.voltage, c.current, c.power, c.soc, c.capacity_remain, c.nominal_capacity, c.cycle_count,
                c.cycle_capacity, c.avg_cell_voltage, c.delta_cell_voltage, s.min_cell_voltage, s.max_cell_voltage,
                c.balance_current, c.temp_t1, c.temp_t2, c.temp_mos, int(c.charge_on), int(c.discharge_on),
                int(c.balance_on), c.balancing_action, c.bms_uptime_s, json.dumps(s.cell_voltages),
                json.dumps(s.wire_resist),
            )
        )  # fmt: skip
    return rows
