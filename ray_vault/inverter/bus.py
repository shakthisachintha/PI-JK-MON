"""Modbus RTU access to the inverter.

All serial I/O runs on one dedicated thread, so requests are naturally serialised (polls and writes never
interleave on the wire) and a slow device never blocks the event loop. Failures surface as typed exceptions,
never as sentinel values.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

log = logging.getLogger(__name__)

INTER_FRAME_GAP_S = 0.02  # quiet time between transactions; some SRNE firmwares drop back-to-back requests


class BusError(Exception):
    """Base class for inverter communication failures."""


class PortError(BusError):
    """The serial port can't be opened (unplugged, wrong path, permissions)."""


class NoResponse(BusError):
    """The port works but the inverter didn't answer (or answered garbage)."""


class IllegalAddress(BusError):
    """The inverter answered but refused the request (any Modbus exception except a transient one).

    For reads this means "read a smaller range"; the driver falls back accordingly.
    """


# SRNE's own meanings for Modbus exception codes (docs/inverter/*protocol*.pdf). They differ from the
# standard names minimalmodbus prints, e.g. 10 is "length error" there, not "gateway path unavailable".
SRNE_ERRORS = {
    1: "illegal command",
    2: "illegal data address",
    3: "illegal data value",
    4: "operation failed (the parameter can't be set to this now)",
    5: "password error",
    6: "data frame error",
    7: "parameter is read-only",
    8: "parameter can't be changed while running",
    9: "password protection: the inverter is locked with a user password",
    10: "length error: too many registers in one request",
    11: "permission denied",
}

# minimalmodbus reports slave exceptions as text; map its wording back to the exception code.
_MINIMALMODBUS_TEXT = {
    "illegal function": 1,
    "illegal data address": 2,
    "illegal data value": 3,
    "device failure": 4,
    "device busy": 6,
    "negative acknowledge": 7,
    "memory parity error": 8,
    "gateway path unavailable": 10,
    "gateway target device failed to respond": 11,
}


class Refused(IllegalAddress):
    """The inverter rejected the request with an SRNE exception code."""

    def __init__(self, code: int | None, raw: str) -> None:
        self.code = code
        meaning = SRNE_ERRORS.get(code, raw) if code is not None else raw
        super().__init__(f"inverter refused: {meaning}" + (f" (code {code})" if code is not None else ""))


def exception_code(message: str) -> int | None:
    text = message.lower()
    for phrase, code in _MINIMALMODBUS_TEXT.items():
        if phrase in text:
            return code
    if "error code" in text:
        tail = text.rsplit("error code", 1)[1].strip().split()[0]
        return int(tail) if tail.isdigit() else None
    return None


class Bus(Protocol):
    async def read_block(self, start: int, count: int) -> list[int]: ...
    async def read_register(self, address: int) -> int: ...
    async def write_register(self, address: int, raw: int) -> None: ...
    async def reopen(self) -> None: ...
    async def close(self) -> None: ...


class ModbusBus:
    def __init__(self, port: str, slave: int = 1, baudrate: int = 9600, timeout_s: float = 1.0) -> None:
        self.port = port
        self.slave = slave
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modbus")
        self._instrument = None
        self._last_io = 0.0

    # --- thread side ---

    def _open(self):
        import minimalmodbus
        import serial

        if self._instrument is not None:
            return self._instrument
        try:
            inst = minimalmodbus.Instrument(self.port, self.slave, close_port_after_each_call=False)
        except (serial.SerialException, OSError) as e:
            raise PortError(f"cannot open {self.port}: {e}") from e
        inst.serial.baudrate = self.baudrate
        inst.serial.timeout = self.timeout_s
        inst.clear_buffers_before_each_transaction = True
        self._instrument = inst
        log.info("opened %s (slave %d, %d baud)", self.port, self.slave, self.baudrate)
        return inst

    def _close(self) -> None:
        inst, self._instrument = self._instrument, None
        if inst is not None:
            try:
                inst.serial.close()
            except Exception:
                pass

    def _call(self, fn_name: str, *args):
        import minimalmodbus
        import serial

        inst = self._open()
        gap = INTER_FRAME_GAP_S - (time.monotonic() - self._last_io)
        if gap > 0:
            time.sleep(gap)
        try:
            return getattr(inst, fn_name)(*args)
        except minimalmodbus.SlaveDeviceBusyError as e:
            # Code 6: "busy" in standard Modbus, "data frame error" (garbled request) for SRNE. Retry either way.
            raise NoResponse(f"inverter reported a frame error/busy: {e}") from e
        except minimalmodbus.SlaveReportedException as e:
            raise Refused(exception_code(str(e)), str(e)) from e
        except (minimalmodbus.NoResponseError, minimalmodbus.InvalidResponseError) as e:
            raise NoResponse(str(e)) from e
        except minimalmodbus.ModbusException as e:
            raise NoResponse(f"{type(e).__name__}: {e}") from e
        except (serial.SerialException, OSError) as e:
            self._close()  # the port is gone (USB unplugged); reopen on the next call
            raise PortError(str(e)) from e
        finally:
            self._last_io = time.monotonic()

    # --- async API ---

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def read_block(self, start: int, count: int) -> list[int]:
        return await self._run(self._call, "read_registers", start, count, 3)

    async def read_register(self, address: int) -> int:
        return (await self.read_block(address, 1))[0]

    async def write_register(self, address: int, raw: int) -> None:
        # Function code 6 (write single register), as SRNE documents; raw is the unsigned register value.
        await self._run(self._call, "write_register", address, raw, 0, 6, False)

    async def reopen(self) -> None:
        await self._run(self._close)

    async def close(self) -> None:
        await self._run(self._close)
        self._executor.shutdown(wait=False)
