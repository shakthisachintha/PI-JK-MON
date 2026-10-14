"""BLE source: scan, connect, subscribe and supervise the link to one JK BMS (§5.3, §5.6).

Liveness is judged by *cell-data frames*, not by notifications: the BMS acknowledges every command with a
short notification, so re-requesting cell info would otherwise keep a dead data stream looking alive.

Recovery ladder while no cell data arrives:
  1. re-request cell info            every cell_rerequest_s
  2. disconnect and reconnect        after link_timeout_s without cell data on a connection
  3. power-cycle the BT adapter      after adapter_reset_after_s without cell data, then with doubling backoff
  4. exit so systemd restarts us     after restart_after_resets adapter resets that didn't help
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from . import protocol as p
from .bt_admin import connection_rssi, power_cycle_adapter
from .source import OnEvent, OnFrame, OnLinkChange
from .state import LINK_CONNECTED, LINK_CONNECTING, LINK_DISCONNECTED, LINK_SCANNING, BleStats, now_ms

log = logging.getLogger(__name__)

SCAN_WINDOW_S = 5.0
SCAN_GAP_S = 2.0
COMMAND_DELAY_S = 0.5
SUPERVISE_TICK_S = 1.0
BACKOFF_S = (5, 10, 30)


class LinkRecoveryExhausted(Exception):
    """Adapter resets did not bring data back; the process should exit and let systemd restart it."""


class RecoveryLadder:
    """Decides when to power-cycle the adapter or give up, from the time since the last cell-data frame."""

    def __init__(
        self,
        reset_after_s: float,
        max_interval_s: float,
        restart_after_resets: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reset_after_s = reset_after_s
        self.max_interval_s = max_interval_s
        self.restart_after_resets = restart_after_resets
        self.clock = clock
        self.good()

    def good(self) -> None:
        """Cell data arrived: everything is healthy again."""
        self.last_good = self.clock()
        self.resets = 0
        self.next_reset_at = self.reset_after_s  # seconds of stall

    def stalled_s(self) -> float:
        return self.clock() - self.last_good

    def next_action(self) -> str | None:
        """None, "reset" (power-cycle the adapter) or "restart" (give up; exit for systemd)."""
        if self.stalled_s() < self.next_reset_at:
            return None
        if self.restart_after_resets and self.resets >= self.restart_after_resets:
            return "restart"
        return "reset"

    def did_reset(self) -> None:
        self.resets += 1
        interval = min(self.reset_after_s * 2**self.resets, self.max_interval_s)
        self.next_reset_at = self.stalled_s() + interval


class BleSource:
    def __init__(
        self,
        mac: str,
        adapter: str = "hci0",
        cell_rerequest_s: float = 5,
        link_timeout_s: float = 20,
        adapter_reset_after_s: float = 120,
        adapter_reset_max_interval_s: float = 1800,
        restart_after_resets: int = 3,
        rssi_poll_s: float = 30,
        stats: BleStats | None = None,
    ) -> None:
        self.mac = mac.lower()
        self.adapter = adapter
        self.cell_rerequest_s = cell_rerequest_s
        self.link_timeout_s = link_timeout_s
        self.rssi_poll_s = rssi_poll_s
        self.stats = stats or BleStats()
        self.ladder = RecoveryLadder(adapter_reset_after_s, adapter_reset_max_interval_s, restart_after_resets)

    async def run(self, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        failures = 0
        while True:
            await self._recover_if_stalled(on_link_change, on_event)
            try:
                on_link_change(LINK_SCANNING)
                device = await self._scan_once()
                if device is None:
                    await asyncio.sleep(SCAN_GAP_S)
                    continue
                on_link_change(LINK_CONNECTING)
                await self._session(device, on_frame, on_link_change, on_event)
                failures = 0  # clean session end: go straight back to scanning
            except asyncio.CancelledError:
                raise
            except Exception as e:
                on_link_change(LINK_DISCONNECTED)
                delay = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
                failures += 1
                log.warning("BLE error (%s: %s); retrying in %ds", type(e).__name__, e, delay)
                await asyncio.sleep(delay)

    async def _recover_if_stalled(self, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        action = self.ladder.next_action()
        if action is None:
            return
        stalled = self.ladder.stalled_s()
        if action == "restart":
            detail = f"no cell data for {stalled:.0f}s after {self.ladder.resets} adapter reset(s); exiting"
            log.error(detail)
            on_event("service_restart", detail)
            raise LinkRecoveryExhausted(detail)

        self.stats.recovery_stage = "adapter_reset"
        on_link_change(LINK_DISCONNECTED)
        log.warning("no cell data for %.0fs; power-cycling %s", stalled, self.adapter)
        try:
            method = await power_cycle_adapter(self.adapter)
            on_event("adapter_reset", f"after {stalled:.0f}s without cell data (via {method})")
        except Exception as e:
            log.error("adapter reset failed: %s", e)
            on_event("adapter_reset", f"failed after {stalled:.0f}s without cell data: {e}")
        self.ladder.did_reset()
        self.stats.adapter_resets += 1
        self.stats.last_adapter_reset_ts = now_ms()

    async def _scan_once(self):
        """One scan window. Returns the BLEDevice, or None if it wasn't seen. Records its RSSI."""
        from bleak import BleakScanner

        seen_rssi: list[int] = []

        def match(d, ad) -> bool:
            if d.address.lower() == self.mac:
                seen_rssi.append(ad.rssi)
                return True
            return False

        log.debug("scanning for %s on %s", self.mac, self.adapter)
        device = await BleakScanner.find_device_by_filter(match, timeout=SCAN_WINDOW_S, bluez={"adapter": self.adapter})
        if device is not None:
            rssi = seen_rssi[-1] if seen_rssi else None
            log.info("found %s (%s), RSSI %s dBm", device.address, device.name, rssi)
            self._set_rssi(rssi, "scan")
        return device

    def _set_rssi(self, rssi: int | None, source: str) -> None:
        if rssi is None:
            return
        self.stats.rssi = rssi
        self.stats.rssi_ts = now_ms()
        self.stats.rssi_source = source

    async def _poll_rssi(self) -> None:
        while True:
            await asyncio.sleep(self.rssi_poll_s)
            self._set_rssi(await connection_rssi(self.adapter, self.mac), "connection")

    async def _session(self, device, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        from bleak import BleakClient

        assembler = p.FrameAssembler()
        disconnected = asyncio.Event()
        now = time.monotonic
        last_cells = now()
        last_cell_request = 0.0

        def on_notify(_char, data: bytearray) -> None:
            nonlocal last_cells
            for frame in assembler.feed(bytes(data)):
                if p.frame_type(frame) == p.FRAME_TYPE_CELLS and p.checksum_ok(frame):
                    last_cells = now()
                    self.ladder.good()
                    self.stats.recovery_stage = "ok"
                on_frame(frame)

        async def send(register: int) -> None:
            await client.write_gatt_char(p.CHAR_UUID, p.build_command(register), response=False)

        client = BleakClient(
            device,
            disconnected_callback=lambda _c: disconnected.set(),
            bluez={"adapter": self.adapter},
        )
        log.info("connecting to %s", self.mac)
        await client.connect()
        reason = "remote"
        rssi_task = None
        try:
            await client.start_notify(p.CHAR_UUID, on_notify)
            on_link_change(LINK_CONNECTED)
            on_event("ble_connected", f"{self.mac}, RSSI {self.stats.rssi} dBm")
            log.info("connected to %s", self.mac)
            rssi_task = asyncio.create_task(self._poll_rssi())

            await asyncio.sleep(COMMAND_DELAY_S)
            await send(p.COMMAND_DEVICE_INFO)
            await asyncio.sleep(COMMAND_DELAY_S)
            await send(p.COMMAND_CELL_INFO)
            last_cells = last_cell_request = now()  # give the BMS a full timeout window from here

            while not disconnected.is_set():
                try:
                    await asyncio.wait_for(disconnected.wait(), SUPERVISE_TICK_S)
                    break
                except TimeoutError:
                    pass
                t = now()
                if t - last_cells > self.link_timeout_s:
                    log.warning("no cell data for %.0fs; reconnecting", t - last_cells)
                    on_event("ble_timeout", f"no cell data for {t - last_cells:.0f}s")
                    self.stats.recovery_stage = "reconnecting"
                    reason = "timeout"
                    break
                if t - last_cells > self.cell_rerequest_s and t - last_cell_request > self.cell_rerequest_s:
                    log.info("no cell data for %.0fs; re-requesting cell info", t - last_cells)
                    on_event("cell_info_rerequest", None)
                    self.stats.recovery_stage = "rerequesting"
                    await send(p.COMMAND_CELL_INFO)
                    last_cell_request = t
        except Exception as e:
            reason = f"error: {type(e).__name__}: {e}"
            raise
        finally:
            if rssi_task:
                rssi_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await rssi_task
            try:
                await asyncio.wait_for(client.disconnect(), 10)
            except Exception as e:  # already gone, or BlueZ hiccup
                log.debug("disconnect: %s", e)
            on_link_change(LINK_DISCONNECTED)
            on_event("ble_disconnected", reason)
            log.info("disconnected from %s (%s)", self.mac, reason)
