"""Configuration: a sectioned TOML file, each key overridable by RAYVAULT_<SECTION>_<KEY> environment variables.

Files written for the old single-device app (flat keys such as `bms_mac`, `db_path`) are still accepted and
mapped onto the sections, so an existing /etc config keeps working after the rename.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/ray-vault/config.toml")
ENV_PREFIX = "RAYVAULT_"


class ConfigError(Exception):
    pass


@dataclass
class HttpConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class StorageConfig:
    db_path: str = "/var/lib/ray-vault/ray-vault.db"
    sample_interval_s: float = 3  # one stored row per device per interval while data is fresh
    stale_s: float = 10  # readings older than this are not stored and are flagged stale
    raw_retention_days: int = 30  # per-minute rollups are kept forever


@dataclass
class BmsConfig:
    enabled: bool = True
    mac: str = ""
    adapter: str = "hci0"
    cell_rerequest_s: float = 5
    link_timeout_s: float = 20  # no cell-data frame for this long while connected -> reconnect
    adapter_reset_after_s: float = 120  # no cell data for this long -> power-cycle the BT adapter
    adapter_reset_max_interval_s: float = 1800  # backoff cap between adapter resets
    restart_after_resets: int = 3  # adapter resets without recovery -> restart bluetoothd (0 = never)
    rssi_poll_s: float = 30


@dataclass
class InverterConfig:
    enabled: bool = False
    port: str = ""  # prefer /dev/serial/by-id/... over /dev/ttyUSB0 (stable across re-plugs)
    baudrate: int = 9600
    slave: int = 1
    profile: str = "hf"  # register map, see ray_vault/inverter/profiles/
    poll_interval_s: float = 2
    settings_poll_s: float = 60
    timeout_s: float = 1.0


@dataclass
class ControlConfig:
    pin: str = ""  # empty = inverter settings are read-only
    max_failures: int = 5  # wrong PINs within lockout_s from one client ...
    lockout_s: float = 600  # ... lock that client out for this long


@dataclass
class Config:
    http: HttpConfig = field(default_factory=HttpConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    bms: BmsConfig = field(default_factory=BmsConfig)
    inverter: InverterConfig = field(default_factory=InverterConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    log_level: str = "INFO"


SECTIONS = ("http", "storage", "bms", "inverter", "control")

# Flat keys from the jkbms-pi era -> (section, key).
LEGACY_KEYS = {
    "bms_mac": ("bms", "mac"),
    "adapter": ("bms", "adapter"),
    "cell_rerequest_s": ("bms", "cell_rerequest_s"),
    "link_timeout_s": ("bms", "link_timeout_s"),
    "adapter_reset_after_s": ("bms", "adapter_reset_after_s"),
    "adapter_reset_max_interval_s": ("bms", "adapter_reset_max_interval_s"),
    "restart_after_resets": ("bms", "restart_after_resets"),
    "rssi_poll_s": ("bms", "rssi_poll_s"),
    "db_path": ("storage", "db_path"),
    "sample_interval_s": ("storage", "sample_interval_s"),
    "stale_s": ("storage", "stale_s"),
    "raw_retention_days": ("storage", "raw_retention_days"),
    "http_host": ("http", "host"),
    "http_port": ("http", "port"),
}


def _coerce(name: str, value, typ: type):
    try:
        if typ is bool:
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            raise ValueError(value)
        if typ is int:
            return int(value)
        if typ is float:
            return float(value)
        return str(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"invalid value for {name!r}: {value!r}") from e


def _read_file(path: str | os.PathLike | None) -> dict:
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
    else:
        p = DEFAULT_CONFIG_PATH
        if not p.is_file():
            return {}
    try:
        return tomllib.loads(p.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: {e}") from e


def _normalise(data: dict) -> dict[str, dict]:
    """Fold legacy flat keys into sections; reject unknown keys."""
    out: dict[str, dict] = {s: {} for s in SECTIONS}
    top: dict = {}
    for k, v in data.items():
        if k in SECTIONS:
            if not isinstance(v, dict):
                raise ConfigError(f"[{k}] must be a table")
            out[k].update(v)
        elif k in LEGACY_KEYS:
            sec, key = LEGACY_KEYS[k]
            out[sec].setdefault(key, v)
        elif k == "log_level":
            top[k] = v
        else:
            raise ConfigError(f"unknown config key: {k}")
    out["_top"] = top
    return out


def load_config(path: str | os.PathLike | None, env: dict[str, str] | None = None) -> tuple[Config, set[str]]:
    """Load config. Returns the config and the dotted keys that were set explicitly (file or env).

    If `path` is None, the default path is used when it exists; otherwise defaults apply.
    """
    env = os.environ if env is None else env
    raw = _normalise(_read_file(path))
    cfg = Config()
    explicit: set[str] = set()

    for sec in SECTIONS:
        obj = getattr(cfg, sec)
        known = {f.name: f for f in fields(obj)}
        unknown = set(raw[sec]) - set(known)
        if unknown:
            raise ConfigError(f"unknown key(s) in [{sec}]: {', '.join(sorted(unknown))}")
        for name, f in known.items():
            typ = type(f.default)
            dotted = f"{sec}.{name}"
            if name in raw[sec]:
                setattr(obj, name, _coerce(dotted, raw[sec][name], typ))
                explicit.add(dotted)
            env_val = env.get(f"{ENV_PREFIX}{sec.upper()}_{name.upper()}")
            if env_val is not None:
                setattr(obj, name, _coerce(dotted, env_val, typ))
                explicit.add(dotted)

    level = env.get(f"{ENV_PREFIX}LOG_LEVEL", raw["_top"].get("log_level", cfg.log_level))
    cfg.log_level = str(level).upper()

    cfg.bms.mac = cfg.bms.mac.strip().lower()
    if cfg.storage.sample_interval_s <= 0:
        raise ConfigError("storage.sample_interval_s must be > 0")
    if cfg.inverter.enabled and not cfg.inverter.port:
        raise ConfigError("inverter.port is required when the inverter is enabled")
    return cfg, explicit
