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

from .bus import Bus, BusError, IllegalAddress
from .driver import infer_system_voltage

SCAN_RANGES = ((0xE000, 0xE040), (0xE200, 0xE230))


async def _read_each(bus: Bus, addresses) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    for a in addresses:
        try:
            out[a] = await bus.read_register(a)
        except IllegalAddress:
            out[a] = None
    return out


async def probe(bus: Bus, profile: ModuleType, out=print) -> int:
    prof = profile.PROFILE
    try:
        info_regs = {}
        for b in prof.info_blocks:
            info_regs.update({a: v for a, v in (await _read_each(bus, b.addresses)).items() if v is not None})
    except BusError as e:
        out(f"error: cannot talk to the inverter: {e}")
        return 1

    out(f"== Identity (profile: {prof.title})")
    for k, v in profile.decode_info(info_regs).items():
        out(f"  {k:22} {v}")

    live_regs: dict[int, int] = {}
    for b in prof.live_blocks:
        live_regs.update({a: v for a, v in (await _read_each(bus, b.addresses)).items() if v is not None})
    out("\n== Live values")
    for r in prof.readings:
        raw = live_regs.get(r.address)
        val = "-" if raw is None else f"{r.decode(raw)} {r.unit}".strip()
        out(f"  0x{r.address:04X}  {r.label:24} {val:>14}   raw={raw}")
    hexbits, codes = profile.decode_faults(live_regs)
    out(f"  faults: bits={hexbits} codes={codes or 'none'}")

    settings: dict[int, int | None] = {}
    for lo, hi in SCAN_RANGES:
        settings.update(await _read_each(bus, range(lo, hi)))
    sv = infer_system_voltage(settings.get(prof.system_voltage_register), (live_regs.get(0x0101) or 0) / 10)
    out(f"\n== Settings (system voltage used for scaling: {sv} V)")
    known = {s.address: [] for s in prof.settings}
    for s in prof.settings:
        known[s.address].append(s)
    for a, raw in settings.items():
        if a in known:
            for s in known[a]:
                if raw is None:
                    val = "- (rejected)"
                else:
                    v = s.from_raw(raw)
                    if s.kind == "enum":
                        val = f"{v} = {dict(s.options).get(v, '?')}"
                    elif s.battery_scaled and sv:
                        val = f"{s.to_display(v, sv)} {s.unit} ({v} @12V)"
                    else:
                        val = f"{v} {s.unit}".strip()
                out(f"  0x{a:04X}  {s.label:30} {val}   raw={raw}")
        elif raw is not None:
            out(f"  0x{a:04X}  {'(not in profile)':30} raw={raw}")
    return 0
