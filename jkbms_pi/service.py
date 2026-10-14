"""Wires a source to the decoder, in-memory state and store; runs background tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from . import protocol as p
from .config import Config
from .sampler import run_sampler
from .source import Source
from .state import State, now_ms
from .store import DAY_MS, Store

log = logging.getLogger(__name__)

ROLLUP_INTERVAL_S = 60
RETENTION_INTERVAL_S = DAY_MS / 1000
CRC_EVENT_MIN_INTERVAL_MS = 60_000


class Service:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        state: State,
        source: Source,
        dump_dir: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.state = state
        self.source = source
        self.dump_dir = dump_dir
        self._bg: set[asyncio.Task] = set()
        self._connected_once = False
        self._crc_pending = 0
        self._crc_last_event_ms = 0

    # --- callbacks from the source (run in the event loop; must not block) ---

    def on_frame(self, frame: bytes) -> None:
        ts = now_ms()
        if self.dump_dir is not None:
            self._dump(ts, frame)

        if not p.checksum_ok(frame):
            self.state.counters.crc_errors += 1
            log.warning("checksum mismatch on frame type 0x%02x; dropped", p.frame_type(frame))
            self._crc_pending += 1
            if ts - self._crc_last_event_ms >= CRC_EVENT_MIN_INTERVAL_MS:
                self._record_event(ts, "crc_error", f"{self._crc_pending} frame(s) dropped")
                self._crc_pending = 0
                self._crc_last_event_ms = ts
            return

        decoded = p.decode_frame(frame)
        self.state.counters.frames_ok += 1
        if decoded.cells is not None:
            self.state.latest_cells = decoded.cells
            self.state.latest_cells_ts = ts
        elif decoded.settings is not None:
            self.state.latest_settings = decoded.settings
            self.state.latest_settings_ts = ts
            self._spawn(self._store_settings, ts, decoded.settings)
        elif decoded.device_info is not None:
            self.state.device_info = decoded.device_info
            self.state.device_info_ts = ts
            self._spawn(self.store.upsert_device_info, ts, decoded.device_info)
        else:
            self.state.counters.unknown_frames += 1
            log.debug("unknown frame type 0x%02x: %s", decoded.type, frame.hex(" "))

    def on_link_change(self, status: str) -> None:
        if status != self.state.link_status:
            log.info("link: %s", status)
        self.state.link_status = status

    def on_event(self, type_: str, detail: str | None = None) -> None:
        if type_ == "ble_connected":
            if self._connected_once:
                self.state.counters.reconnects += 1
            self._connected_once = True
        self._record_event(now_ms(), type_, detail)

    # --- internals ---

    def _record_event(self, ts: int, type_: str, detail: str | None) -> None:
        self._spawn(self.store.add_event, ts, type_, detail)

    def _store_settings(self, ts: int, data: dict) -> None:
        changed = self.store.insert_settings_if_changed(ts, data)
        if changed is not None:
            detail = ", ".join(changed[:20]) + ("…" if len(changed) > 20 else "") if changed else "initial settings"
            self.store.add_event(ts, "settings_changed", detail)

    def _spawn(self, fn: Callable, *args) -> None:
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(fn, *args))
        self._bg.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background DB write failed", exc_info=task.exception())

    def _dump(self, ts: int, frame: bytes) -> None:
        log.info("frame type 0x%02x: %s", p.frame_type(frame), frame.hex(" "))
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / f"{ts}_{p.frame_type(frame):02x}.hex").write_text(frame.hex(" ") + "\n")
        except OSError as e:
            log.warning("could not save frame dump: %s", e)

    # --- background loops ---

    async def maintenance(self) -> None:
        """Rollup every minute; retention at startup and then daily (§7.2, §7.4)."""
        loop = asyncio.get_running_loop()
        last_retention = None
        while True:
            try:
                n = await asyncio.to_thread(self.store.rollup, now_ms())
                if n:
                    log.debug("rolled up %d minute(s)", n)
                if last_retention is None or loop.time() - last_retention >= RETENTION_INTERVAL_S:
                    result = await asyncio.to_thread(self.store.apply_retention, now_ms(), self.cfg.raw_retention_days)
                    log.info("retention: %s", result)
                    last_retention = loop.time()
            except Exception:
                log.exception("maintenance failed")
            await asyncio.sleep(ROLLUP_INTERVAL_S)

    async def run(self) -> None:
        await asyncio.to_thread(self.store.add_event, now_ms(), "service_start", None)
        try:
            await asyncio.gather(
                self.source.run(self.on_frame, self.on_link_change, self.on_event),
                run_sampler(self.state, self.store, self.cfg.sample_interval_s, self.cfg.stale_s),
                self.maintenance(),
            )
        finally:
            # Let pending event/settings writes land (e.g. the service_restart event) before exiting.
            if self._bg:
                await asyncio.wait(set(self._bg), timeout=5)
