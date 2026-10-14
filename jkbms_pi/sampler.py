"""Periodic writer: one samples row per interval while data is fresh (§6)."""

from __future__ import annotations

import asyncio
import logging

from .state import CellSnapshot, State, now_ms, snapshot
from .store import Store

log = logging.getLogger(__name__)


def fresh_snapshot(state: State, at_ms: int, stale_s: float) -> CellSnapshot | None:
    """The latest cells as a snapshot, or None if missing or older than stale_s."""
    age = state.cells_age_s(at_ms)
    if state.latest_cells is None or age is None or age > stale_s:
        return None
    return snapshot(state.latest_cells, state.cell_count, state.cell_count_known)


async def run_sampler(state: State, store: Store, interval_s: float, stale_s: float) -> None:
    loop = asyncio.get_running_loop()
    next_t = loop.time()
    while True:
        next_t += interval_s
        delay = next_t - loop.time()
        if delay < -interval_s:  # fell behind (e.g. suspended); don't burst to catch up
            next_t = loop.time()
            delay = 0
        await asyncio.sleep(max(0.0, delay))

        ts = now_ms()
        snap = fresh_snapshot(state, ts, stale_s)
        if snap is None:
            continue  # gaps stay gaps
        try:
            await asyncio.to_thread(store.insert_sample, ts, snap)
        except Exception:
            log.exception("failed to store sample")
