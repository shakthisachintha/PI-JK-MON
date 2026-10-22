import struct
from pathlib import Path

import pytest

from ray_vault.bms import protocol as p
from ray_vault.bms.fake import FAKE_DEVICE_INFO, FAKE_SETTINGS

FIXTURES = Path(__file__).parent / "fixtures"


def _blank(ftype: int) -> bytearray:
    f = bytearray(p.FRAME_LEN)
    f[0:4] = p.FRAME_HEADER
    f[4] = ftype
    return f


def _seal(f: bytearray) -> bytes:
    f[299] = sum(f[:299]) & 0xFF
    return bytes(f)


# --- commands -----------------------------------------------------------------


def test_cell_info_command_matches_spec_example():
    expected = bytes.fromhex("AA 55 90 EB 96 00 00 00 00 00 00 00 00 00 00 00 00 00 00 10")
    assert p.build_command(p.COMMAND_CELL_INFO) == expected


def test_device_info_command():
    cmd = p.build_command(p.COMMAND_DEVICE_INFO)
    assert len(cmd) == 20
    assert cmd[:6] == bytes([0xAA, 0x55, 0x90, 0xEB, 0x97, 0x00])
    assert cmd[19] == sum(cmd[:19]) & 0xFF == 0x11


def test_command_value_is_little_endian():
    cmd = p.build_command(0x10, value=0x01020304, length=4)
    assert cmd[6:10] == bytes([4, 3, 2, 1])


# --- checksum and assembly ----------------------------------------------------


def test_checksum():
    f = _seal(_blank(p.FRAME_TYPE_CELLS))
    assert p.checksum_ok(f)
    bad = bytearray(f)
    bad[100] ^= 0x01
    assert not p.checksum_ok(bytes(bad))


@pytest.mark.parametrize("chunk_size", [20, 128, 185, 244, 300, 320])
def test_assembler_chunking(chunk_size):
    frame = p.encode_settings(FAKE_SETTINGS)
    a = p.FrameAssembler()
    out = []
    for i in range(0, len(frame), chunk_size):
        out += a.feed(frame[i : i + chunk_size])
    assert out == [frame]


def test_assembler_drops_stray_chunks_and_restarts_on_header():
    frame = p.encode_settings(FAKE_SETTINGS)
    a = p.FrameAssembler()
    assert a.feed(b"\x01\x02\x03") == []  # stray, no frame in progress
    assert a.feed(frame[:100]) == []
    # a new header discards the partial buffer
    assert a.feed(frame[:150]) == []
    assert a.feed(frame[150:]) == [frame]
    assert a.feed(b"\x00" * 50) == []  # stray after completion


def test_assembler_truncates_trailing_bytes_to_frame_len():
    frame = p.encode_settings(FAKE_SETTINGS)
    a = p.FrameAssembler()
    out = a.feed(frame[:200]) + a.feed(frame[200:] + b"\xee" * 15)
    assert out == [frame]


# --- decoding against hand-built frames at spec offsets -----------------------


def test_decode_cells_at_spec_offsets():
    f = _blank(p.FRAME_TYPE_CELLS)
    for n in range(16):
        struct.pack_into("<H", f, 6 + 2 * n, 3300 + n)
        struct.pack_into("<H", f, 80 + 2 * n, 50 + n)
    struct.pack_into("<H", f, 74, 3307)
    struct.pack_into("<H", f, 76, 15)
    struct.pack_into("<h", f, 144, 275)
    struct.pack_into("<I", f, 150, 53120)
    struct.pack_into("<i", f, 158, -4210)
    struct.pack_into("<h", f, 162, 241)
    struct.pack_into("<h", f, 164, -55)
    f[170], f[171] = 0x2C, 0x01  # +0.300 A
    f[172] = 2
    f[173] = 87
    struct.pack_into("<I", f, 174, 243600)
    struct.pack_into("<I", f, 178, 280000)
    struct.pack_into("<I", f, 182, 41)
    struct.pack_into("<I", f, 186, 11234500)
    f[194:197] = bytes([0x87, 0xD6, 0x12])  # 1234567
    f[198], f[199], f[201] = 1, 0, 3

    c = p.decode_cells(_seal(f))
    assert c.cell_voltages[0] == 3.300 and c.cell_voltages[15] == 3.315
    assert c.wire_resist[0] == 0.050 and c.wire_resist[15] == 0.065
    assert c.avg_cell_voltage == 3.307
    assert c.delta_cell_voltage == 0.015
    assert c.temp_mos == 27.5
    assert c.voltage == 53.12
    assert c.current == -4.21
    assert c.temp_t1 == 24.1
    assert c.temp_t2 == -5.5
    assert c.balance_current == 0.3
    assert c.balancing_action == 2
    assert c.soc == 87
    assert c.capacity_remain == 243.6
    assert c.nominal_capacity == 280.0
    assert c.cycle_count == 41
    assert c.cycle_capacity == 11234.5
    assert c.bms_uptime_s == 1234567
    assert (c.charge_on, c.discharge_on, c.balance_on) == (True, False, True)
    assert c.power == round(53.12 * -4.21, 2)


