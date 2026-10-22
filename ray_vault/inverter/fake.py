"""In-memory fake inverter for --fake and tests: a register map that behaves like the HF2430 over Modbus.

Live registers follow a daily solar/load profile; settings registers hold plausible 24 V LFP values and
writes really change them, so the whole control path (validation -> write -> read-back) runs end to end.
"""

from __future__ import annotations

import asyncio
import math
import random
import time

from .bus import IllegalAddress, NoResponse, PortError

DAY_S = 86_400

# Settings for a 24 V (8S LFP) pack; battery voltages are 12 V-normalised (x0.1 V).
FAKE_SETTINGS = {
    0xE001: 60,  # PV charge current A
    0xE002: 100,  # capacity Ah
    0xE003: 24,  # system voltage
    0xE004: 4,  # battery type (raw)
    0xE005: 146,  # over-voltage 14.6 -> 29.2 V
    0xE006: 144,  # charge limit 14.4 -> 28.8 V
    0xE007: 142,
    0xE008: 142,  # boost 14.2 -> 28.4 V
    0xE009: 136,  # float 13.6 -> 27.2 V
    0xE00A: 132,
    0xE00B: 128,  # over-discharge return 12.8 -> 25.6 V
    0xE00C: 120,
    0xE00D: 116,  # over-discharge 11.6 -> 23.2 V
    0xE00E: 110,  # discharge limit 11.0 -> 22.0 V
    0xE00F: (100 << 8) | 15,  # charge cut-off 100 %, discharge cut-off 15 %
    0xE010: 60,
    0xE011: 120,
    0xE012: 120,
    0xE013: 30,
    0xE01B: 120,  # switch to mains 12.0 -> 24.0 V
    0xE01C: 20,
    0xE204: 2,  # SBU
    0xE205: 100,  # mains charge 10.0 A
    0xE20A: 400,  # max charge 40.0 A
    0xE20F: 0,  # PV preferred
}

INFO = {
    0x000B: 3,
    0x0014: 105,
    0x0015: 100,
    0x0016: 100,
    0x0017: 100,
    0x001A: 1,
    0x001B: 2430,
}


def _ascii_regs(text: str, start: int, count: int, low_only: bool) -> dict[int, int]:
    data = text.encode()
    out = {}
    for i in range(count):
        if low_only:
            out[start + i] = data[i] if i < len(data) else 0
        else:
            hi = data[2 * i] if 2 * i < len(data) else 0
            lo = data[2 * i + 1] if 2 * i + 1 < len(data) else 0
            out[start + i] = (hi << 8) | lo
    return out


def simulate(t: float, soc: float, rng: random.Random, dt_s: float = 2) -> dict:
    """Physical state of the fake system at time t: PV, load, battery voltage/current, next SOC."""
    hour = (t % DAY_S) / 3600
    pv = max(0.0, max(0.0, math.sin((hour - 6) / 12 * math.pi)) * 1800 + rng.gauss(0, 15))
    load = 350 + 150 * math.sin(t / 700) + (600 if 18 <= hour <= 22 else 0) + rng.gauss(0, 10)
    batt_w = pv - load
    bv = 25.6 + 0.6 * (soc / 100) + batt_w / 4000
    bi = batt_w / bv
    soc = max(5.0, min(100.0, soc + bi * dt_s / 3600 / 100 * 100))
    return {"pv": pv, "load": load, "bv": bv, "bi": bi, "soc": soc, "grid_v": 230 + rng.gauss(0, 1.2)}


def seed_rows(start_ms: int, end_ms: int, interval_s: float, seed: int = 1) -> list[tuple]:
    """Synthetic inverter_samples rows for --seed-days."""
    from .series import sample_row

    rng = random.Random(seed)
    soc = 60.0
    rows = []
    for ts in range(start_ms, end_ms, int(interval_s * 1000)):
        sim = simulate(ts / 1000, soc, rng, interval_s)
        soc = sim["soc"]
        pv, load, bv, bi = sim["pv"], sim["load"], sim["bv"], sim["bi"]
        live = {
            "battery_voltage": round(bv, 1),
            "battery_current": round(bi, 1),
            "battery_power": round(bv * bi, 1),
            "soc": round(soc),
            "pv_voltage": 95.0 if pv > 0 else 3.0,
            "pv_current": round(pv / 95, 1),
            "pv_power": round(pv),
            "grid_voltage": round(sim["grid_v"], 1),
            "grid_current": 0.0,
            "grid_frequency": 50.0,
            "grid_charge_current": 0.0,
            "output_voltage": 230.0,
            "output_current": round(load / 230, 1),
            "output_frequency": 50.0,
            "load_current": round(load / 230, 1),
            "load_power": round(load),
            "load_apparent_power": round(load * 1.05),
            "load_percent": round(load / 3000 * 100),
            "temp_dcdc": round(35 + pv / 1000, 1),
            "temp_dcac": round(38 + load / 500, 1),
            "temp_transformer": 33.0,
            "temp_ambient": 28.0,
            "machine_state": 5,
            "fault_bits": "0" * 16,
        }
        rows.append(sample_row(ts, live))
    return rows


