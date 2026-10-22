"""SRNE HF-series all-in-one inverter/charger (HF2430S80-H, HF2430U80-H, …).

Source: docs/inverter/Solar_inverter_charger_register_definition.pdf ("Modbus register address of integrated
inverter controller V1.3"). Other SRNE families reuse addresses with different meanings, so don't use this
profile for hybrid models without checking `python -m ray_vault probe-inverter` output first.
"""

from __future__ import annotations

from ..registers import Block, Profile, Reading, SettingSpec

BATTERY_VOLTAGES = "Battery voltages"

READINGS = (
    Reading("soc", 0x0100, unit="%", label="Battery SOC"),
    Reading("battery_voltage", 0x0101, 0.1, unit="V", label="Battery voltage"),
    # The inverter reports + while discharging; negate so + means charging, like the BMS.
    Reading("battery_current", 0x0102, 0.1, signed=True, negate=True, unit="A", label="Battery current"),
    Reading("pv_voltage", 0x0107, 0.1, unit="V", label="PV voltage"),
    Reading("pv_current", 0x0108, 0.1, unit="A", label="PV current"),
    Reading("pv_power", 0x0109, unit="W", label="PV power"),
    Reading("machine_state", 0x0210, label="State"),
    Reading("bus_voltage", 0x0212, 0.1, unit="V", label="DC bus voltage"),
    Reading("grid_voltage", 0x0213, 0.1, unit="V", label="Grid voltage"),
    Reading("grid_current", 0x0214, 0.1, unit="A", label="Grid current"),
    Reading("grid_frequency", 0x0215, 0.01, unit="Hz", label="Grid frequency"),
    Reading("output_voltage", 0x0216, 0.1, unit="V", label="Output voltage"),
    Reading("output_current", 0x0217, 0.1, unit="A", label="Inverter current"),
    Reading("output_frequency", 0x0218, 0.01, unit="Hz", label="Output frequency"),
    Reading("load_current", 0x0219, 0.1, unit="A", label="Load current"),
    Reading("load_power", 0x021B, unit="W", label="Load power"),
    Reading("load_apparent_power", 0x021C, unit="VA", label="Load apparent power"),
    Reading("grid_charge_current", 0x021E, 0.1, unit="A", label="Mains charge current"),
    Reading("load_percent", 0x021F, unit="%", label="Load"),
    Reading("temp_dcdc", 0x0220, 0.1, signed=True, unit="°C", label="DC-DC heat sink"),
    Reading("temp_dcac", 0x0221, 0.1, signed=True, unit="°C", label="DC-AC heat sink"),
    Reading("temp_transformer", 0x0222, 0.1, signed=True, unit="°C", label="Heat sink C"),
    Reading("temp_ambient", 0x0223, 0.1, signed=True, unit="°C", label="Ambient"),
    Reading("pv_charge_current", 0x0224, 0.1, unit="A", label="PV charge current"),
)

MACHINE_STATES = {
    0: "Power-up delay",
    1: "Waiting",
    2: "Initialising",
    3: "Soft start",
    4: "On mains",
    5: "On inverter",
    6: "Inverter → mains",
    7: "Mains → inverter",
    10: "Shut down",
    11: "Fault",
}

FAULT_BITS = range(0x0200, 0x0204)
FAULT_CODES = range(0x0204, 0x0208)

