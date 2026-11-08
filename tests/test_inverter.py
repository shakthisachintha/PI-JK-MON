import asyncio

import pytest

from ray_vault.bms import protocol as bp
from ray_vault.bms.fake import FAKE_SETTINGS as BMS_SETTINGS
from ray_vault.inverter.bus import IllegalAddress, ModbusBus, NoResponse
from ray_vault.inverter.controls import ControlError, PinGuard, validate_value
from ray_vault.inverter.driver import (
    LINK_CONNECTED,
    LINK_NO_RESPONSE,
    LINK_PORT_MISSING,
    infer_system_voltage,
    runs,
)
from ray_vault.inverter.probe import probe
from ray_vault.inverter.profiles import get_profile, hf
from ray_vault.inverter.registers import Block, Reading, SettingSpec
from tests.conftest import PIN, feed_bms, make_env

# --- registers ------------------------------------------------------------------


def test_reading_decode():
    assert Reading("v", 0, 0.1).decode(265) == 26.5
    assert Reading("t", 0, 0.1, signed=True).decode(0xFFF6) == -1.0
    assert Reading("i", 0, 0.1, signed=True, negate=True).decode(100) == -10.0  # inverter + = discharging
    assert Reading("f", 0, 0.01).decode(4999) == 49.99
    assert Reading("w", 0).decode(253) == 253


def test_packed_byte_setting():
    hi = SettingSpec("c", "c", "g", 0xE00F, byte="hi", min=0, max=100)
    lo = SettingSpec("d", "d", "g", 0xE00F, byte="lo", min=0, max=100)
    reg = (100 << 8) | 15
    assert hi.from_raw(reg) == 100 and lo.from_raw(reg) == 15
    assert lo.to_raw(20, reg) == (100 << 8) | 20  # other byte preserved
    assert hi.to_raw(95, reg) == (95 << 8) | 15
    with pytest.raises(ValueError):
        lo.to_raw(20)


def test_battery_scaled_setting():
    s = hf.PROFILE.setting("charge_limit_voltage")
    assert s.from_raw(144) == 14.4
    assert s.to_display(14.4, 24) == 28.8
    assert s.from_display(28.8, 24) == pytest.approx(14.4)
    assert s.to_raw(14.4) == 144
    d = s.describe(24)
    assert (d["min"], d["max"], d["step"], d["danger"]) == (18.0, 31.0, 0.2, True)


def test_infer_system_voltage():
    assert infer_system_voltage(24, None) == 24
    assert infer_system_voltage(255, 26.4) == 24  # 255 = auto-detect on the inverter
    assert infer_system_voltage(None, 53.1) == 48
    assert infer_system_voltage(None, None) is None


def test_profile_registry_and_integrity():
    assert get_profile("hf") is hf
    with pytest.raises(ValueError):
        get_profile("nope")
    keys = [s.key for s in hf.PROFILE.settings]
    assert len(keys) == len(set(keys))
    for a, _op, b in hf.PROFILE.ordering:
        hf.PROFILE.setting(a), hf.PROFILE.setting(b)
    covered = {a for blk in hf.PROFILE.settings_blocks for a in blk.addresses}
    assert all(s.address in covered for s in hf.PROFILE.settings), "every setting must be read by a block"
    live = {a for blk in hf.PROFILE.live_blocks for a in blk.addresses}
    assert all(r.address in live for r in hf.PROFILE.readings)


# --- driver -----------------------------------------------------------------------


async def run_until(driver, cond, timeout=3.0):
    task = asyncio.create_task(driver.run())
    try:
        async with asyncio.timeout(timeout):
            while not cond():
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await driver.writer.flush()


def connected(env):
    asyncio.run(run_until(env.inverter, lambda: env.inverter.state.settings_ts is not None))


