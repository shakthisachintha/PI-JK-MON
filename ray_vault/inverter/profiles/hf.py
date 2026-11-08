"""SRNE HF-series all-in-one inverter/charger (HF2430S80-H, HF2430U80-H, …).

Sources: docs/inverter/Solar_inverter_charger_register_definition.pdf ("Modbus register address of integrated
inverter controller V1.3"), the newer protocol V1.96 for the SOC registers, and the HF user manual for the
keypad numbers, descriptions and ranges. Other SRNE families reuse addresses with different meanings, so don't use this
profile for hybrid models without checking `python -m ray_vault probe-inverter` output first.
"""

from __future__ import annotations

from ..registers import Block, Profile, Reading, SettingSpec

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

# Settings, in the order of the inverter's setup menu. `keypad` is the parameter number shown on the inverter's
# display ([01], [04] …) and in the user manual (docs/inverter/HF series…manual_1.1.pdf, section 4.2); the
# descriptions and ranges come from that manual. Settings without a keypad number exist only over Modbus.
# Ranges of battery voltages are 12 V-normalised (x2 on this 24 V system): e.g. 10.0–13.0 here is 20–26 V.

PRIORITIES = "Output & priorities"
CHARGING = "Charging"
BATTERY = "Battery"
BATTERY_VOLTAGES = "Battery voltages"
SWITCHING = "Mains ↔ battery switching (SBU)"
SOC_LIMITS = "SOC limits (needs BMS communication)"
EQUALIZATION = "Equalization (lead-acid only)"
PROTECTION = "Protection & alarms"

ON_OFF = ((0, "Disabled (DIS)"), (1, "Enabled (ENA)"))

