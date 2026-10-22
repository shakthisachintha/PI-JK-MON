"""Register model for SRNE inverters: live readings, read blocks and writable settings.

Pure module (no I/O). A profile (profiles/*.py) instantiates these for one model/firmware family; everything
else (driver, controls, API, UI) is driven from the profile, so supporting another model means adding a profile.

Units: a SettingSpec's min/max/step are in *register units* (for 12 V-normalised battery voltages that's the
12 V-equivalent, e.g. 14.4 V). `display_*` helpers convert to real units using the system voltage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["enum", "number"]


@dataclass(frozen=True)
class Reading:
    key: str
    address: int
    scale: float = 1.0
    signed: bool = False
    unit: str = ""
    label: str = ""
    negate: bool = False  # flip sign so + means "into the battery", matching the BMS convention

    def decode(self, raw: int) -> float | int:
        if self.signed and raw >= 0x8000:
            raw -= 0x10000
        value = raw * self.scale
        if self.negate:
            value = -value
        return int(value) if self.scale == 1 else round(value, _digits(self.scale))


@dataclass(frozen=True)
class Block:
    start: int
    count: int

    @property
    def addresses(self) -> range:
        return range(self.start, self.start + self.count)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    group: str
    address: int
    kind: Kind = "number"
    scale: float = 1.0  # value = raw * scale (register units)
    unit: str = ""
    min: float = 0
    max: float = 0
    step: float = 1
    options: tuple[tuple[int, str], ...] = ()  # enum values -> labels
    byte: Literal["hi", "lo"] | None = None  # value lives in one byte of a shared register
    battery_scaled: bool = False  # 12 V-normalised battery voltage; multiply by system_voltage / 12
    charge_side: bool = False  # a charging voltage: must stay below the BMS over-voltage limit
    discharge_side: bool = False  # a discharge voltage: must stay above the BMS under-voltage limit
    danger: bool = False  # wrong values can damage the battery: needs explicit confirmation
    writable: bool = True
    description: str = ""

    # --- raw register <-> register units ---

    def from_raw(self, raw: int) -> float | int:
        if self.byte == "hi":
            raw = (raw >> 8) & 0xFF
        elif self.byte == "lo":
            raw &= 0xFF
        if self.kind == "enum" or self.scale == 1:
            return int(raw)
        return round(raw * self.scale, _digits(self.scale))

    def to_raw(self, value: float, current_raw: int | None = None) -> int:
        raw = int(round(value / self.scale))
        if self.byte is None:
            return raw & 0xFFFF
        if current_raw is None:
            raise ValueError(f"{self.key}: packed byte setting needs the current register value")
        if self.byte == "hi":
            return ((raw & 0xFF) << 8) | (current_raw & 0x00FF)
        return (current_raw & 0xFF00) | (raw & 0xFF)

    # --- register units <-> display units ---

    def factor(self, system_voltage: int | None) -> float:
        if not self.battery_scaled:
            return 1.0
        if not system_voltage:
            raise ValueError("system voltage unknown")
        return system_voltage / 12

    def to_display(self, value: float, system_voltage: int | None) -> float | int:
        if not self.battery_scaled:
            return value
        return round(value * self.factor(system_voltage), 2)

    def from_display(self, value: float, system_voltage: int | None) -> float:
        if not self.battery_scaled:
            return value
        return value / self.factor(system_voltage)

    def describe(self, system_voltage: int | None) -> dict:
        """JSON schema for the UI, in display units."""
        f = self.factor(system_voltage) if (not self.battery_scaled or system_voltage) else None
        d = {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "kind": self.kind,
            "unit": self.unit,
            "danger": self.danger,
            "writable": self.writable,
            "description": self.description,
        }
        if self.kind == "enum":
            d["options"] = [{"value": v, "label": lbl} for v, lbl in self.options]
        elif f is not None and self.writable:
            d.update(min=round(self.min * f, 2), max=round(self.max * f, 2), step=round(self.step * f, 3))
        return d


@dataclass(frozen=True)
class Profile:
    name: str
    title: str
    live_blocks: tuple[Block, ...]
    settings_blocks: tuple[Block, ...]
    info_blocks: tuple[Block, ...]
    readings: tuple[Reading, ...]
    settings: tuple[SettingSpec, ...]
    machine_states: dict[int, str] = field(default_factory=dict)
    system_voltage_register: int | None = None
    ordering: tuple[tuple[str, str, str], ...] = ()  # (a, "<=" | "<", b) constraints between settings

    def setting(self, key: str) -> SettingSpec:
        for s in self.settings:
            if s.key == key:
                return s
        raise KeyError(key)


def _digits(scale: float) -> int:
    s = f"{scale:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0