def test_driver_polls_live_settings_and_info(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    st = env.inverter.state
    assert st.link == LINK_CONNECTED
    assert 0 <= st.live["soc"] <= 100
    assert 20 < st.live["battery_voltage"] < 30
    assert st.live["machine_state_label"] in ("On inverter", "On mains")
    assert st.device["model"] == "HF2430S80-H" and st.device["software_version"] == "1.05"
    assert env.inverter.system_voltage == 24
    snap = env.inverter.settings_snapshot()
    assert snap["output_priority"] == 2 and snap["charge_limit_voltage"] == 28.8
    assert snap["charge_cutoff_soc"] == 100 and snap["discharge_cutoff_soc"] == 15
    # reads are chunked to the HF limit of 12 registers
    assert max(env.inverter.bus.reads) <= 12
    # the fake rejects 0x0208-0x020B, so the chunk containing them is read as the run before them
    plan = next(p for b, p in env.inverter._plans.items() if b.start == 0x0200)
    assert [(r.start, r.count) for r in plan] == [(0x0200, 8)]
    assert env.inverter.sample(st.live_ts)[0] == st.live_ts
    types = [e["type"] for e in env.store.events(source="inverter")]
    assert "inverter_connected" in types
    assert env.store.device_info("inverter")[1]["model"] == "HF2430S80-H"
    env.store.close()


def test_driver_supervision_no_response_then_recovery(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bus, d = env.inverter.bus, env.inverter
    bus.fail_next = 4
    asyncio.run(run_until(d, lambda: d.state.link == LINK_NO_RESPONSE))
    assert d.state.counters.reopens == 1
    asyncio.run(run_until(d, lambda: d.state.link == LINK_CONNECTED))
    types = [e["type"] for e in env.store.events(source="inverter")]
    assert "inverter_no_response" in types and types.count("inverter_connected") >= 2
    env.store.close()


def test_driver_port_missing(tmp_path, monkeypatch):
    from ray_vault.inverter import driver as drv

    monkeypatch.setattr(drv, "PORT_BACKOFF_S", (0.01,))
    env = make_env(tmp_path)
    env.inverter.bus.port_missing = True
    asyncio.run(run_until(env.inverter, lambda: env.inverter.state.link == LINK_PORT_MISSING))
    assert env.inverter.sample(1) is None
    env.inverter.bus.port_missing = False
    asyncio.run(run_until(env.inverter, lambda: env.inverter.state.link == LINK_CONNECTED))
    env.store.close()


def test_probe_is_read_only(tmp_path):
    env = make_env(tmp_path)
    lines = []
    assert asyncio.run(probe(env.inverter.bus, hf, out=lines.append)) == 0
    text = "\n".join(lines)
    assert "HF2430S80-H" in text and "Charge limit voltage" in text and "28.8" in text
    assert env.inverter.bus.writes == []
    env.store.close()


# --- controls ---------------------------------------------------------------------


def test_pin_guard_lockout():
    t = [0.0]
    g = PinGuard("1234", max_failures=3, lockout_s=60, clock=lambda: t[0])
    g.check("a", "1234")
    for _ in range(2):
        with pytest.raises(ControlError) as e:
            g.check("a", "0000")
        assert e.value.status == 401
    with pytest.raises(ControlError) as e:
        g.check("a", "0000")
    assert e.value.status == 423
    with pytest.raises(ControlError) as e:
        g.check("a", "1234")  # even the right PIN while locked
    assert e.value.status == 423
    g.check("b", "1234")  # other clients unaffected
    t[0] = 61
    g.check("a", "1234")
    with pytest.raises(ControlError) as e:
        PinGuard("").check("a", "1234")
    assert e.value.status == 403


def test_validate_value():
    s = hf.PROFILE.setting("max_charge_current")
    assert validate_value(s, 40) == 40
    for bad in (85, 42.5, -5, True, "40"):
        with pytest.raises(ControlError):
            validate_value(s, bad)
    e = hf.PROFILE.setting("output_priority")
    assert validate_value(e, 1) == 1
    with pytest.raises(ControlError):
        validate_value(e, 3)
    with pytest.raises(ControlError):
        validate_value(hf.PROFILE.setting("system_voltage"), 24)


def bms_8s(env):
    feed_bms(env, bp.encode_settings({**BMS_SETTINGS, "cell_count": 8, "cell_ovp": 3.65, "cell_uvp": 2.8}))


def write(env, key, value, pin=PIN, confirm=False):
    headers = {"X-Control-Pin": pin} if pin is not None else {}
    return env.client.post(f"/api/inverter/settings/{key}", json={"value": value, "confirm": confirm}, headers=headers)


def test_write_enum_with_readback_and_audit(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    r = write(env, "output_priority", 1)
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "output_priority", "old": 2, "new": 1}
    assert env.inverter.bus.writes == [(0xE204, 1)]
    ev = env.store.events(source="inverter")[0]
    assert (
        ev["type"] == "inverter_setting" and "output_priority: 2 -> 1" in ev["detail"] and "testclient" in ev["detail"]
    )
    env.store.close()


def test_write_rejections_send_nothing(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    assert write(env, "output_priority", 1, pin=None).status_code == 401
    assert write(env, "output_priority", 1, pin="0000").status_code == 401
    assert write(env, "output_priority", 7).status_code == 400
    assert write(env, "max_charge_current", 42.5).status_code == 400  # step 1 A
    assert write(env, "system_voltage", 48).status_code == 400  # read-only
    assert write(env, "nope", 1).status_code == 404
    assert env.inverter.bus.writes == []
    env.store.close()


def test_soc_settings_use_their_own_registers(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    r = write(env, "discharge_cutoff_soc", 20)
    assert r.status_code == 200, r.text
    assert env.inverter.bus.regs[0xE00F] == 20  # whole register, as manual [59] / protocol V1.7+
    assert write(env, "charge_cutoff_soc", 95).status_code == 200
    assert env.inverter.bus.regs[0xE01D] == 95
    # switch-back SOC must stay above the switch-to-mains SOC
    r = write(env, "battery_return_soc", 10)
    assert r.status_code == 400 and "[61]" in r.json()["error"] and "[62]" in r.json()["error"]
    env.store.close()


def test_old_packed_soc_layout_blocks_soc_writes(tmp_path):
    env = make_env(tmp_path)
    env.inverter.bus.regs[0xE00F] = (100 << 8) | 15  # register sheet V1.3: charge | discharge cut-off packed
    connected(env)
    r = write(env, "discharge_cutoff_soc", 20)
    assert r.status_code == 409 and "keypad" in r.json()["error"]
    assert env.inverter.bus.regs[0xE00F] == (100 << 8) | 15
    items = {i["key"]: i for g in env.client.get("/api/inverter/settings").json()["groups"] for i in g["settings"]}
    assert items["mains_switch_soc"]["writable"] is False and items["mains_switch_soc"]["unavailable"]
    assert items["output_priority"]["writable"] is True and "unavailable" not in items["output_priority"]
    env.store.close()


def test_settings_map_one_to_one_to_keypad_numbers(tmp_path):
    numbers = [s.keypad for s in hf.SETTINGS if s.keypad is not None]
    assert len(numbers) == len(set(numbers))  # no keypad number used twice
    keypad_only = [n for n, _, _ in hf.PROFILE.keypad_only]
    assert not set(numbers) & set(keypad_only)
    # every setup item in the manual (section 4.2) is either mapped or listed as keypad-only
    manual = set(range(1, 30)) | {32, 33, 34, 35, 37, 38, 40} | set(range(57, 63))
    assert set(numbers) | set(keypad_only) == manual
    assert all(s.description for s in hf.SETTINGS)
    env = make_env(tmp_path)
    connected(env)
    body = env.client.get("/api/inverter/settings").json()
    items = {i["key"]: i for g in body["groups"] for i in g["settings"]}
    assert items["mains_switch_voltage"]["keypad"] == 4 and items["over_voltage"]["keypad"] is None
    assert {k["keypad"] for k in body["keypad_only"]} == set(keypad_only)
    env.store.close()


def test_battery_voltage_guardrails(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    # BMS limits unknown -> refuse
    r = write(env, "charge_limit_voltage", 28.6, confirm=True)
    assert r.status_code == 400 and "BMS" in r.json()["error"]
    bms_8s(env)
    # danger needs confirm
    assert write(env, "charge_limit_voltage", 28.6).status_code == 428
    # 29.0 V / 8 = 3.625 V/cell > 3.65 - 0.05
    r = write(env, "charge_limit_voltage", 29.0, confirm=True)
    assert r.status_code == 400 and "over-voltage" in r.json()["error"]
    # below the boost voltage (28.4) breaks the ordering float <= boost <= limit
    r = write(env, "charge_limit_voltage", 28.2, confirm=True)
    assert r.status_code == 400 and "≤" in r.json()["error"]
    # 22.4 V / 8 = 2.8 V/cell < 2.8 + 0.05
    r = write(env, "discharge_limit_voltage", 22.4, confirm=True)
    assert r.status_code == 400 and "under-voltage" in r.json()["error"]
    assert env.inverter.bus.writes == []
    # a valid change, in real volts on a 24 V system
    r = write(env, "charge_limit_voltage", 28.6, confirm=True)
    assert r.status_code == 200, r.text
    assert r.json()["new"] == 28.6 and env.inverter.bus.writes == [(0xE006, 143)]
    env.store.close()


def test_readback_mismatch_is_an_error(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bus = env.inverter.bus
    real_write = bus.write_register

    async def ignoring_write(address, raw, function=16):
        await real_write(address, raw, function)
        bus.regs[address] = 0  # the inverter "didn't take it"

    bus.write_register = ignoring_write
    r = write(env, "output_priority", 1)
    assert r.status_code == 502 and "mismatch" in r.json()["error"]
    assert env.store.events(source="inverter")[0]["type"] == "inverter_setting_failed"
    env.store.close()


def test_read_only_without_pin(tmp_path):
    env = make_env(tmp_path, pin="")
    connected(env)
    body = env.client.get("/api/inverter/settings").json()
    assert body["writable"] is False
    assert all(not s["writable"] for g in body["groups"] for s in g["settings"])
    assert write(env, "output_priority", 1).status_code == 403
    env.store.close()


def test_settings_schema_and_overview(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bms_8s(env)
    body = env.client.get("/api/inverter/settings").json()
    assert body["writable"] and body["connected"] and body["system_voltage"] == 24
    assert body["bms_limits"] == {"cell_count": 8, "cell_ovp": 3.65, "cell_uvp": 2.8}
    groups = {g["name"]: {s["key"]: s for s in g["settings"]} for g in body["groups"]}
    assert groups["Output & priorities"]["output_priority"]["options"][2]["label"].startswith("Solar → battery")
    assert groups["Battery voltages"]["charge_limit_voltage"]["value"] == 28.8
    cur = env.client.get("/api/inverter/current").json()
    assert cur["link"] == "connected" and cur["pv_power"] is not None
    ov = env.client.get("/api/overview").json()
    assert ov["soc_source"] == "inverter"  # BMS has no fresh cells in this test
    assert ov["inverter"]["link"] == "connected" and "load_power" in ov
    assert ov["priorities"]["output_priority"] == {"value": 2, "label": "Solar → battery → mains (SBU)"}
    assert ov["priorities"]["charge_priority"]["value"] == 0
    hist = env.client.get("/api/inverter/history").json()
    assert hist["fields"] == ["pv_power", "load_power", "battery_power", "soc"]
    env.store.close()


def test_runs():
    assert runs([5, 1, 2, 3, 7, 8]) == [Block(1, 3), Block(5, 1), Block(7, 2)]
    assert runs(list(range(40)), max_count=32) == [Block(0, 32), Block(32, 8)]


def test_bus_translates_srne_exception_codes():
    """SRNE reuses Modbus exception codes with its own meanings (10 = length error, 11 = permission denied)."""
    import minimalmodbus

    bus = ModbusBus("/dev/null")

    class Inst:
        def __init__(self, exc):
            self.exc = exc

        def read_registers(self, *a):
            raise self.exc

    for exc, expected, code, text in (
        (minimalmodbus.SlaveReportedException("Slave reported gateway path unavailable"), IllegalAddress, 10, "length"),
        (minimalmodbus.SlaveReportedException("Slave reported gateway target device failed to respond"),
         IllegalAddress, 11, "permission denied"),
        (minimalmodbus.IllegalRequestError("Slave reported illegal data address"), IllegalAddress, 2, "address"),
        (minimalmodbus.SlaveReportedException("Slave reported error code 9"), IllegalAddress, 9, "password"),
        (minimalmodbus.SlaveDeviceBusyError("Slave reported device busy"), NoResponse, None, "frame"),
        (minimalmodbus.NoResponseError("No communication"), NoResponse, None, ""),
    ):  # fmt: skip
        bus._instrument = Inst(exc)
        with pytest.raises(expected) as e:
            bus._call("read_registers", 0x0200, 37, 3)
        assert getattr(e.value, "code", None) == code and text in str(e.value)


def test_driver_recovers_from_gateway_rejected_blocks(tmp_path):
    """A unit that rejects whole blocks still connects and returns every readable register."""
    env = make_env(tmp_path)
    bus = env.inverter.bus
    bus.illegal |= {
        0x0103,
        0x0104,
        0x0105,
        0x0106,
        *range(0x000C, 0x0014),
    }  # like the real HF2430: no DC load, no model
    connected(env)
    live = env.inverter.state.live
    assert env.inverter.state.link == LINK_CONNECTED and live["battery_voltage"] and live["pv_power"] is not None
    assert env.inverter.state.device["model"] in (None, "")
    env.store.close()


def test_driver_skips_registers_that_time_out(tmp_path):
    """Unimplemented registers that time out (instead of being rejected) are skipped, not fatal."""
    env = make_env(tmp_path)
    env.inverter.bus.silent |= {0xE013, 0xE014}  # two silent registers inside the first settings block
    connected(env)
    assert env.inverter.state.link == LINK_CONNECTED
    assert env.inverter.settings_snapshot()["output_priority"] == 2
    assert env.inverter.settings_snapshot()["boost_charge_time"] == 120  # E012, next to the silent ones
    env.store.close()


def test_mains_battery_switching_thresholds(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bms_8s(env)
    snap = env.inverter.settings_snapshot()
    assert (snap["mains_switch_voltage"], snap["battery_return_voltage"]) == (24.0, 26.4)
    # switching back must stay above the switch-to-mains point, or the inverter would flap
    r = write(env, "battery_return_voltage", 24.0)
    assert r.status_code == 400 and "Battery → mains voltage [04]" in r.json()["error"]
    # ... and at or below the charge limit, or it would never return to battery
    assert write(env, "battery_return_voltage", 29.0).status_code == 400
    # not a protection voltage: no typed confirmation needed
    r = write(env, "battery_return_voltage", 26.8)
    assert r.status_code == 200, r.text
    assert r.json()["new"] == 26.8 and env.inverter.bus.regs[0xE022] == 134
    env.store.close()


def test_permission_denied_points_to_the_keypad(tmp_path):
    from ray_vault.inverter.bus import Refused

    env = make_env(tmp_path)
    connected(env)
    bms_8s(env)
    bus = env.inverter.bus

    async def refuse(address, raw, function=16):
        raise Refused(11, "Slave reported gateway target device failed to respond")

    bus.write_register = refuse
    r = write(env, "battery_return_voltage", 26.8)
    err = r.json()["error"]
    assert r.status_code == 502
    assert "permission denied (code 11)" in err and "no password entered" in err and "[05]" in err
    env.store.close()


def test_hf_writes_use_function_16_and_fall_back_to_6(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bus = env.inverter.bus
    assert write(env, "output_priority", 1).status_code == 200
    assert bus.functions == [16]  # the HF protocol documents only 03H and 10H
    bus.write_functions = {6}  # a firmware that only knows write-single
    assert write(env, "output_priority", 0).status_code == 200
    assert bus.functions == [16, 6] and bus.regs[0xE204] == 0
    assert write(env, "output_priority", 2).status_code == 200
    assert bus.functions == [16, 6, 6]  # remembered
    env.store.close()


def test_unlock_test_finds_a_working_password(tmp_path):
    from ray_vault.inverter.fake import FakeBus
    from ray_vault.inverter.unlock import unlock_test

    bus = FakeBus(seed=1)
    bus.locked, bus.password = {0xE022}, 4321
    lines: list[str] = []
    assert asyncio.run(unlock_test(bus, hf, 1111, 0xE022, out=lines.append)) == 1
    assert any("REFUSED" in ln and "code 5" in ln for ln in lines)
    lines.clear()
    assert asyncio.run(unlock_test(bus, hf, 4321, 0xE022, out=lines.append)) == 0
    assert "user password entered" in "\n".join(lines)
    assert 0xE202 not in [a for a, _ in bus.writes]  # never sets a keypad password
    assert bus.regs[0xE022] == 132  # rewritten with its own value


def test_configured_password_is_entered_before_writes(tmp_path):
    env = make_env(tmp_path)
    connected(env)
    bms_8s(env)
    bus = env.inverter.bus
    bus.locked, bus.password = {0xE022}, 4321
    r = write(env, "battery_return_voltage", 26.8)
    assert r.status_code == 502 and "inverter-unlock-test" in r.json()["error"]
    env.inverter.password = 1111
    r = write(env, "battery_return_voltage", 26.8)
    assert r.status_code == 502 and "password in the config is wrong" in r.json()["error"]
    env.inverter.password = 4321
    r = write(env, "battery_return_voltage", 26.8)
    assert r.status_code == 200, r.text
    assert bus.writes[-2:] == [(0xE203, 4321), (0xE022, 134)]
    assert env.inverter.state.password_level == 1
    env.store.close()
