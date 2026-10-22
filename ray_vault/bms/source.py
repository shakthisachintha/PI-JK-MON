"""The data-source interface shared by the BLE client and the fake generator (§11)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

OnFrame = Callable[[bytes], None]  # one complete FRAME_LEN-byte frame, checksum unchecked
OnLinkChange = Callable[[str], None]  # one of the state.LINK_* values
OnEvent = Callable[[str, "str | None"], None]  # (event type, detail) for the events table


class Source(Protocol):
    async def run(self, on_frame: OnFrame, on_link_change: OnLinkChange, on_event: OnEvent) -> None:
        """Run forever, delivering frames and link changes. Cancel the task to stop."""
        ...
