"""Bluetooth adapter administration on Linux/BlueZ: power-cycle the adapter, read a live connection's RSSI.

Both are best effort. Power-cycling goes through BlueZ's D-Bus API (allowed for the `bluetooth` group),
falling back to `btmgmt`. Live RSSI uses a raw HCI Read RSSI command (hci_rssi.py, needs CAP_NET_RAW),
falling back to `btmgmt conn-info` (needs CAP_NET_ADMIN); both are granted by the systemd unit. If neither
works, callers fall back to the RSSI seen in the last scan.
"""

from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger(__name__)

_RSSI_RE = re.compile(r"RSSI\s+(-?\d+)")
_rssi_disabled = False
_rssi_failures = 0
RSSI_MAX_FAILURES = 3  # consecutive failures before giving up on live RSSI


def adapter_index(adapter: str) -> str:
    m = re.fullmatch(r"hci(\d+)", adapter)
    return m.group(1) if m else "0"


async def _run(*cmd: str, timeout: float = 10) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode or 0, out.decode(errors="replace")


async def _dbus_set_powered(adapter: str, on: bool) -> None:
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=f"/org/bluez/{adapter}",
                interface="org.freedesktop.DBus.Properties",
                member="Set",
                signature="ssv",
                body=["org.bluez.Adapter1", "Powered", Variant("b", on)],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{reply.error_name}: {' '.join(map(str, reply.body))}")
    finally:
        bus.disconnect()


async def restart_bluetoothd(settle_s: float = 5) -> None:
    """Restart bluetooth.service via systemd's D-Bus API.

    Allowed for the service user by deploy/50-ray-vault.rules (polkit); raises if not permitted or it fails.
    """
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.freedesktop.systemd1",
                path="/org/freedesktop/systemd1",
                interface="org.freedesktop.systemd1.Manager",
                member="RestartUnit",
                signature="ss",
                body=["bluetooth.service", "replace"],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{reply.error_name}: {' '.join(map(str, reply.body))}")
    finally:
        bus.disconnect()
    await asyncio.sleep(settle_s)  # let bluetoothd re-register the adapter


async def power_cycle_adapter(adapter: str, settle_s: float = 3) -> str:
    """Power the adapter off and on again. Returns the method used; raises RuntimeError if all fail."""
    errors = []
    try:
        await _dbus_set_powered(adapter, False)
        await asyncio.sleep(settle_s)
        await _dbus_set_powered(adapter, True)
        await asyncio.sleep(settle_s)
        return "dbus"
    except Exception as e:
        errors.append(f"dbus: {type(e).__name__}: {e}")

    idx = adapter_index(adapter)
    try:
        rc, out = await _run("btmgmt", "--index", idx, "power", "off")
        if rc != 0:
            raise RuntimeError(out.strip() or f"exit {rc}")
        await asyncio.sleep(settle_s)
        rc, out = await _run("btmgmt", "--index", idx, "power", "on")
        if rc != 0:
            raise RuntimeError(out.strip() or f"exit {rc}")
        await asyncio.sleep(settle_s)
        return "btmgmt"
    except Exception as e:
        errors.append(f"btmgmt: {type(e).__name__}: {e}")

    raise RuntimeError("; ".join(errors))


async def connection_rssi(adapter: str, mac: str) -> int | None:
    """RSSI (dBm) of an established connection via `btmgmt conn-info`, or None if unavailable."""
    global _rssi_disabled, _rssi_failures
    if _rssi_disabled:
        return None
    from .hci_rssi import HciError, read_rssi

    try:
        rssi = await asyncio.to_thread(read_rssi, mac, adapter)
        _rssi_failures = 0
        return rssi
    except HciError as e:
        hci_error = str(e)

    idx = adapter_index(adapter)
    last = ""
    for addr_type in ("1", "2"):  # LE public, LE random
        try:
            rc, out = await _run("btmgmt", "--index", idx, "conn-info", "-t", addr_type, mac, timeout=5)
        except FileNotFoundError:
            last = "btmgmt not installed"
            _rssi_failures = RSSI_MAX_FAILURES
            break
        m = _RSSI_RE.search(out)
        if rc == 0 and m:
            _rssi_failures = 0
            return int(m.group(1))
        last = out.strip()
    _rssi_failures += 1
    if _rssi_failures >= RSSI_MAX_FAILURES:
        log.info(
            "live RSSI unavailable (HCI: %s; btmgmt: %s); using scan RSSI instead", hci_error, last[:200] or "no output"
        )
        _rssi_disabled = True
    return None