@pytest.mark.parametrize(
    "lo,hi,expected",
    [
        (0x00, 0x00, 0.0),
        (0xE8, 0x03, 1.0),  # +1000
        (0xFF, 0x0F, 4.095),
        (0xF4, 0xF1, -0.5),  # -(0x1F4)
        (0xE8, 0xF3, -1.0),
    ],
)
def test_balance_current_encoding(lo, hi, expected):
    f = _blank(p.FRAME_TYPE_CELLS)
    f[170], f[171] = lo, hi
    assert p.decode_cells(_seal(f)).balance_current == expected


def test_decode_settings_at_spec_offsets():
    f = _blank(p.FRAME_TYPE_SETTINGS)
    struct.pack_into("<I", f, 10, 2800)
    struct.pack_into("<I", f, 18, 3650)
    struct.pack_into("<I", f, 54, 30)
    struct.pack_into("<i", f, 98, -50)  # charge_utp -5.0 °C
    struct.pack_into("<I", f, 114, 16)
    struct.pack_into("<I", f, 130, 280000)
    struct.pack_into("<I", f, 138, 3400)
    s = p.decode_settings(_seal(f))
    assert s["cell_uvp"] == 2.8
    assert s["cell_ovp"] == 3.65
    assert s["charge_ocp_delay"] == 30
    assert s["charge_utp"] == -5.0
    assert s["cell_count"] == 16
    assert s["total_battery_capacity"] == 280.0
    assert s["balance_starting_voltage"] == 3.4
    assert len(s) == 29


def test_decode_device_info_drops_passcodes():
    f = _blank(p.FRAME_TYPE_DEVICE_INFO)
    f[6 : 6 + 8] = b"JK_B2A16"
    f[22 : 22 + 5] = b"10.XW"
    f[30 : 30 + 5] = b"10.07"
    struct.pack_into("<I", f, 38, 999)
    struct.pack_into("<I", f, 42, 7)
    f[46 : 46 + 6] = b"MyBMS "
    f[62 : 62 + 4] = b"1234"  # device_passcode
    f[86 : 86 + 11] = b"SN123456789"
    f[97 : 97 + 5] = b"00000"  # passcode
    f[118 : 118 + 6] = b"SECRET"  # setup_passcode
    d = p.decode_device_info(_seal(f))
    assert d["vendor_id"] == "JK_B2A16"
    assert d["hardware_version"] == "10.XW"
    assert d["software_version"] == "10.07"
    assert d["uptime_s"] == 999
    assert d["power_on_count"] == 7
    assert d["device_name"] == "MyBMS"
    assert d["serial_number"] == "SN123456789"
    assert not any("passcode" in k for k in d)
    assert "1234" not in d.values() and "SECRET" not in str(d)


def test_decode_frame_dispatch():
    assert p.decode_frame(p.encode_settings(FAKE_SETTINGS)).settings["cell_count"] == 8
    assert p.decode_frame(p.encode_device_info(FAKE_DEVICE_INFO)).device_info["device_name"] == "FAKE-BMS"
    unknown = p.decode_frame(_seal(_blank(0x07)))
    assert unknown.type == 0x07 and unknown.cells is None and unknown.settings is None


def test_settings_roundtrip():
    assert p.decode_settings(p.encode_settings(FAKE_SETTINGS)) == FAKE_SETTINGS


def test_active_cell_count():
    assert p.active_cell_count(8) == 8
    assert p.active_cell_count(0) == 16
    assert p.active_cell_count(24) == 16
    assert p.active_cell_count(None) == 16


# --- captured real frames (tests/fixtures/*.hex, added once available) ---------


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.hex")), ids=lambda x: x.name)
def test_fixture_frames_decode(path):
    frame = bytes.fromhex(path.read_text())
    assert len(frame) == p.FRAME_LEN
    assert p.checksum_ok(frame)
    p.decode_frame(frame)
