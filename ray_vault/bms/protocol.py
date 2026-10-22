"""JK BMS BLE protocol (JK02 layout).

Pure module: no I/O, no asyncio. Bytes in, dataclasses out. Every frame offset
lives here (spec §5.5, §5.7); if a BMS uses a different layout, this is the only
file to change.
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass

# --- GATT (§5.1) --------------------------------------------------------------

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# --- Commands (§5.2) ----------------------------------------------------------

COMMAND_HEADER = bytes([0xAA, 0x55, 0x90, 0xEB])
COMMAND_LEN = 20
COMMAND_CELL_INFO = 0x96
COMMAND_DEVICE_INFO = 0x97


def build_command(register: int, value: int = 0, length: int = 0) -> bytes:
    """Build a 20-byte command frame with its trailing checksum."""
    body = bytearray(COMMAND_LEN)
    body[0:4] = COMMAND_HEADER
    body[4] = register & 0xFF
    body[5] = length & 0xFF
    body[6:10] = struct.pack("<I", value & 0xFFFFFFFF)
    body[19] = sum(body[0:19]) & 0xFF
    return bytes(body)


# --- Frames (§5.4) ------------------------------------------------------------

FRAME_HEADER = bytes([0x55, 0xAA, 0xEB, 0x90])
FRAME_LEN = 300
FRAME_BUFFER_CAP = 320

FRAME_TYPE_SETTINGS = 0x01
FRAME_TYPE_CELLS = 0x02
FRAME_TYPE_DEVICE_INFO = 0x03

MAX_CELLS = 16


def frame_type(frame: bytes) -> int:
    return frame[4]


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def checksum_ok(frame: bytes) -> bool:
    return len(frame) >= FRAME_LEN and frame[FRAME_LEN - 1] == checksum(frame[: FRAME_LEN - 1])


class FrameAssembler:
    """Reassembles notification chunks into 300-byte frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False

    def feed(self, chunk: bytes) -> list[bytes]:
        """Feed one notification chunk; return any frames it completed.

        Returned frames are exactly FRAME_LEN bytes; the checksum is not checked here.
        """
        if chunk[: len(FRAME_HEADER)] == FRAME_HEADER:
            self._buf = bytearray()
            self._in_frame = True
        elif not self._in_frame:
            return []  # stray chunk

        room = FRAME_BUFFER_CAP - len(self._buf)
        self._buf += chunk[:room]

        if len(self._buf) >= FRAME_LEN:
            frame = bytes(self._buf[:FRAME_LEN])
            self._buf = bytearray()
            self._in_frame = False
            return [frame]
        return []


# --- Field helpers ------------------------------------------------------------


def _u8(f: bytes, o: int) -> int:
    return f[o]


def _u16(f: bytes, o: int) -> int:
    return struct.unpack_from("<H", f, o)[0]


def _i16(f: bytes, o: int) -> int:
    return struct.unpack_from("<h", f, o)[0]


def _u24(f: bytes, o: int) -> int:
    return f[o] | (f[o + 1] << 8) | (f[o + 2] << 16)


def _u32(f: bytes, o: int) -> int:
    return struct.unpack_from("<I", f, o)[0]


def _i32(f: bytes, o: int) -> int:
    return struct.unpack_from("<i", f, o)[0]


def _scaled(raw: int, scale: float) -> float:
    decimals = {1: 0, 0.1: 1, 0.01: 2, 0.001: 3}[scale]
    return round(raw * scale, decimals) if decimals else float(raw)


def _ascii(f: bytes, o: int, n: int) -> str:
    return f[o : o + n].split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


# --- Cell-data frame (type 0x02) ----------------------------------------------

