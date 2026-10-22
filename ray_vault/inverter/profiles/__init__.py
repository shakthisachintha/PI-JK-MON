"""Register profiles, one module per inverter family. Each exposes PROFILE, decode_info() and decode_faults()."""

from __future__ import annotations

from types import ModuleType

from . import hf

PROFILES: dict[str, ModuleType] = {"hf": hf}


def get_profile(name: str) -> ModuleType:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown inverter profile {name!r}; available: {', '.join(PROFILES)}") from None
