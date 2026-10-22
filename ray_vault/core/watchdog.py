"""systemd integration: READY=1 once the HTTP server is up, then WATCHDOG=1 heartbeats while healthy.

With `Type=notify` and `WatchdogSec=` in the unit, systemd restarts the service if the heartbeat stops, i.e.
if the event loop hangs or the database stops responding. Outside systemd (no NOTIFY_SOCKET) it does nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable

from .store import Store

log = logging.getLogger(__name__)

DB_CHECK_TIMEOUT_S = 10


def sd_notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except OSError as e:
        log.warning("sd_notify failed: %s", e)
        return False


class Watchdog:
    def __init__(self, store: Store) -> None:
        self.store = store
        usec = int(os.environ.get("WATCHDOG_USEC", "0") or 0)
        self.interval_s = usec / 2e6 if usec else None  # ping at half the timeout

    async def _healthy(self) -> bool:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self.store.check_writable), DB_CHECK_TIMEOUT_S)
        except Exception:
            return False

    async def run(self, ready: Callable[[], bool]) -> None:
        if not os.environ.get("NOTIFY_SOCKET"):
            return
        while not ready():
            await asyncio.sleep(0.2)
        sd_notify("READY=1")
        log.info("notified systemd: ready%s", f", watchdog every {self.interval_s:.0f}s" if self.interval_s else "")
        if not self.interval_s:
            return
        while True:
            if await self._healthy():
                sd_notify("WATCHDOG=1")
            else:
                log.error("health check failed; withholding watchdog ping")
            await asyncio.sleep(self.interval_s)

    def stopping(self) -> None:
        sd_notify("STOPPING=1")
