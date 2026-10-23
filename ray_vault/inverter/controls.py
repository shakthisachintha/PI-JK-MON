"""Changing inverter settings safely.

Order of checks for a write (any failure stops it, nothing is sent to the inverter):
  1. writes enabled (a PIN is configured) and the client isn't locked out
  2. PIN correct (constant-time compare; repeated failures lock the client out)
  3. the setting exists in the profile and is writable
  4. value valid for the spec (enum option / range / step)
  5. "danger" settings carry confirm=true
  6. ordering between related settings (e.g. float <= boost <= charge limit)
  7. battery guardrails from the BMS: per-cell charge voltages below the BMS over-voltage limit, discharge
     voltages above its under-voltage limit
Then: write -> read back -> must match -> audit event with old -> new and the client address.
"""

from __future__ import annotations

import hmac
import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .bus import BusError
from .driver import InverterDriver
from .registers import SettingSpec

log = logging.getLogger(__name__)

BMS_MARGIN_V = 0.05  # per cell, kept between inverter setpoints and the BMS protection limits


class ControlError(Exception):
    status = 400

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status = status


@dataclass
class BmsLimits:
    cell_count: int
    cell_ovp: float  # V per cell
    cell_uvp: float


class PinGuard:
    def __init__(self, pin: str, max_failures: int = 5, lockout_s: float = 600, clock=time.monotonic) -> None:
        self.pin = pin
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self.clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.pin)

    def locked_for(self, client: str) -> float:
        """Seconds this client is still locked out (0 if not)."""
        return max(0.0, self._locked_until.get(client, 0) - self.clock())

    def check(self, client: str, pin: str | None) -> None:
        if not self.enabled:
            raise ControlError("inverter settings are read-only: set control.pin in the config to enable changes", 403)
        if (left := self.locked_for(client)) > 0:
            raise ControlError(f"too many wrong PINs; try again in {math.ceil(left / 60)} min", 423)
        if pin is not None and hmac.compare_digest(pin.encode(), self.pin.encode()):
            self._failures.pop(client, None)
            return
        now = self.clock()
        q = self._failures[client]
        q.append(now)
        while q and now - q[0] > self.lockout_s:
            q.popleft()
        if len(q) >= self.max_failures:
            self._locked_until[client] = now + self.lockout_s
            q.clear()
            log.warning("control: %s locked out after %d wrong PINs", client, self.max_failures)
            raise ControlError("too many wrong PINs; locked out", 423)
        raise ControlError("wrong PIN", 401)


def _is_multiple(value: float, base: float, step: float) -> bool:
    k = (value - base) / step
    return abs(k - round(k)) < 1e-6


def validate_value(spec: SettingSpec, value, factor: float = 1.0) -> float:
    """Validate a value given in display units (register units x factor). Returns it in register units."""
    if not spec.writable:
        raise ControlError(f"{spec.label} is read-only")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ControlError(f"{spec.label}: expected a number")
    if spec.kind == "enum":
        allowed = {v for v, _ in spec.options}
        if value not in allowed:
            raise ControlError(f"{spec.label}: must be one of {sorted(allowed)}")
        return int(value)
    lo, hi, step = spec.min * factor, spec.max * factor, spec.step * factor
    unit = f" {spec.unit}" if spec.unit else ""
    if value < lo - 1e-9 or value > hi + 1e-9:
        raise ControlError(f"{spec.label}: must be between {lo:g} and {hi:g}{unit}")
    if not _is_multiple(value, lo, step):
        raise ControlError(f"{spec.label}: must be in steps of {step:g}{unit}")
    return round(value / factor, 6)