SETTINGS = (
    # --- output & priorities ---
    SettingSpec(
        "output_priority", "Output source priority", PRIORITIES, 0xE204, kind="enum", keypad=1,
        options=((0, "Solar first (SOL)"), (1, "Mains first (UTI)"), (2, "Solar → battery → mains (SBU)")),
        description="SOL: PV first; switches to mains when PV fails or the battery drops below [04]. "
                    "UTI: mains first; the inverter runs only when the mains fails. "
                    "SBU: inverter first; switches to mains only on battery under-voltage or below [04].",
    ),
    SettingSpec(
        "output_frequency", "Output frequency", PRIORITIES, 0xE209, scale=0.01, unit="Hz", writable=False, keypad=2,
        description="Follows the mains frequency automatically while mains is connected; this is the frequency "
                    "used without mains. Change it on the keypad.",
    ),
    SettingSpec(
        "ac_input_range", "AC input voltage range", PRIORITIES, 0xE20B, kind="enum", keypad=3,
        options=((0, "Wide (APL)"), (1, "Narrow (UPS)")),
        description="APL accepts mains from 90–280 V (230 V model); UPS accepts 170–280 V and switches to "
                    "battery sooner on bad mains, which suits sensitive loads.",
    ),
    SettingSpec(
        "charge_priority", "Charger source priority", PRIORITIES, 0xE20F, kind="enum", keypad=6,
        options=(
            (0, "PV first (CSO)"),
            (1, "Mains first (CUB)"),
            (2, "PV + mains, PV first (SNU)"),
            (3, "PV only (OSO)"),
        ),
        description="CSO: mains charges only when PV charging fails. CUB: PV charges only when mains charging "
                    "fails. SNU: PV first, mains tops up when PV is short (both only while the load runs on mains "
                    "bypass). OSO: PV only, mains never charges.",
    ),
    SettingSpec(
        "output_voltage", "AC output voltage", PRIORITIES, 0xE208, scale=0.1, unit="V", writable=False, keypad=38,
        description="Rated output power scales with it. Can only be changed on the keypad, in standby.",
    ),
    # --- charging ---
    SettingSpec(
        "max_charge_current", "Max charge current (PV + mains)", CHARGING, 0xE20A, scale=0.1, unit="A",
        min=0, max=80, step=1, keypad=7,
        description="Combined PV + mains charge current limit.",
    ),
    SettingSpec(
        "grid_charge_current", "Max mains (AC) charge current", CHARGING, 0xE205, scale=0.1, unit="A",
        min=0, max=80, step=1, keypad=28,
        description="Mains charger limit. S models allow 0–80 A, U models 0–40 A.",
    ),
    SettingSpec(
        "pv_charge_current", "Max PV charge current", CHARGING, 0xE001, unit="A", min=0, max=80, step=1,
        description="PV charger limit. Not in the keypad menu.",
    ),
    SettingSpec(
        "boost_charge_time", "Boost charge time", CHARGING, 0xE012, unit="min", min=5, max=900, step=5, keypad=10,
        description="Longest time the charger holds the boost voltage [09] in constant-voltage charging. "
                    "User-defined and lithium batteries.",
    ),
    SettingSpec(
        "stop_charge_current", "Stop charging current", CHARGING, 0xE01C, scale=0.1, unit="A",
        min=0, max=10, step=0.1, keypad=57,
        description="Lithium: charging stops when the current in constant-voltage charging falls below this.",
    ),
    # --- battery (read-only context) ---
    SettingSpec(
        "battery_type", "Battery type", BATTERY, 0xE004, kind="enum", writable=False, keypad=8,
        options=(
            (0, "User defined (USE)"), (1, "Sealed lead-acid (SLd)"), (2, "Flooded lead-acid (FLd)"),
            (3, "Gel (GEL)"), (4, "LFP 14S"), (5, "LFP 15S"), (6, "LFP 16S"), (7, "LFP 7S (LF07)"),
            (8, "LFP 8S (LF08)"), (9, "LFP 9S (LF09)"), (10, "NCA 7S"), (11, "NCA 8S"), (12, "NCA 13S"),
            (13, "NCA 14S"),
        ),
        description="USE: all battery parameters can be set. Lead-acid presets fix their voltages; lithium "
                    "presets (LF07/08/09, NCA) start from defaults that stay adjustable. Change it on the keypad: "
                    "switching type loads that type's default voltages.",
    ),
    SettingSpec("system_voltage", "System voltage", BATTERY, 0xE003, unit="V", writable=False,
                description="Battery bank voltage; battery voltages here are scaled to it."),
    SettingSpec("battery_capacity", "Nominal capacity", BATTERY, 0xE002, unit="Ah", writable=False,
                description="Not in the keypad menu."),
    # --- battery voltages (12 V-normalised) ---
    *(
        SettingSpec(
            key, label, BATTERY_VOLTAGES, addr, scale=0.1, unit="V", min=lo, max=hi, step=0.1,
            battery_scaled=True, danger=True, charge_side=charge, discharge_side=not charge, keypad=num,
            description=desc,
        )
        for key, label, num, addr, lo, hi, charge, desc in (
            ("over_voltage", "Over-voltage disconnect", None, 0xE005, 9.0, 15.5, True,
             "Fast over-charge protection: charging is cut above this. Not in the keypad menu."),
            ("charge_limit_voltage", "Charge limit voltage", None, 0xE006, 9.0, 15.5, True,
             "Over-charge protection voltage. Not in the keypad menu."),
            ("boost_voltage", "Boost charge voltage", 9, 0xE008, 12.0, 14.6, True,
             "Constant-voltage (absorption) charge voltage; for lithium, the over-charge voltage. "
             "User-defined and lithium batteries."),
            ("float_voltage", "Floating charge voltage", 11, 0xE009, 12.0, 14.6, True,
             "Float voltage after the boost stage; for lithium, charging restarts below it. User-defined battery."),
            ("boost_return_voltage", "Fully charged recovery point", 37, 0xE00A, 9.0, 15.5, True,
             "After a full charge, the battery must fall below this before charging starts again."),
            ("over_discharge_voltage", "Over-discharge voltage (delayed off)", 12, 0xE00D, 10.0, 12.0, False,
             "Below this, the inverter output turns off after the delay [13]. "
             "User-defined and lithium batteries."),
            ("over_discharge_return_voltage", "Under-voltage recovery point", 35, 0xE00B, 9.0, 15.5, False,
             "After an under-voltage cut-off, the battery must recover above this before the output restarts."),
            ("under_voltage_warning", "Under-voltage alarm", 14, 0xE00C, 10.0, 13.0, False,
             "Alarm below this; the output stays on. User-defined and lithium batteries."),
            ("discharge_limit_voltage", "Discharge limit voltage", 15, 0xE00E, 10.0, 13.0, False,
             "Below this, the output turns off immediately. User-defined and lithium batteries."),
        )
    ),
    SettingSpec(
        "over_discharge_delay", "Over-discharge delay", BATTERY_VOLTAGES, 0xE010, unit="s", min=5, max=55, step=5,
        keypad=13,
        description="How long the battery may stay below [12] before the output turns off.",
    ),
    # --- SBU switching between mains and battery ---
    SettingSpec(
        "mains_switch_voltage", "Battery → mains voltage", SWITCHING, 0xE01B, scale=0.1, unit="V",
        min=10.0, max=13.0, step=0.1, battery_scaled=True, danger=True, discharge_side=True, keypad=4,
        description="In SOL/SBU, the output moves from the inverter to mains when the battery falls below this. "
                    "Can't exceed the under-voltage alarm [14].",
    ),
    SettingSpec(
        "battery_return_voltage", "Mains → battery voltage", SWITCHING, 0xE022, scale=0.1, unit="V",
        min=12.0, max=15.0, step=0.1, battery_scaled=True, keypad=5,
        description="In SOL/SBU, the output moves from mains back to the inverter when the battery rises above "
                    "this. Can't be lower than [04] or [35].",
    ),
    SettingSpec(
        "mains_switch_soc", "Battery → mains SOC", SWITCHING, 0xE01F, unit="%", min=0, max=100, keypad=61,
        description="Switch to mains when the SOC falls below this. Needs BMS communication.",
    ),
    SettingSpec(
        "battery_return_soc", "Mains → battery SOC", SWITCHING, 0xE020, unit="%", min=1, max=100, keypad=62,
        description="Switch back to inverter output when the SOC reaches this. Needs BMS communication.",
    ),
    # --- SOC limits (BMS communication) ---
    SettingSpec(
        "low_soc_alarm", "Discharge alarm SOC", SOC_LIMITS, 0xE01E, unit="%", min=0, max=100, keypad=58,
        description="Alarm when the SOC falls below this.",
    ),
    SettingSpec(
        "discharge_cutoff_soc", "Cut-off discharge SOC", SOC_LIMITS, 0xE00F, unit="%", min=0, max=100, keypad=59,
        description="Stop discharging when the SOC falls below this.",
    ),
    SettingSpec(
        "charge_cutoff_soc", "Cut-off charge SOC", SOC_LIMITS, 0xE01D, unit="%", min=0, max=100, keypad=60,
        description="Stop charging when the SOC reaches this.",
    ),
    # --- equalization (vented and sealed lead-acid only) ---
    SettingSpec(
        "equalize_enable", "Equalization", EQUALIZATION, 0xE206, kind="enum", options=ON_OFF, danger=True,
        keypad=16,
        description="Periodic over-charge to balance lead-acid cells. Only works for FLd/SLd batteries; "
                    "keep it disabled for lithium.",
    ),
    SettingSpec(
        "equalize_voltage", "Equalization voltage", EQUALIZATION, 0xE007, scale=0.1, unit="V", min=12.0, max=14.6,
        step=0.1, battery_scaled=True, danger=True, charge_side=True, keypad=17,
        description="Charge voltage during equalization.",
    ),
    SettingSpec("equalize_time", "Equalization time", EQUALIZATION, 0xE011, unit="min", min=5, max=900, step=5,
                keypad=18, description="How long equalization runs."),
    SettingSpec("equalize_timeout", "Equalization timeout", EQUALIZATION, 0xE023, unit="min", min=5, max=900,
                step=5, keypad=19, description="Longest time allowed to reach the equalization voltage."),
    SettingSpec("equalize_interval", "Equalization interval", EQUALIZATION, 0xE013, unit="days", min=0, max=30,
                keypad=20, description="Days between equalization charges."),
    # --- protection & alarms ---
    SettingSpec(
        "power_saving", "Power saving mode", PROTECTION, 0xE20C, kind="enum", options=ON_OFF, keypad=22,
        description="With no load (or under 50 W), the inverter output turns off after a delay and restarts "
                    "when the load goes above 50 W.",
    ),
    SettingSpec(
        "overload_restart", "Restart after overload", PROTECTION, 0xE20D, kind="enum", options=ON_OFF, keypad=23,
        description="After an overload shutdown, restart after 3 minutes (gives up after 5 times).",
    ),
    SettingSpec(
        "overtemp_restart", "Restart after over-temperature", PROTECTION, 0xE20E, kind="enum", options=ON_OFF,
        keypad=24, description="After an over-temperature shutdown, restart once it has cooled down.",
    ),
    SettingSpec("alarm_enable", "Alarm buzzer", PROTECTION, 0xE210, kind="enum", options=ON_OFF, keypad=25,
                description="Audible alarms."),
    SettingSpec(
        "source_change_beep", "Beep when the input source changes", PROTECTION, 0xE211, kind="enum",
        options=ON_OFF, keypad=26, description="Beep when the main input source is interrupted or comes back.",
    ),
    SettingSpec(
        "overload_bypass", "Switch to mains on overload", PROTECTION, 0xE212, kind="enum", options=ON_OFF,
        keypad=27, description="When the inverter is overloaded, move the load to mains automatically.",
    ),
)  # fmt: skip

