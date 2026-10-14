"""Configuration: TOML file, overridden per key by JKBMS_<KEY> environment variables (§10)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/jkbms-pi/config.toml")
ENV_PREFIX = "JKBMS_"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    bms_mac: str = ""
    adapter: str = "hci0"
    db_path: str = "/var/lib/jkbms-pi/jkbms.db"
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    sample_interval_s: float = 3
    stale_s: float = 10
    raw_retention_days: int = 30
    cell_rerequest_s: float = 5
    link_timeout_s: float = 20  # no cell-data frame for this long while connected -> reconnect
    adapter_reset_after_s: float = 120  # no cell data for this long -> power-cycle the BT adapter
    adapter_reset_max_interval_s: float = 1800  # backoff cap between adapter resets
    restart_after_resets: int = 3  # adapter resets without recovery -> exit for systemd restart (0 = never)
    rssi_poll_s: float = 30
    log_level: str = "INFO"


def _coerce(name: str, value, typ: type):
    try:
        if typ is int:
            return int(value)
        if typ is float:
            return float(value)
        return str(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"invalid value for {name!r}: {value!r}") from e


def load_config(path: str | os.PathLike | None, env: dict[str, str] | None = None) -> tuple[Config, set[str]]:
    """Load config. Returns the config and the set of keys that were explicitly set.

    If `path` is None, the default path is used when it exists; otherwise defaults apply.
    An explicit `path` that does not exist is an error.
    """
    env = os.environ if env is None else env
    data: dict = {}
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        data = tomllib.loads(p.read_text())
    elif DEFAULT_CONFIG_PATH.is_file():
        data = tomllib.loads(DEFAULT_CONFIG_PATH.read_text())

    known = {f.name: f for f in fields(Config)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")

    values: dict = {}
    for name, f in known.items():
        typ = type(f.default)
        if name in data:
            values[name] = _coerce(name, data[name], typ)
        env_val = env.get(ENV_PREFIX + name.upper())
        if env_val is not None:
            values[name] = _coerce(name, env_val, typ)

    cfg = Config(**values)
    cfg.bms_mac = cfg.bms_mac.strip().lower()
    cfg.log_level = cfg.log_level.upper()
    if cfg.sample_interval_s <= 0:
        raise ConfigError("sample_interval_s must be > 0")
    return cfg, set(values)