SETTINGS = (
    # --- priorities ---
    SettingSpec(
        "output_priority", "Output priority", "Priorities", 0xE204, kind="enum",
        options=((0, "Solar first (SOL)"), (1, "Mains first (UTI)"), (2, "Solar → battery → mains (SBU)")),
        description="Which source powers the load first.",
    ),
    SettingSpec(
        "charge_priority", "Charger priority", "Priorities", 0xE20F, kind="enum",
        options=(
            (0, "PV preferred (CSO)"),
            (1, "Mains preferred (CUB)"),
            (2, "PV + mains, PV first (SNU)"),
            (3, "PV only (OSO)"),
        ),
        description="Which source charges the battery.",
    ),
    # --- charge limits ---
    SettingSpec(
        "max_charge_current", "Max charge current (total)", "Charge limits", 0xE20A, scale=0.1, unit="A",
        min=0, max=80, step=5, description="Combined PV + mains charge current limit.",
    ),
    SettingSpec(
        "grid_charge_current", "Max mains charge current", "Charge limits", 0xE205, scale=0.1, unit="A",
        min=0, max=80, step=5,
    ),
    SettingSpec(
        "pv_charge_current", "Max PV charge current", "Charge limits", 0xE001, unit="A", min=0, max=80, step=1,
    ),
    SettingSpec(
        "stop_charge_current", "Stop-charging current", "Charge limits", 0xE01C, scale=0.1, unit="A",
        min=0, max=40, step=0.1,
        description="Lithium only: in constant-voltage charging, stop when the current falls below this.",
    ),
    SettingSpec(
        "boost_charge_time", "Boost charge time", "Charge limits", 0xE012, unit="min", min=10, max=600, step=10,
    ),
    # --- SOC cut-offs (two bytes of E00F) ---
    SettingSpec(
        "charge_cutoff_soc", "Charge cut-off SOC", "SOC limits", 0xE00F, byte="hi", unit="%", min=0, max=100,
        description="Stop charging at this SOC. Uses the SOC the inverter knows (BMS link or its own estimate).",
    ),
    SettingSpec(
        "discharge_cutoff_soc", "Discharge cut-off SOC", "SOC limits", 0xE00F, byte="lo", unit="%", min=0, max=100,
        description="Stop discharging at this SOC.",
    ),
    # --- battery voltages (12 V-normalised) ---
    *(
        SettingSpec(
            key, label, BATTERY_VOLTAGES, addr, scale=0.1, unit="V", min=9.0, max=15.5, step=0.1,
            battery_scaled=True, danger=True, charge_side=charge, discharge_side=not charge, description=desc,
        )
        for key, label, addr, charge, desc in (
            ("over_voltage", "Over-voltage protection", 0xE005, True, "Fast overcharge protection."),
            ("charge_limit_voltage", "Charge limit voltage", 0xE006, True, "Overcharge protection voltage."),
            ("equalize_voltage", "Equalizing voltage", 0xE007, True, ""),
            ("boost_voltage", "Boost / overcharge voltage", 0xE008, True, "Lithium: overcharge voltage."),
            ("float_voltage", "Float / overcharge return", 0xE009, True, "Lithium: charging restarts below this."),
            ("boost_return_voltage", "Boost return voltage", 0xE00A, True, ""),
            ("over_discharge_return_voltage", "Over-discharge return", 0xE00B, False,
             "Discharging resumes above this after an over-discharge cut-off."),
            ("under_voltage_warning", "Low-voltage warning", 0xE00C, False, "Alarm only; the load stays on."),
            ("over_discharge_voltage", "Over-discharge voltage", 0xE00D, False, "Alarm and load cut-off."),
            ("discharge_limit_voltage", "Discharge limit voltage", 0xE00E, False, "Load is cut off immediately."),
            ("mains_switch_voltage", "Switch to mains below", 0xE01B, False,
             "In SBU mode, the load moves to mains when the battery drops below this."),
        )
    ),
    # --- read-only context ---
    SettingSpec("system_voltage", "System voltage", "Battery", 0xE003, unit="V", writable=False),
    SettingSpec("battery_type", "Battery type (raw)", "Battery", 0xE004, writable=False),
    SettingSpec("battery_capacity", "Nominal capacity", "Battery", 0xE002, unit="Ah", writable=False),
)  # fmt: skip

PROFILE = Profile(
    name="hf",
    title="SRNE HF series (HF2430)",
    live_blocks=(Block(0x0100, 0x10), Block(0x0200, 0x25)),
    settings_blocks=(Block(0xE000, 0x1D), Block(0xE200, 0x10)),
    info_blocks=(Block(0x000A, 0x17), Block(0x0035, 0x14)),
    readings=READINGS,
    settings=SETTINGS,
    machine_states=MACHINE_STATES,
    system_voltage_register=0xE003,
    ordering=(
        ("float_voltage", "<=", "boost_voltage"),
        ("boost_voltage", "<=", "charge_limit_voltage"),
        ("charge_limit_voltage", "<=", "over_voltage"),
        ("discharge_limit_voltage", "<=", "over_discharge_voltage"),
        ("over_discharge_voltage", "<", "over_discharge_return_voltage"),
    ),
)


def _ascii(regs: dict[int, int], start: int, count: int, low_byte_only: bool) -> str:
    out = bytearray()
    for a in range(start, start + count):
        v = regs.get(a)
        if v is None:
            continue
        if low_byte_only:
            out.append(v & 0xFF)
        else:
            out += bytes([(v >> 8) & 0xFF, v & 0xFF])
    return out.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _version(v: int | None) -> str | None:
    return None if v is None else f"{v // 100}.{v % 100:02d}"


def decode_info(regs: dict[int, int]) -> dict:
    """Device identity from the info blocks."""
    return {
        "product_type": regs.get(0x000B),
        "model": _ascii(regs, 0x000C, 8, low_byte_only=False) or None,
        "software_version": _version(regs.get(0x0014)),
        "software_version_2": _version(regs.get(0x0015)),
        "hardware_version": _version(regs.get(0x0016)),
        "power_board_version": _version(regs.get(0x0017)),
        "rs485_address": regs.get(0x001A),
        "model_code": regs.get(0x001B),
        "serial_number": _ascii(regs, 0x0035, 0x14, low_byte_only=True) or None,
    }


def decode_faults(regs: dict[int, int]) -> tuple[str | None, list[int]]:
    """(hex of the 4 fault-bit registers, list of active fault codes)."""
    bits = [regs.get(a) for a in FAULT_BITS]
    hexbits = None if any(b is None for b in bits) else "".join(f"{b:04x}" for b in bits)
    codes = [c for a in FAULT_CODES if (c := regs.get(a))]
    return hexbits, codes
