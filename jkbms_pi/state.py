"""In-memory latest state shared by the source, sampler and HTTP API (§6)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .protocol import CellData, active_cell_count

LINK_SCANNING = "scanning"
LINK_CONNECTING = "connecting"
LINK_CONNECTED = "connected"
LINK_DISCONNECTED = "disconnected"


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Counters:
    frames_ok: int = 0
    crc_errors: int = 0
    reconnects: int = 0
    unknown_frames: int = 0


@dataclass
class BleStats:
    """Link diagnostics written by the source, read by the API."""

    rssi: int | None = None  # dBm
    rssi_ts: int | None = None  # UTC epoch ms
    rssi_source: str | None = None  # "connection" (live) or "scan" (last advertisement)
    adapter_resets: int = 0
    last_adapter_reset_ts: int | None = None
    recovery_stage: str = "ok"  # ok | rerequesting | reconnecting | adapter_reset


@dataclass
class State:
    latest_cells: CellData | None = None
    latest_cells_ts: int | None = None  # UTC epoch ms when received
    latest_settings: dict | None = None
    latest_settings_ts: int | None = None
    device_info: dict | None = None
    device_info_ts: int | None = None
    link_status: str = LINK_SCANNING
    counters: Counters = field(default_factory=Counters)
    ble: BleStats = field(default_factory=BleStats)
    started_ms: int = field(default_factory=now_ms)

    def cells_age_s(self, at_ms: int | None = None) -> float | None:
        if self.latest_cells_ts is None:
            return None
        return max(0.0, ((at_ms or now_ms()) - self.latest_cells_ts) / 1000)

    @property
    def cell_count(self) -> int:
        """Active cell count from the settings frame, else all 16 (§5.5)."""
        cc = self.latest_settings.get("cell_count") if self.latest_settings else None
        return active_cell_count(cc)

    @property
    def cell_count_known(self) -> bool:
        cc = self.latest_settings.get("cell_count") if self.latest_settings else None
        return active_cell_count(cc) == cc


@dataclass
class CellSnapshot:
    """A CellData trimmed to active cells, with derived min/max."""

    cells: CellData
    cell_voltages: list[float]
    wire_resist: list[float]
    min_cell_voltage: float | None
    max_cell_voltage: float | None
    min_cell_index: int | None  # 1-based, as numbered in the JK app
    max_cell_index: int | None


def snapshot(cells: CellData, cell_count: int, cell_count_known: bool) -> CellSnapshot:
    volts = cells.cell_voltages[:cell_count]
    resist = cells.wire_resist[:cell_count]
    # When the cell count is unknown, 0.000 V slots are unused cells; ignore them for min/max.
    candidates = [(v, i) for i, v in enumerate(volts) if cell_count_known or v > 0]
    if candidates:
        vmin, imin = min(candidates)
        vmax, imax = max(candidates, key=lambda t: (t[0], -t[1]))
        return CellSnapshot(cells, volts, resist, vmin, vmax, imin + 1, imax + 1)
    return CellSnapshot(cells, volts, resist, None, None, None, None)