CELL_VOLTAGE_OFFSET = 6
AVG_CELL_VOLTAGE_OFFSET = 74
DELTA_CELL_VOLTAGE_OFFSET = 76
WIRE_RESIST_OFFSET = 80
TEMP_MOS_OFFSET = 144
VOLTAGE_OFFSET = 150
CURRENT_OFFSET = 158
TEMP_T1_OFFSET = 162
TEMP_T2_OFFSET = 164
BALANCE_CURRENT_OFFSET = 170
BALANCING_ACTION_OFFSET = 172
SOC_OFFSET = 173
CAPACITY_REMAIN_OFFSET = 174
NOMINAL_CAPACITY_OFFSET = 178
CYCLE_COUNT_OFFSET = 182
CYCLE_CAPACITY_OFFSET = 186
BMS_UPTIME_OFFSET = 194
CHARGE_ON_OFFSET = 198
DISCHARGE_ON_OFFSET = 199
BALANCE_ON_OFFSET = 201


@dataclass
class CellData:
    cell_voltages: list[float]  # V, always MAX_CELLS entries; trim with active_cells()
    avg_cell_voltage: float
    delta_cell_voltage: float
    wire_resist: list[float]  # Ohm, MAX_CELLS entries
    temp_mos: float
    voltage: float
    current: float
    temp_t1: float
    temp_t2: float
    balance_current: float
    balancing_action: int
    soc: int
    capacity_remain: float
    nominal_capacity: float
    cycle_count: int
    cycle_capacity: float
    bms_uptime_s: int
    charge_on: bool
    discharge_on: bool
    balance_on: bool

    @property
    def power(self) -> float:
        return round(self.voltage * self.current, 2)


def _decode_balance_current(f: bytes) -> float:
    lo, hi = f[BALANCE_CURRENT_OFFSET], f[BALANCE_CURRENT_OFFSET + 1]
    if hi & 0xF0 == 0xF0:
        return -_scaled(((hi & 0x0F) << 8) | lo, 0.001)
    return _scaled((hi << 8) | lo, 0.001)


def _encode_balance_current(amps: float) -> bytes:
    raw = round(abs(amps) * 1000)
    if amps < 0:
        raw = min(raw, 0x0FFF)
        return bytes([raw & 0xFF, 0xF0 | (raw >> 8)])
    raw = min(raw, 0x0FFF)
    return bytes([raw & 0xFF, raw >> 8])


def decode_cells(f: bytes) -> CellData:
    return CellData(
        cell_voltages=[_scaled(_u16(f, CELL_VOLTAGE_OFFSET + 2 * n), 0.001) for n in range(MAX_CELLS)],
        avg_cell_voltage=_scaled(_u16(f, AVG_CELL_VOLTAGE_OFFSET), 0.001),
        delta_cell_voltage=_scaled(_u16(f, DELTA_CELL_VOLTAGE_OFFSET), 0.001),
        wire_resist=[_scaled(_u16(f, WIRE_RESIST_OFFSET + 2 * n), 0.001) for n in range(MAX_CELLS)],
        temp_mos=_scaled(_i16(f, TEMP_MOS_OFFSET), 0.1),
        voltage=_scaled(_u32(f, VOLTAGE_OFFSET), 0.001),
        current=_scaled(_i32(f, CURRENT_OFFSET), 0.001),
        temp_t1=_scaled(_i16(f, TEMP_T1_OFFSET), 0.1),
        temp_t2=_scaled(_i16(f, TEMP_T2_OFFSET), 0.1),
        balance_current=_decode_balance_current(f),
        balancing_action=_u8(f, BALANCING_ACTION_OFFSET),
        soc=_u8(f, SOC_OFFSET),
        capacity_remain=_scaled(_u32(f, CAPACITY_REMAIN_OFFSET), 0.001),
        nominal_capacity=_scaled(_u32(f, NOMINAL_CAPACITY_OFFSET), 0.001),
        cycle_count=_u32(f, CYCLE_COUNT_OFFSET),
        cycle_capacity=_scaled(_u32(f, CYCLE_CAPACITY_OFFSET), 0.001),
        bms_uptime_s=_u24(f, BMS_UPTIME_OFFSET),
        charge_on=_u8(f, CHARGE_ON_OFFSET) > 0,
        discharge_on=_u8(f, DISCHARGE_ON_OFFSET) > 0,
        balance_on=_u8(f, BALANCE_ON_OFFSET) > 0,
    )


