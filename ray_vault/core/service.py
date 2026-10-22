"""Runs the device drivers side by side and the jobs they share (sampling, rollup, retention, watchdog).

Drivers are isolated: each runs in its own supervised task, and an unexpected crash restarts only that driver
(with backoff) instead of taking down the process, the API, or the other device.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from .clock import now_ms
from .config import Config
from .store import DAY_MS, Store
from .timeseries import TimeSeries

log = logging.getLogger(__name__)

ROLLUP_INTERVAL_S = 60
RETENTION_INTERVAL_S = DAY_MS / 1000
RESTART_BACKOFF_S = (5, 15, 60, 300)


class FatalDriverError(Exception):
    """A driver needs the whole process restarted (systemd does it). Not caught by the supervisor."""

    exit_code = 1


class Device(Protocol):
    source: str  # "bms" | "inverter"
    series: TimeSeries

    async def run(self) -> None: ...

    def sample(self, ts: int) -> tuple | None:
        """One row for the series if the device has fresh data, else None (gaps stay gaps)."""
        ...


class BackgroundWriter:
    """Fire-and-forget DB writes from event-loop callbacks, run on threads, awaited at shutdown."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, fn: Callable, *args) -> None:
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(fn, *args))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def event(self, source: str, type_: str, detail: str | None = None, ts: int | None = None) -> None:
        self.spawn(self.store.add_event, ts or now_ms(), type_, detail, source)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background DB write failed", exc_info=task.exception())

    async def flush(self, timeout: float = 5) -> None:
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)


class Service:
    def __init__(self, cfg: Config, store: Store, writer: BackgroundWriter, devices: list[Device]) -> None:
        self.cfg = cfg
        self.store = store
        self.writer = writer
        self.devices = {d.source: d for d in devices}
        self.restarts: dict[str, int] = dict.fromkeys(self.devices, 0)

    async def supervise(self, device: Device) -> None:
        attempt = 0
        while True:
            try:
                await device.run()
                log.warning("%s driver returned; restarting", device.source)
            except asyncio.CancelledError:
                raise
            except FatalDriverError:
                raise
            except Exception as e:
                log.exception("%s driver crashed", device.source)
                self.writer.event("system", "driver_crashed", f"{device.source}: {type(e).__name__}: {e}")
            self.restarts[device.source] += 1
            delay = RESTART_BACKOFF_S[min(attempt, len(RESTART_BACKOFF_S) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def sampler(self) -> None:
        """One row per device per interval, all stamped with the same ts so devices line up in queries."""
        interval = self.cfg.storage.sample_interval_s
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while True:
            next_t += interval
            delay = next_t - loop.time()
            if delay < -interval:  # fell behind (e.g. suspended); don't burst to catch up
                next_t = loop.time()
                delay = 0
            await asyncio.sleep(max(0.0, delay))
            ts = now_ms()
            for d in self.devices.values():
                try:
                    row = d.sample(ts)
                    if row is not None:
                        await asyncio.to_thread(d.series.insert, [row])
                except Exception:
                    log.exception("failed to store %s sample", d.source)

    async def maintenance(self) -> None:
        """Rollup every minute; retention at startup and then daily."""
        loop = asyncio.get_running_loop()
        last_retention = None
        while True:
            try:
                for d in self.devices.values():
                    n = await asyncio.to_thread(d.series.rollup, now_ms())
                    if n:
                        log.debug("%s: rolled up %d minute(s)", d.source, n)
                if last_retention is None or loop.time() - last_retention >= RETENTION_INTERVAL_S:
                    cutoff = now_ms() - self.cfg.storage.raw_retention_days * DAY_MS
                    deleted = {
                        d.source: await asyncio.to_thread(d.series.delete_raw_before, cutoff)
                        for d in self.devices.values()
                    }
                    result = await asyncio.to_thread(self.store.apply_retention, now_ms())
                    log.info("retention: raw rows deleted %s, %s", deleted, result)
                    last_retention = loop.time()
            except Exception:
                log.exception("maintenance failed")
            await asyncio.sleep(ROLLUP_INTERVAL_S)

    async def run(self) -> None:
        await asyncio.to_thread(self.store.add_event, now_ms(), "service_start", ", ".join(self.devices), "system")
        try:
            await asyncio.gather(
                *(self.supervise(d) for d in self.devices.values()),
                self.sampler(),
                self.maintenance(),
            )
        finally:
            await self.writer.flush()
