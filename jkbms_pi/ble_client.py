"""BLE source: scan, connect, subscribe and supervise the link to one JK BMS (§5.3, §5.6)."""

from __future__ import annotations

import asyncio
import logging
import time

from . import protocol as p
from .source import OnEvent, OnFrame, OnLinkChange
from .state import LINK_CONNECTED, LINK_CONNECTING, LINK_DISCONNECTED, LINK_SCANNING

log = logging.getLogger(__name__)

SCAN_WINDOW_S = 5.0
SCAN_GAP_S = 2.0
COMMAND_DELAY_S = 0.5
SUPERVISE_TICK_S = 1.0
BACKOFF_S = (5, 10, 30)


class BleSource:
    def __init__(
        self,
        mac: str,
        adapter: str = "hci0",
        cell_rerequest_s: float = 5,
        link_timeout_s: float = 20,
    ) -> None:
        self.mac = mac.lower()
        self.adapter = adapter
        self.cell_rerequest_s = cell_rerequest_s
        self.link_timeout_s = link_timeout_s

    async def run(self, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        failures = 0
        while True:
            try:
                device = await self._scan(on_link_change)
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

    async def _scan(self, on_link_change: OnLinkChange):
        from bleak import BleakScanner

        on_link_change(LINK_SCANNING)
        log.info("scanning for %s on %s", self.mac, self.adapter)
        while True:
            device = await BleakScanner.find_device_by_address(
                self.mac, timeout=SCAN_WINDOW_S, bluez={"adapter": self.adapter}
            )
            if device is not None:
                log.info("found %s (%s)", device.address, device.name)
                return device
            await asyncio.sleep(SCAN_GAP_S)

    async def _session(self, device, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        from bleak import BleakClient

        assembler = p.FrameAssembler()
        disconnected = asyncio.Event()
        now = time.monotonic
        last_notify = now()
        last_cells = 0.0
        last_cell_request = 0.0

        def on_notify(_char, data: bytearray) -> None:
            nonlocal last_notify, last_cells
            last_notify = now()
            for frame in assembler.feed(bytes(data)):
                if p.frame_type(frame) == p.FRAME_TYPE_CELLS:
                    last_cells = now()
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
        try:
            await client.start_notify(p.CHAR_UUID, on_notify)
            on_link_change(LINK_CONNECTED)
            on_event("ble_connected", self.mac)
            log.info("connected to %s", self.mac)
            last_notify = now()

            await asyncio.sleep(COMMAND_DELAY_S)
            await send(p.COMMAND_DEVICE_INFO)
            await asyncio.sleep(COMMAND_DELAY_S)
            await send(p.COMMAND_CELL_INFO)
            last_cell_request = now()

            while not disconnected.is_set():
                try:
                    await asyncio.wait_for(disconnected.wait(), SUPERVISE_TICK_S)
                    break
                except TimeoutError:
                    pass
                t = now()
                if t - last_notify > self.link_timeout_s:
                    log.warning("no notifications for %.0fs; disconnecting", t - last_notify)
                    on_event("ble_timeout", f"no notifications for {t - last_notify:.0f}s")
                    reason = "timeout"
                    break
                if t - last_cells > self.cell_rerequest_s and t - last_cell_request > self.cell_rerequest_s:
                    log.info("no cell data; re-requesting cell info")
                    on_event("cell_info_rerequest", None)
                    await send(p.COMMAND_CELL_INFO)
                    last_cell_request = t
        except Exception as e:
            reason = f"error: {type(e).__name__}: {e}"
            raise
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), 10)
            except Exception as e:  # already gone, or BlueZ hiccup
                log.debug("disconnect: %s", e)
            on_link_change(LINK_DISCONNECTED)
            on_event("ble_disconnected", reason)
            log.info("disconnected from %s (%s)", self.mac, reason)
