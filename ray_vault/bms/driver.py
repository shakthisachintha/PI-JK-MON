"""BMS driver: wires a BLE (or fake) source to the decoder, in-memory state and store."""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.clock import now_ms
from ..core.service import BackgroundWriter
from ..core.store import Store
from ..core.timeseries import TimeSeries
from . import protocol as p
from .series import SPEC, sample_row
from .source import Source
from .state import CellSnapshot, State, snapshot

log = logging.getLogger(__name__)

CRC_EVENT_MIN_INTERVAL_MS = 60_000
SOURCE = "bms"


def fresh_snapshot(state: State, at_ms: int, stale_s: float) -> CellSnapshot | None:
    """The latest cells as a snapshot, or None if missing or older than stale_s."""
    age = state.cells_age_s(at_ms)
    if state.latest_cells is None or age is None or age > stale_s:
        return None
    return snapshot(state.latest_cells, state.cell_count, state.cell_count_known)


class BmsDriver:
    source = SOURCE

    def __init__(
        self,
        store: Store,
        writer: BackgroundWriter,
        state: State,
        link: Source,
        stale_s: float,
        dump_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.writer = writer
        self.state = state
        self.link = link
        self.stale_s = stale_s
        self.dump_dir = dump_dir
        self.series = TimeSeries(store, SPEC)
        self._connected_once = False
        self._crc_pending = 0
        self._crc_last_event_ms = 0

    async def run(self) -> None:
        await self.link.run(self.on_frame, self.on_link_change, self.on_event)

    def sample(self, ts: int) -> tuple | None:
        snap = fresh_snapshot(self.state, ts, self.stale_s)
        return None if snap is None else sample_row(ts, snap)

    # --- callbacks from the link (run in the event loop; must not block) ---

    def on_frame(self, frame: bytes) -> None:
        ts = now_ms()
        if self.dump_dir is not None:
            self._dump(ts, frame)

        if not p.checksum_ok(frame):
            self.state.counters.crc_errors += 1
            log.warning("checksum mismatch on frame type 0x%02x; dropped", p.frame_type(frame))
            self._crc_pending += 1
            if ts - self._crc_last_event_ms >= CRC_EVENT_MIN_INTERVAL_MS:
                self.writer.event(SOURCE, "crc_error", f"{self._crc_pending} frame(s) dropped", ts)
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
            self.writer.spawn(self._store_settings, ts, decoded.settings)
        elif decoded.device_info is not None:
            self.state.device_info = decoded.device_info
            self.state.device_info_ts = ts
            self.writer.spawn(self.store.upsert_device_info, ts, decoded.device_info, SOURCE)
        else:
            self.state.counters.unknown_frames += 1
            log.debug("unknown frame type 0x%02x: %s", decoded.type, frame.hex(" "))

    def on_link_change(self, status: str) -> None:
        if status != self.state.link_status:
            log.info("BMS link: %s", status)
        self.state.link_status = status

    def on_event(self, type_: str, detail: str | None = None) -> None:
        if type_ == "ble_connected":
            if self._connected_once:
                self.state.counters.reconnects += 1
            self._connected_once = True
        self.writer.event(SOURCE, type_, detail)

    # --- internals ---

    def _store_settings(self, ts: int, data: dict) -> None:
        changed = self.store.insert_settings_if_changed(ts, data, SOURCE)
        if changed is not None:
            detail = ", ".join(changed[:20]) + ("…" if len(changed) > 20 else "") if changed else "initial settings"
            self.store.add_event(ts, "settings_changed", detail, SOURCE)

    def _dump(self, ts: int, frame: bytes) -> None:
        log.info("frame type 0x%02x: %s", p.frame_type(frame), frame.hex(" "))
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / f"{ts}_{p.frame_type(frame):02x}.hex").write_text(frame.hex(" ") + "\n")
        except OSError as e:
            log.warning("could not save frame dump: %s", e)