KEYPAD_ONLY = (
    (21, "Equalize immediately", "Starts or stops an equalization charge now; no documented register."),
    (29, "Split phase", "Register E214 means something else on newer firmware, so it isn't read."),
    (32, "RS485-2 communication", "PC/monitoring (SLA) or BMS mode; no documented register."),
    (33, "BMS communication protocol", "Battery maker's BMS protocol; no documented register."),
    (34, "Hybrid output function", "No documented register."),
    (40, "Select upgrade target", "Firmware upgrade from a USB stick."),
)

# Two register layouts exist for the SOC settings. The HF register sheet (V1.3) packs charge/discharge cut-off SOC
# into E00F and uses E01D–E020 for solar-controller light control; newer firmware (protocol V1.7+, and this
# manual's [58]–[62] with the same defaults) has E00F = discharge cut-off and E01D–E020 = the other SOC points.
# A packed E00F reads above 100, so writes to the SOC settings are blocked on that older layout.
SOC_KEYS = ("mains_switch_soc", "battery_return_soc", "low_soc_alarm", "discharge_cutoff_soc", "charge_cutoff_soc")


def layout_check(regs: dict[int, int]) -> dict[str, str]:
    e00f = regs.get(0xE00F)
    if e00f is not None and e00f > 100:
        why = (
            f"This firmware uses the older register layout (E00F = 0x{e00f:04X}), so this setting's address "
            "doesn't match. Change it on the inverter's keypad."
        )
        return dict.fromkeys(SOC_KEYS, why)
    return {}


