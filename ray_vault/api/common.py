"""Shared pieces for the API routers: the app context, time-range parsing and CSV streaming."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi.responses import StreamingResponse

from ..bms.driver import BmsDriver
from ..core.clock import now_ms
from ..core.config import Config
from ..core.store import DAY_MS, QueryError, Store
from ..core.system import SystemMonitor
from ..inverter.controls import Controller
from ..inverter.driver import InverterDriver

MAX_POINTS_CAP = 5000


@dataclass
class AppContext:
    cfg: Config
    store: Store
    bms: BmsDriver | None = None
    inverter: InverterDriver | None = None
    controller: Controller | None = None
    started_ms: int = field(default_factory=now_ms)
    monitor: SystemMonitor | None = None

    def __post_init__(self) -> None:
        if self.monitor is None:
            self.monitor = SystemMonitor(self.cfg.storage.db_path)


def parse_time(value: str | None, default: int | None, name: str) -> int | None:
    """Epoch milliseconds or ISO-8601 (naive means UTC)."""
    if value is None or value == "":
        return default
    v = value.strip()
    if v.lstrip("-").isdigit():
        return int(v)
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise QueryError(f"invalid '{name}': expected epoch ms or ISO-8601") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def parse_range(frm: str | None, to: str | None, default_span_ms: int = DAY_MS) -> tuple[int, int]:
    t = parse_time(to, now_ms(), "to")
    f = parse_time(frm, t - default_span_ms, "from")
    if f > t:
        raise QueryError("'from' must be <= 'to'")
    return f, t


def parse_fields(fields: str | None, allowed: tuple[str, ...], default: tuple[str, ...]) -> list[str]:
    if fields is None:
        return list(default)
    names = list(dict.fromkeys(x.strip() for x in fields.split(",") if x.strip()))
    if not names:
        raise QueryError(f"'fields' must list one or more of: {','.join(allowed)}")
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise QueryError(f"unknown field(s): {', '.join(unknown)}")
    return names


def csv_response(rows: Iterator[list], prefix: str, frm: int, to: int) -> StreamingResponse:
    header = next(rows)  # raises QueryError before streaming starts

    def stream():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for i, row in enumerate(rows, 1):
            w.writerow(row)
            if i % 500 == 0:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    stamp = "%Y%m%dT%H%MZ"
    a = datetime.fromtimestamp(frm / 1000, UTC)
    b = datetime.fromtimestamp(to / 1000, UTC)
    name = f"{prefix}_{a:{stamp}}_{b:{stamp}}.csv"
    return StreamingResponse(
        stream(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


def age_s(ts: int | None, at: int | None = None) -> float | None:
    return None if ts is None else round(max(0.0, ((at or now_ms()) - ts) / 1000), 1)
