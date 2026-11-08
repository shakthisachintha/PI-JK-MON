"""Find out whether entering a password over Modbus lets the inverter accept battery-setting writes.

SRNE inverters have privilege levels (register 0x0211: 0 = no password entered, 1 = user, 4 = manufacturer) and a
write-only "password input" register (E203). Which level a setting needs isn't documented, so this tests it:

    sudo systemctl stop ray-vault
    sudo -u rayvault /opt/ray-vault/venv/bin/python -m ray_vault inverter-unlock-test --password 1234
    sudo systemctl start ray-vault

What it writes:
  * the test register's *current* value back to itself (nothing on the inverter changes), before and after
  * the password into E203 (password input). It never writes E202, which would set a keypad password.
"""

from __future__ import annotations

from types import ModuleType

from .bus import Bus, BusError

LEVELS = {0: "no password entered", 1: "user password entered", 4: "manufacturer password entered"}


def level_text(level: int | None) -> str:
    return "unknown" if level is None else LEVELS.get(level, f"level {level}")


async def _level(bus: Bus, register: int | None) -> int | None:
    if register is None:
        return None
    try:
        return await bus.read_register(register)
    except BusError:
        return None


async def _rewrite(bus: Bus, address: int, function: int, out) -> bool:
    try:
        value = await bus.read_register(address)
    except BusError as e:
        out(f"  read 0x{address:04X}: failed ({e})")
        return False
    try:
        await bus.write_register(address, value, function=function)
    except BusError as e:
        out(f"  rewrite 0x{address:04X} = {value} (unchanged): REFUSED: {e}")
        return False
    out(f"  rewrite 0x{address:04X} = {value} (unchanged): accepted")
    return True


async def unlock_test(bus: Bus, profile: ModuleType, password: int | None, register: int, out=print) -> int:
    prof = profile.PROFILE
    fc = prof.write_function
    if prof.password_input_register is None:
        out(f"profile {prof.name} has no password input register")
        return 2

    out(f"== Before (write function {fc})")
    out(f"  password status: {level_text(await _level(bus, prof.password_status_register))}")
    before = await _rewrite(bus, register, fc, out)
    if before:
        out("\nThe inverter already accepts this write; no password is needed.")
        return 0
    if password is None:
        out("\nNo --password given; nothing more to try.")
        return 1

    out(f"\n== Entering password {password} into 0x{prof.password_input_register:04X}")
    try:
        await bus.write_register(prof.password_input_register, password, function=fc)
        out("  accepted")
    except BusError as e:
        out(f"  REFUSED: {e}")
    out(f"  password status: {level_text(await _level(bus, prof.password_status_register))}")

    out("\n== After")
    after = await _rewrite(bus, register, fc, out)
    if after:
        out(f'\nThe password works. Add  password = "{password}"  under [inverter] in the config and restart.')
        return 0
    out("\nStill refused with this password.")
    return 1