def active_cell_count(cell_count: int | None) -> int:
    """Number of cells to keep: cell_count when plausible, otherwise all MAX_CELLS."""
    return cell_count if cell_count and 0 < cell_count <= MAX_CELLS else MAX_CELLS


# --- Settings frame (type 0x01) -----------------------------------------------

# (offset, name, scale, signed)
SETTINGS_FIELDS: tuple[tuple[int, str, float, bool], ...] = (
    (10, "cell_uvp", 0.001, False),
    (14, "cell_uvpr", 0.001, False),
    (18, "cell_ovp", 0.001, False),
    (22, "cell_ovpr", 0.001, False),
    (26, "balance_trigger_voltage", 0.001, False),
    (46, "power_off_voltage", 0.001, False),
    (50, "max_charge_current", 0.001, False),
    (54, "charge_ocp_delay", 1, False),
    (58, "charge_ocp_recovery", 1, False),
    (62, "max_discharge_current", 0.001, False),
    (66, "discharge_ocp_delay", 1, False),
    (70, "discharge_ocp_recovery", 1, False),
    (74, "short_circuit_recovery", 1, False),
    (78, "max_balance_current", 0.001, False),
    (82, "charge_otp", 0.1, True),
    (86, "charge_otpr", 0.1, True),
    (90, "discharge_otp", 0.1, True),
    (94, "discharge_otpr", 0.1, True),
    (98, "charge_utp", 0.1, True),
    (102, "charge_utpr", 0.1, True),
    (106, "mos_otp", 0.1, True),
    (110, "mos_otpr", 0.1, True),
    (114, "cell_count", 1, False),
    (118, "charge_switch", 1, False),
    (122, "discharge_switch", 1, False),
    (126, "balancer_switch", 1, False),
    (130, "total_battery_capacity", 0.001, False),
    (134, "short_circuit_delay", 1, False),
    (138, "balance_starting_voltage", 0.001, False),
)


def decode_settings(f: bytes) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    for offset, name, scale, signed in SETTINGS_FIELDS:
        raw = _i32(f, offset) if signed else _u32(f, offset)
        out[name] = raw if scale == 1 else _scaled(raw, scale)
    return out


# --- Device-info frame (type 0x03) --------------------------------------------

# (offset, length, name). Passcode fields (62, 97, 118) are deliberately absent:
# they are never decoded, stored, logged or returned (§5.5, §13).
DEVICE_INFO_ASCII_FIELDS: tuple[tuple[int, int, str], ...] = (
    (6, 16, "vendor_id"),
    (22, 8, "hardware_version"),
    (30, 8, "software_version"),
    (46, 16, "device_name"),
    (78, 8, "manufacturing_date"),
    (86, 11, "serial_number"),
    (102, 16, "user_data"),
)
DEVICE_INFO_U32_FIELDS: tuple[tuple[int, str], ...] = (
    (38, "uptime_s"),
    (42, "power_on_count"),
)


def decode_device_info(f: bytes) -> dict[str, str | int]:
    out: dict[str, str | int] = {}
    for offset, length, name in DEVICE_INFO_ASCII_FIELDS:
        out[name] = _ascii(f, offset, length)
    for offset, name in DEVICE_INFO_U32_FIELDS:
        out[name] = _u32(f, offset)
    return out


# --- Decoding entry point -----------------------------------------------------


@dataclass
class DecodedFrame:
    type: int
    cells: CellData | None = None
    settings: dict | None = None
    device_info: dict | None = None


def decode_frame(frame: bytes) -> DecodedFrame:
    """Decode a complete frame. Caller must check checksum_ok() first."""
    t = frame_type(frame)
    if t == FRAME_TYPE_CELLS:
        return DecodedFrame(t, cells=decode_cells(frame))
    if t == FRAME_TYPE_SETTINGS:
        return DecodedFrame(t, settings=decode_settings(frame))
    if t == FRAME_TYPE_DEVICE_INFO:
        return DecodedFrame(t, device_info=decode_device_info(frame))
    return DecodedFrame(t)


