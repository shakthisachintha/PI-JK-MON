"""Inverter driver: polls the inverter over Modbus, keeps the latest state, supervises the serial link.

Link supervision (time is cheap here, the bus is not):
  - a poll fails             -> retry on the next tick
  - 3 failures in a row      -> close and reopen the serial port, link = no_response
  - port can't be opened     -> link = port_missing, back off 2 → 5 → 10 → 30 s, keep retrying
Every link transition is logged as an event.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import ModuleType

from ..core.clock import now_ms
from ..core.service import BackgroundWriter
from ..core.store import Store
from ..core.timeseries import TimeSeries
from .bus import Bus, BusError, IllegalAddress, NoResponse, PortError
from .registers import Block, SettingSpec
from .series import SPEC, sample_row

log = logging.getLogger(__name__)

SOURCE = "inverter"
REOPEN_AFTER_FAILURES = 3
PORT_BACKOFF_S = (2, 5, 10, 30)
NOMINAL_SYSTEM_VOLTAGES = (12, 24, 36, 48)

LINK_CONNECTING = "connecting"
LINK_CONNECTED = "connected"
LINK_NO_RESPONSE = "no_response"
LINK_PORT_MISSING = "port_missing"


@dataclass
class InverterCounters:
    polls_ok: int = 0
    polls_failed: int = 0
    reopens: int = 0
    writes: int = 0


@dataclass
class InverterState:
    link: str = LINK_CONNECTING
    last_error: str | None = None
    live: dict | None = None
    live_ts: int | None = None
    fault_codes: list[int] = field(default_factory=list)
    regs: dict[int, int] = field(default_factory=dict)  # settings registers, raw
    settings_ts: int | None = None
    device: dict | None = None
    device_ts: int | None = None
    counters: InverterCounters = field(default_factory=InverterCounters)

    def live_age_s(self, at_ms: int | None = None) -> float | None:
        if self.live_ts is None:
            return None
        return max(0.0, ((at_ms or now_ms()) - self.live_ts) / 1000)


def infer_system_voltage(register_value: int | None, battery_voltage: float | None) -> int | None:
    """Nominal battery system voltage: the E003 setting if it's a real value, else inferred from the pack."""
    if register_value in NOMINAL_SYSTEM_VOLTAGES:
        return register_value
    if battery_voltage:
        guess = round(battery_voltage / 13) * 12  # ~13 V per 12 V block covers lead-acid and LFP
        if guess in NOMINAL_SYSTEM_VOLTAGES:
            return guess
    return None