class Controller:
    def __init__(self, driver: InverterDriver, guard: PinGuard, bms_limits=lambda: None) -> None:
        self.driver = driver
        self.guard = guard
        self.bms_limits = bms_limits  # () -> BmsLimits | None, read from the live BMS state

    @property
    def writable(self) -> bool:
        return self.guard.enabled

    def _check_ordering(self, key: str, new_display: float) -> None:
        d = self.driver
        prof = d.profile
        for a, op, b in prof.ordering:
            if key not in (a, b):
                continue
            va = new_display if key == a else d.setting_value(prof.setting(a))
            vb = new_display if key == b else d.setting_value(prof.setting(b))
            if va is None or vb is None:
                continue
            ok = va <= vb + 1e-9 if op == "<=" else va < vb - 1e-9
            if not ok:
                la, lb = prof.setting(a).label, prof.setting(b).label
                raise ControlError(f"{la} ({va} V) must be {'≤' if op == '<=' else '<'} {lb} ({vb} V)")

    def _check_bms(self, spec: SettingSpec, display: float) -> None:
        if not (spec.charge_side or spec.discharge_side):
            return
        limits = self.bms_limits()
        if limits is None or not limits.cell_count:
            raise ControlError(
                "BMS protection limits are unknown (BMS not connected yet), so battery voltages can't be checked. "
                "Try again once the BMS is online."
            )
        per_cell = display / limits.cell_count
        if spec.charge_side and per_cell > limits.cell_ovp - BMS_MARGIN_V:
            cap = (limits.cell_ovp - BMS_MARGIN_V) * limits.cell_count
            raise ControlError(
                f"{spec.label} {display:.2f} V is {per_cell:.3f} V/cell, above the BMS over-voltage limit "
                f"({limits.cell_ovp:.3f} V/cell) minus {BMS_MARGIN_V * 1000:.0f} mV. Max allowed: {cap:.2f} V."
            )
        if spec.discharge_side and per_cell < limits.cell_uvp + BMS_MARGIN_V:
            floor = (limits.cell_uvp + BMS_MARGIN_V) * limits.cell_count
            raise ControlError(
                f"{spec.label} {display:.2f} V is {per_cell:.3f} V/cell, below the BMS under-voltage limit "
                f"({limits.cell_uvp:.3f} V/cell) plus {BMS_MARGIN_V * 1000:.0f} mV. Min allowed: {floor:.2f} V."
            )

    def _hint(self, spec: SettingSpec, e: Exception) -> str:
        code = getattr(e, "code", None)
        if code in (7, 11) and spec.battery_scaled:
            try:
                bt_spec = self.driver.profile.setting("battery_type")
            except KeyError:
                return ""
            bt = self.driver.setting_value(bt_spec)
            label = dict(bt_spec.options).get(bt, f"type {bt}") if bt is not None else "unknown"
            if bt != 0:
                return (
                    f". The battery type is {label}: SRNE inverters normally only accept battery voltage changes "
                    "when the battery type is User defined (USE)."
                )
        if code == 9:
            return ". Unlock the inverter's user password on its keypad, or clear it, and try again."
        return ""

    async def apply(self, key: str, value, *, pin: str | None, client: str, confirm: bool = False) -> dict:
        self.guard.check(client, pin)
        d = self.driver
        try:
            spec = d.profile.setting(key)
        except KeyError:
            raise ControlError(f"unknown setting: {key}", 404) from None
        if d.state.link != "connected":
            raise ControlError("inverter is not connected", 409)
        if d.state.settings_ts is None:
            raise ControlError("inverter settings haven't been read yet; try again in a moment", 409)

        sv = d.system_voltage
        if spec.battery_scaled and not sv:
            raise ControlError("system voltage unknown; can't convert battery voltages", 409)
        reg_value = validate_value(spec, value, spec.factor(sv))
        display = spec.to_display(reg_value, sv)
        if spec.danger and not confirm:
            raise ControlError(f"{spec.label} can damage the battery if wrong; resend with confirm=true", 428)
        if spec.battery_scaled:
            self._check_ordering(key, display)
            self._check_bms(spec, display)

        old = d.setting_value(spec)
        try:
            readback = await d.write_verified(spec, reg_value)
        except BusError as e:
            d.writer.event("inverter", "inverter_setting_failed", f"{key}: {old} -> {display}: {e} (by {client})")
            raise ControlError(f"writing {spec.label} failed: {e}{self._hint(spec, e)}", 502) from e
        new = spec.to_display(spec.from_raw(readback), sv) if spec.battery_scaled else spec.from_raw(readback)
        d.writer.event("inverter", "inverter_setting", f"{key}: {old} -> {new} (by {client})")
        log.info("control: %s set %s %s -> %s", client, key, old, new)
        d.request_settings_refresh()
        return {"key": key, "old": old, "new": new}

    def describe(self) -> dict:
        """Settings with values and schema, grouped, for the UI."""
        d = self.driver
        sv = d.system_voltage
        groups: dict[str, list] = {}
        for spec in d.profile.settings:
            item = spec.describe(sv)
            item["value"] = d.setting_value(spec)
            if spec.writable and not self.writable:
                item["writable"] = False
            groups.setdefault(spec.group, []).append(item)
        limits = self.bms_limits()
        return {
            "writable": self.writable,
            "connected": d.state.link == "connected",
            "read_ts": d.state.settings_ts,
            "system_voltage": sv,
            "profile": d.profile.title,
            "bms_limits": None
            if limits is None
            else {"cell_count": limits.cell_count, "cell_ovp": limits.cell_ovp, "cell_uvp": limits.cell_uvp},
            "groups": [{"name": g, "settings": items} for g, items in groups.items()],
        }