PROFILE = Profile(
    name="hf",
    title="SRNE HF series (HF2430)",
    live_blocks=(Block(0x0100, 0x10), Block(0x0200, 0x25)),
    settings_blocks=(Block(0xE000, 0x24), Block(0xE200, 0x13)),
    info_blocks=(Block(0x000A, 0x17), Block(0x0035, 0x14)),
    readings=READINGS,
    settings=SETTINGS,
    machine_states=MACHINE_STATES,
    system_voltage_register=0xE003,
    max_read=12,  # HF firmware answers longer reads with SRNE error 0x0A "length error"
    # The HF protocol (Solar_inverter_charger_communication_protocol.pdf) lists only 03H read and 10H write.
    # Single-register writes (06H) are refused with "permission denied" for the battery settings (E0xx).
    write_function=16,
    password_status_register=0x0211,
    password_input_register=0xE203,  # never write E202: that *sets* the keypad password
    ordering=(
        ("float_voltage", "<=", "boost_voltage"),
        ("boost_voltage", "<=", "charge_limit_voltage"),
        ("charge_limit_voltage", "<=", "over_voltage"),
        ("discharge_limit_voltage", "<=", "over_discharge_voltage"),
        ("over_discharge_voltage", "<", "over_discharge_return_voltage"),
        ("mains_switch_voltage", "<=", "under_voltage_warning"),  # manual: [04] can't exceed [14]
        ("mains_switch_voltage", "<", "battery_return_voltage"),  # [05] can't be lower than [04] …
        ("over_discharge_return_voltage", "<=", "battery_return_voltage"),  # … or [35]
        ("battery_return_voltage", "<=", "charge_limit_voltage"),
        ("mains_switch_soc", "<", "battery_return_soc"),
        ("discharge_cutoff_soc", "<", "charge_cutoff_soc"),
    ),
    keypad_only=KEYPAD_ONLY,
    layout_check=layout_check,
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