class InverterDriver:
    source = SOURCE

    def __init__(
        self,
        store: Store,
        writer: BackgroundWriter,
        bus: Bus,
        profile: ModuleType,
        poll_interval_s: float = 2,
        settings_poll_s: float = 60,
        stale_s: float = 10,
        state: InverterState | None = None,
    ) -> None:
        self.store = store
        self.writer = writer
        self.bus = bus
        self.profile_module = profile
        self.profile = profile.PROFILE
        self.poll_interval_s = poll_interval_s
        self.settings_poll_s = settings_poll_s
        self.stale_s = stale_s
        self.state = state or InverterState()
        self.series = TimeSeries(store, SPEC)
        self._split_blocks: set[Block] = set()  # blocks the device rejects as a whole
        self._illegal: set[int] = set()  # single registers the device rejects
        self._refresh = asyncio.Event()
        self._io = asyncio.Lock()  # one logical operation (poll / read-modify-write) at a time
        self._failures = 0

    # --- public ---

    def sample(self, ts: int) -> tuple | None:
        age = self.state.live_age_s(ts)
        if self.state.live is None or age is None or age > self.stale_s:
            return None
        return sample_row(ts, self.state.live)

    @property
    def system_voltage(self) -> int | None:
        reg = self.profile.system_voltage_register
        live = self.state.live or {}
        return infer_system_voltage(self.state.regs.get(reg) if reg else None, live.get("battery_voltage"))

    def setting_value(self, spec: SettingSpec, display: bool = True):
        raw = self.state.regs.get(spec.address)
        if raw is None:
            return None
        value = spec.from_raw(raw)
        if not display or not spec.battery_scaled:
            return value
        sv = self.system_voltage
        return spec.to_display(value, sv) if sv else None

    def settings_snapshot(self) -> dict:
        return {s.key: self.setting_value(s) for s in self.profile.settings}

    def request_settings_refresh(self) -> None:
        self._refresh.set()

    async def write_verified(self, spec: SettingSpec, register_value: float) -> int:
        """Write one setting (register units) and read it back. Returns the read-back raw register."""
        async with self._io:
            current = None
            if spec.byte is not None:
                current = await self.bus.read_register(spec.address)
            raw = spec.to_raw(register_value, current)
            await self.bus.write_register(spec.address, raw)
            await asyncio.sleep(0.2)  # some firmwares apply settings a moment after acknowledging
            readback = await self.bus.read_register(spec.address)
            self.state.regs[spec.address] = readback
            self.state.counters.writes += 1
            if readback != raw:
                raise NoResponse(f"read-back mismatch at 0x{spec.address:04X}: wrote {raw}, read {readback}")
            return readback

    # --- main loop ---

    async def run(self) -> None:
        # Fresh primitives bound to the loop this run executes on (also after a supervisor restart).
        refresh_pending = self._refresh.is_set()
        self._refresh = asyncio.Event()
        if refresh_pending:
            self._refresh.set()
        self._io = asyncio.Lock()
        port_failures = 0
        last_settings = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                async with self._io:
                    if self.state.device is None:
                        await self._read_info()
                    await self._poll_live()
                    if self._refresh.is_set() or loop.time() - last_settings >= self.settings_poll_s:
                        self._refresh.clear()
                        await self._poll_settings()
                        last_settings = loop.time()
                self._failures = 0
                port_failures = 0
                self.state.counters.polls_ok += 1
                self._set_link(LINK_CONNECTED)
                await self._sleep_or_refresh(self.poll_interval_s)
            except asyncio.CancelledError:
                raise
            except PortError as e:
                self.state.counters.polls_failed += 1
                self._set_link(LINK_PORT_MISSING, str(e))
                delay = PORT_BACKOFF_S[min(port_failures, len(PORT_BACKOFF_S) - 1)]
                port_failures += 1
                await self.bus.reopen()
                await asyncio.sleep(delay)
            except BusError as e:
                self.state.counters.polls_failed += 1
                self._failures += 1
                log.debug("inverter poll failed (%d): %s", self._failures, e)
                if self._failures >= REOPEN_AFTER_FAILURES:
                    self._set_link(LINK_NO_RESPONSE, str(e))
                    if self._failures == REOPEN_AFTER_FAILURES:
                        log.warning("inverter not responding; reopening %s", getattr(self.bus, "port", "bus"))
                        self.state.counters.reopens += 1
                        await self.bus.reopen()
                await asyncio.sleep(self.poll_interval_s)

    async def _sleep_or_refresh(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._refresh.wait(), seconds)
        except TimeoutError:
            pass

    def _set_link(self, link: str, error: str | None = None) -> None:
        self.state.last_error = error
        if link == self.state.link:
            return
        prev, self.state.link = self.state.link, link
        log.info("inverter link: %s -> %s%s", prev, link, f" ({error})" if error else "")
        if link == LINK_CONNECTED:
            self.writer.event(SOURCE, "inverter_connected", getattr(self.bus, "port", None))
        elif prev == LINK_CONNECTED or prev == LINK_CONNECTING:
            self.writer.event(SOURCE, f"inverter_{link}", error)

    # --- reads ---

    async def _read_blocks(self, blocks: tuple[Block, ...]) -> dict[int, int]:
        regs: dict[int, int] = {}
        for b in blocks:
            if b not in self._split_blocks:
                try:
                    regs.update(zip(b.addresses, await self.bus.read_block(b.start, b.count), strict=False))
                    continue
                except IllegalAddress:
                    log.info("inverter rejects block 0x%04X+%d; reading registers one by one", b.start, b.count)
                    self._split_blocks.add(b)
            for a in b.addresses:
                if a in self._illegal:
                    continue
                try:
                    regs[a] = await self.bus.read_register(a)
                except IllegalAddress:
                    self._illegal.add(a)
        return regs

    async def _read_info(self) -> None:
        regs = await self._read_blocks(self.profile.info_blocks)
        info = self.profile_module.decode_info(regs)
        info["profile"] = self.profile.name
        ts = now_ms()
        self.state.device, self.state.device_ts = info, ts
        self.writer.spawn(self.store.upsert_device_info, ts, info, SOURCE)
        log.info("inverter: %s", info)

    async def _poll_live(self) -> None:
        regs = await self._read_blocks(self.profile.live_blocks)
        live: dict = {}
        for r in self.profile.readings:
            raw = regs.get(r.address)
            live[r.key] = None if raw is None else r.decode(raw)
        v, i = live.get("battery_voltage"), live.get("battery_current")
        live["battery_power"] = round(v * i, 1) if v is not None and i is not None else None
        state = live.get("machine_state")
        live["machine_state_label"] = (
            self.profile.machine_states.get(state, f"State {state}") if state is not None else None
        )
        live["fault_bits"], codes = self.profile_module.decode_faults(regs)
        if codes != self.state.fault_codes:
            if codes:
                self.writer.event(SOURCE, "inverter_fault", ", ".join(map(str, codes)))
            elif self.state.fault_codes:
                self.writer.event(SOURCE, "inverter_fault_cleared", None)
            self.state.fault_codes = codes
        self.state.live, self.state.live_ts = live, now_ms()

    async def _poll_settings(self) -> None:
        self.state.regs.update(await self._read_blocks(self.profile.settings_blocks))
        self.state.settings_ts = ts = now_ms()
        self.writer.spawn(self._store_settings, ts, self.settings_snapshot())

    def _store_settings(self, ts: int, data: dict) -> None:
        changed = self.store.insert_settings_if_changed(ts, data, SOURCE)
        if changed:  # [] = first row ever; None = unchanged
            self.store.add_event(ts, "settings_changed", ", ".join(changed[:20]), SOURCE)