# --- Encoders (for tests and --fake; use the same offsets as the decoders) ----


def _new_frame(ftype: int) -> bytearray:
    f = bytearray(FRAME_LEN)
    f[0:4] = FRAME_HEADER
    f[4] = ftype
    return f


def _seal(f: bytearray) -> bytes:
    f[FRAME_LEN - 1] = checksum(f[: FRAME_LEN - 1])
    return bytes(f)


def encode_cells(c: CellData) -> bytes:
    f = _new_frame(FRAME_TYPE_CELLS)
    for n, v in enumerate(c.cell_voltages[:MAX_CELLS]):
        struct.pack_into("<H", f, CELL_VOLTAGE_OFFSET + 2 * n, round(v * 1000))
    struct.pack_into("<H", f, AVG_CELL_VOLTAGE_OFFSET, round(c.avg_cell_voltage * 1000))
    struct.pack_into("<H", f, DELTA_CELL_VOLTAGE_OFFSET, round(c.delta_cell_voltage * 1000))
    for n, r in enumerate(c.wire_resist[:MAX_CELLS]):
        struct.pack_into("<H", f, WIRE_RESIST_OFFSET + 2 * n, round(r * 1000))
    struct.pack_into("<h", f, TEMP_MOS_OFFSET, round(c.temp_mos * 10))
    struct.pack_into("<I", f, VOLTAGE_OFFSET, round(c.voltage * 1000))
    struct.pack_into("<i", f, CURRENT_OFFSET, round(c.current * 1000))
    struct.pack_into("<h", f, TEMP_T1_OFFSET, round(c.temp_t1 * 10))
    struct.pack_into("<h", f, TEMP_T2_OFFSET, round(c.temp_t2 * 10))
    f[BALANCE_CURRENT_OFFSET : BALANCE_CURRENT_OFFSET + 2] = _encode_balance_current(c.balance_current)
    f[BALANCING_ACTION_OFFSET] = c.balancing_action
    f[SOC_OFFSET] = c.soc
    struct.pack_into("<I", f, CAPACITY_REMAIN_OFFSET, round(c.capacity_remain * 1000))
    struct.pack_into("<I", f, NOMINAL_CAPACITY_OFFSET, round(c.nominal_capacity * 1000))
    struct.pack_into("<I", f, CYCLE_COUNT_OFFSET, c.cycle_count)
    struct.pack_into("<I", f, CYCLE_CAPACITY_OFFSET, round(c.cycle_capacity * 1000))
    up = c.bms_uptime_s & 0xFFFFFF
    f[BMS_UPTIME_OFFSET : BMS_UPTIME_OFFSET + 3] = bytes([up & 0xFF, (up >> 8) & 0xFF, up >> 16])
    f[CHARGE_ON_OFFSET] = int(c.charge_on)
    f[DISCHARGE_ON_OFFSET] = int(c.discharge_on)
    f[BALANCE_ON_OFFSET] = int(c.balance_on)
    return _seal(f)


def encode_settings(values: dict[str, float | int]) -> bytes:
    f = _new_frame(FRAME_TYPE_SETTINGS)
    for offset, name, scale, signed in SETTINGS_FIELDS:
        raw = round(values.get(name, 0) / scale)
        struct.pack_into("<i" if signed else "<I", f, offset, raw)
    return _seal(f)


def encode_device_info(values: dict[str, str | int]) -> bytes:
    f = _new_frame(FRAME_TYPE_DEVICE_INFO)
    for offset, length, name in DEVICE_INFO_ASCII_FIELDS:
        data = str(values.get(name, "")).encode("ascii")[:length]
        f[offset : offset + len(data)] = data
    for offset, name in DEVICE_INFO_U32_FIELDS:
        struct.pack_into("<I", f, offset, int(values.get(name, 0)))
    return _seal(f)


def cells_to_dict(c: CellData) -> dict:
    d = asdict(c)
    d["power"] = c.power
    return d
