"""Host health for the dashboard: temperature, power/throttling, CPU, memory, disk and WiFi.

Reads Linux /proc and /sys, plus Raspberry Pi firmware flags (sysfs, falling back to `vcgencmd`).
Anything unavailable (e.g. on a laptop running --fake) is returned as null.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

CACHE_S = 3.0

# Raspberry Pi firmware get_throttled bits: current state in bits 0-3, "has occurred since boot" in 16-19.
THROTTLE_FLAGS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _run(*cmd: str) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def decode_throttled(value: int) -> dict:
    out: dict = {"raw": f"0x{value:x}"}
    for bit, name in THROTTLE_FLAGS.items():
        out[f"{name}_now"] = bool(value & (1 << bit))
        out[f"{name}_occurred"] = bool(value & (1 << (bit + 16)))
    return out


def _throttled() -> dict | None:
    raw = _read("/sys/devices/platform/soc/soc:firmware/get_throttled")
    if raw is None:
        out = _run("vcgencmd", "get_throttled")  # "throttled=0x50005"
        raw = out.split("=", 1)[1] if out and "=" in out else None
    if raw is None:
        return None
    try:
        return decode_throttled(int(raw, 16))
    except ValueError:
        return None


def _undervoltage_alarm() -> bool | None:
    """Live under-voltage flag from the rpi_volt hwmon driver (no special permissions needed)."""
    for d in Path("/sys/class/hwmon").glob("hwmon*"):
        if _read(str(d / "name")) == "rpi_volt":
            v = _read(str(d / "in0_lcrit_alarm"))
            return None if v is None else v == "1"
    return None


def _cpu_temp() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000, 1) if raw else None
    except ValueError:
        return None


def _core_volts() -> float | None:
    out = _run("vcgencmd", "measure_volts", "core")  # "volt=1.2000V"
    try:
        return float(out.split("=", 1)[1].rstrip("V")) if out else None
    except (IndexError, ValueError):
        return None


def _meminfo() -> dict | None:
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    kb = {}
    for line in raw.splitlines():
        k, _, v = line.partition(":")
        kb[k] = int(v.split()[0])
    total, avail = kb.get("MemTotal"), kb.get("MemAvailable")
    if not total or avail is None:
        return None
    used = total - avail
    swap_total, swap_free = kb.get("SwapTotal", 0), kb.get("SwapFree", 0)
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "available_mb": round(avail / 1024),
        "percent": round(100 * used / total, 1),
        "swap_used_mb": round((swap_total - swap_free) / 1024),
    }


def _wifi() -> dict | None:
    """Signal of the first wireless interface from /proc/net/wireless."""
    raw = _read("/proc/net/wireless")
    if not raw:
        return None
    for line in raw.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 4:
            try:
                return {
                    "interface": parts[0].rstrip(":"),
                    "link_quality": float(parts[2].rstrip(".")),  # out of 70
                    "signal_dbm": float(parts[3].rstrip(".")),
                }
            except ValueError:
                return None
    return None


def _process_rss_mb() -> float | None:
    raw = _read("/proc/self/status")
    for line in (raw or "").splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return None


class SystemMonitor:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._prev_cpu: tuple[int, int] | None = None
        self._cache: tuple[float, dict] | None = None
        self.model = (_read("/proc/device-tree/model") or "").rstrip("\x00") or None

    def _cpu_percent(self) -> float | None:
        raw = _read("/proc/stat")
        if not raw:
            return None
        vals = [int(x) for x in raw.splitlines()[0].split()[1:]]
        idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
        prev, self._prev_cpu = self._prev_cpu, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def read(self) -> dict:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < CACHE_S:
                return self._cache[1]
            data = self._collect()
            self._cache = (time.monotonic(), data)
            return data

    def _collect(self) -> dict:
        try:
            load = list(os.getloadavg())
        except OSError:
            load = None
        uptime = _read("/proc/uptime")
        db = Path(self.db_path)
        db_bytes = (
            sum(p.stat().st_size for p in db.parent.glob(db.name + "*") if p.is_file()) if db.parent.is_dir() else None
        )
        try:
            du = shutil.disk_usage(db.parent if db.parent.is_dir() else "/")
            disk = {
                "total_gb": round(du.total / 1e9, 1),
                "free_gb": round(du.free / 1e9, 1),
                "percent": round(100 * du.used / du.total, 1),
            }
        except OSError:
            disk = None
        throttled = _throttled()
        uv = _undervoltage_alarm()
        if throttled is None and uv is not None:
            throttled = {"raw": None, "under_voltage_now": uv}
        return {
            "hostname": socket.gethostname(),
            "model": self.model,
            "uptime_s": int(float(uptime.split()[0])) if uptime else None,
            "cpu_temp_c": _cpu_temp(),
            "cpu_percent": self._cpu_percent(),
            "cpu_count": os.cpu_count(),
            "load": [round(x, 2) for x in load] if load else None,
            "memory": _meminfo(),
            "disk": disk,
            "db_size_mb": round(db_bytes / 1e6, 1) if db_bytes is not None else None,
            "power": throttled,
            "core_volts": _core_volts(),
            "wifi": _wifi(),
            "process_rss_mb": _process_rss_mb(),
        }
