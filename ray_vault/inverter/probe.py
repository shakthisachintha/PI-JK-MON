"""Read-only inverter probe: dump identity, live values and every setting register, decoded with the profile.

Run it on the Pi (stop the service first so the port is free) and compare with the inverter's display before
trusting writes:

    sudo systemctl stop ray-vault
    sudo -u rayvault /opt/ray-vault/venv/bin/python -m ray_vault probe-inverter
    sudo systemctl start ray-vault

It never writes. Registers the inverter rejects are listed as "-" so missing features are visible.
"""

from __future__ import annotations

from types import ModuleType

from .bus import Bus, BusError, IllegalAddress, NoResponse
from .driver import infer_system_voltage

SCAN_RANGES = ((0xE000, 0xE040), (0xE200, 0xE230))


REJECTED = "rejected"
TIMEOUT = "timeout"
MAX_CONSECUTIVE_TIMEOUTS = 5


async def _read_each(bus: Bus, addresses) -> dict[int, int | str]:
    """Read registers one by one. Values are ints, or REJECTED / TIMEOUT. Raises after repeated timeouts."""
    out: dict[int, int | str] = {}
    timeouts = 0
    for a in addresses:
        try:
            out[a] = await bus.read_register(a)
            timeouts = 0
        except IllegalAddress:
            out[a] = REJECTED
            timeouts = 0
        except NoResponse:
            out[a] = TIMEOUT
            timeouts += 1
            if timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                raise
    return out


def _ints(values: dict[int, int | str]) -> dict[int, int]:
    return {a: v for a, v in values.items() if isinstance(v, int)}


async def probe(bus: Bus, profile: ModuleType, out=print) -> int:
    prof = profile.PROFILE
    try:
        info_regs: dict[int, int] = {}
        for b in prof.info_blocks:
            info_regs.update(_ints(await _read_each(bus, b.addresses)))

        out(f"== Identity (profile: {prof.title})")
        for k, v in profile.decode_info(info_regs).items():
            out(f"  {k:22} {v}")

        live = await _read_each(bus, [a for b in prof.live_blocks for a in b.addresses])
        live_regs = _ints(live)
        out("\n== Live values")
        for r in prof.readings:
            raw = live.get(r.address)
            val = f"- ({raw})" if not isinstance(raw, int) else f"{r.decode(raw)} {r.unit}".strip()
            out(f"  0x{r.address:04X}  {r.label:24} {val:>14}   raw={raw}")
        hexbits, codes = profile.decode_faults(live_regs)
        out(f"  faults: bits={hexbits} codes={codes or 'none'}")
        skipped = sorted(a for a, v in live.items() if not isinstance(v, int))
        if skipped:
            out(f"  not readable on this unit: {', '.join(f'0x{a:04X}' for a in skipped)}")

        settings: dict[int, int | str] = {}
        for lo, hi in SCAN_RANGES:
            out(f"\n(scanning settings 0x{lo:04X}-0x{hi - 1:04X}, one register at a time…)")
            settings.update(await _read_each(bus, range(lo, hi)))
    except BusError as e:
        out(f"error: cannot talk to the inverter: {e}")
        return 1

    raw_sv = settings.get(prof.system_voltage_register)
    sv = infer_system_voltage(raw_sv if isinstance(raw_sv, int) else None, (live_regs.get(0x0101) or 0) / 10)
    out(f"\n== Settings (system voltage used for scaling: {sv} V)")
    known: dict[int, list] = {}
    for s in prof.settings:
        known.setdefault(s.address, []).append(s)
    missing = []
    for a, raw in settings.items():
        if a in known:
            for s in known[a]:
                if not isinstance(raw, int):
                    val = f"- ({raw})"
                    missing.append(s.label)
                else:
                    v = s.from_raw(raw)
                    if s.kind == "enum":
                        val = f"{v} = {dict(s.options).get(v, '?')}"
                    elif s.battery_scaled and sv:
                        val = f"{s.to_display(v, sv)} {s.unit} ({v} @12V)"
                    else:
                        val = f"{v} {s.unit}".strip()
                out(f"  0x{a:04X}  {s.label:30} {val}   raw={raw}")
        elif isinstance(raw, int):
            out(f"  0x{a:04X}  {'(not in profile)':30} raw={raw}")
    if missing:
        out(f"\nProfile settings this unit doesn't answer (they will show as empty): {', '.join(missing)}")
    return 0