class FakeBus:
    port = "fake"

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.regs: dict[int, int] = {**FAKE_SETTINGS, **INFO}
        self.regs.update(_ascii_regs("HF2430S80-H", 0x000C, 8, low_only=False))
        self.regs.update(_ascii_regs("FAKE0000000000000001", 0x0035, 0x14, low_only=True))
        self.illegal: set[int] = {0x0208, 0x0209, 0x020A, 0x020B}  # "reserved" registers some units reject
        self.fail_next = 0  # tests: make the next N calls raise NoResponse
        self.port_missing = False  # tests: simulate an unplugged adapter
        self.writes: list[tuple[int, int]] = []
        self.soc = 70.0

    def _check(self) -> None:
        if self.port_missing:
            raise PortError("fake port missing")
        if self.fail_next:
            self.fail_next -= 1
            raise NoResponse("fake timeout")

    def _live(self) -> dict[int, int]:
        sim = simulate(time.time(), self.soc, self.rng)
        self.soc = sim["soc"]
        pv, load, bv, bi = sim["pv"], sim["load"], sim["bv"], sim["bi"]
        on_mains = self.regs[0xE204] == 1
        grid_v = sim["grid_v"]

        def u(x: float) -> int:
            return int(round(x)) & 0xFFFF

        return {
            0x0100: u(self.soc),
            0x0101: u(bv * 10),
            0x0102: u(-bi * 10),  # inverter reports + while discharging
            0x0107: u((95 if pv > 0 else 3) * 10),
            0x0108: u(pv / 95 * 10),
            0x0109: u(pv),
            0x0200: 0,
            0x0201: 0,
            0x0202: 0,
            0x0203: 0,
            0x0204: 0,
            0x0205: 0,
            0x0206: 0,
            0x0207: 0,
            0x0210: 4 if on_mains else 5,
            0x0212: u(380 * 10),
            0x0213: u(grid_v * 10),
            0x0214: u((load / 230 if on_mains else 0) * 10),
            0x0215: u(50.0 * 100 + self.rng.gauss(0, 2)),
            0x0216: u(230 * 10),
            0x0217: u(load / 230 * 10),
            0x0218: 5000,
            0x0219: u(load / 230 * 10),
            0x021A: 95,
            0x021B: u(load),
            0x021C: u(load * 1.05),
            0x021E: 0,
            0x021F: u(load / 3000 * 100),
            0x0220: u(35 * 10 + pv / 100),
            0x0221: u(38 * 10 + load / 50),
            0x0222: u(33 * 10),
            0x0223: u(28 * 10),
            0x0224: u(pv / bv * 10),
        }

    def _get(self, a: int) -> int:
        if a in self.illegal:
            raise IllegalAddress(f"0x{a:04X}")
        if 0x0100 <= a < 0x0300:
            return self._live().get(a, 0)
        return self.regs.get(a, 0)

    async def read_block(self, start: int, count: int) -> list[int]:
        await asyncio.sleep(0)
        self._check()
        if any(a in self.illegal for a in range(start, start + count)):
            raise IllegalAddress(f"block 0x{start:04X}+{count}")
        live = self._live() if start < 0x0300 else {}
        return [live.get(a, self.regs.get(a, 0)) if start < 0x0300 else self.regs.get(a, 0)
                for a in range(start, start + count)]  # fmt: skip

    async def read_register(self, address: int) -> int:
        await asyncio.sleep(0)
        self._check()
        return self._get(address)

    async def write_register(self, address: int, raw: int) -> None:
        await asyncio.sleep(0)
        self._check()
        if not 0xE000 <= address < 0xE300:
            raise IllegalAddress(f"0x{address:04X} is not writable")
        self.writes.append((address, raw))
        self.regs[address] = raw

    async def reopen(self) -> None:
        pass

    async def close(self) -> None:
        pass
