"""FastAPI app: REST API (§8) and the static dashboard (§9)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config
from .state import State, now_ms, snapshot
from .store import DAY_MS, HISTORY_FIELDS, QueryError, Store

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HISTORY_FIELDS = "voltage,current,power,soc"
MAX_POINTS_CAP = 5000

CURRENT_FIELDS = (
    "voltage", "current", "power", "soc", "capacity_remain", "nominal_capacity", "cycle_count",
    "cycle_capacity", "avg_cell_voltage", "delta_cell_voltage", "min_cell_voltage", "max_cell_voltage",
    "min_cell_index", "max_cell_index", "balance_current", "balancing_action", "temp_t1", "temp_t2",
    "temp_mos", "charge_on", "discharge_on", "balance_on", "bms_uptime_s", "cell_voltages", "wire_resist",
)  # fmt: skip


def parse_time(value: str | None, default: int, name: str) -> int:
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
    now = now_ms()
    t = parse_time(to, now, "to")
    f = parse_time(frm, t - default_span_ms, "from")
    if f > t:
        raise QueryError("'from' must be <= 'to'")
    return f, t


def create_app(cfg: Config, state: State, store: Store, static_dir: Path | None = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="JK BMS Monitor", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.exception_handler(QueryError)
    async def _query_error(_req: Request, exc: QueryError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=400, content={"error": "; ".join(msgs) or "invalid request"})

    @app.get("/api/health")
    def health():
        age = state.cells_age_s()
        db_ok = store.check_writable()
        degraded = age is None or age > cfg.stale_s or not db_ok
        return {
            "status": "degraded" if degraded else "ok",
            "ble": state.link_status,
            "last_frame_age_s": None if age is None else round(age, 1),
            "db_ok": db_ok,
            "service_uptime_s": int((now_ms() - state.started_ms) / 1000),
            "version": __version__,
            "counters": vars(state.counters),
        }

    @app.get("/api/current")
    def current():
        at = now_ms()
        age = state.cells_age_s(at)
        out: dict = {
            "ts": state.latest_cells_ts,
            "age_s": None if age is None else round(age, 1),
            "stale": age is None or age > cfg.stale_s,
            "link": state.link_status,
        }
        cells = state.latest_cells
        if cells is None:
            out.update({k: None for k in CURRENT_FIELDS})
            return out
        snap = snapshot(cells, state.cell_count, state.cell_count_known)
        for k in CURRENT_FIELDS:
            out[k] = getattr(snap, k) if hasattr(snap, k) else getattr(cells, k)
        return out

    @app.get("/api/settings")
    def settings():
        if state.latest_settings is not None:
            return {"ts": state.latest_settings_ts, "settings": state.latest_settings}
        row = store.latest_settings()
        return {"ts": row[0], "settings": row[1]} if row else {"ts": None, "settings": None}

    @app.get("/api/device")
    def device():
        if state.device_info is not None:
            return {"ts": state.device_info_ts, "device": state.device_info}
        row = store.device_info()
        return {"ts": row[0], "device": row[1]} if row else {"ts": None, "device": None}

    @app.get("/api/history")
    def history(
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        fields: str = DEFAULT_HISTORY_FIELDS,
        max_points: int = Query(600, ge=1, le=MAX_POINTS_CAP),
    ):
        f, t = parse_range(from_, to)
        names = [x.strip() for x in fields.split(",") if x.strip()]
        if not names:
            raise QueryError(f"'fields' must list one or more of: {','.join(HISTORY_FIELDS)}")
        return store.history(f, t, list(dict.fromkeys(names)), max_points, cfg.sample_interval_s)

    @app.get("/api/history/cells")
    def history_cells(
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        max_points: int = Query(600, ge=1, le=MAX_POINTS_CAP),
    ):
        f, t = parse_range(from_, to)
        return store.cell_history(f, t, max_points, cfg.sample_interval_s)

    @app.get("/api/events")
    def events(
        limit: int = Query(100, ge=1, le=1000),
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
    ):
        f = parse_time(from_, None, "from") if from_ else None
        t = parse_time(to, None, "to") if to else None
        if f is not None and t is not None and f > t:
            raise QueryError("'from' must be <= 'to'")
        return {"events": store.events(limit, f, t)}

    @app.get("/api/export.csv")
    def export_csv(from_: str | None = Query(None, alias="from"), to: str | None = None):
        f, t = parse_range(from_, to)
        rows = store.export_rows(f, t)
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
        name = (
            f"jkbms_{datetime.fromtimestamp(f / 1000, UTC):{stamp}}_{datetime.fromtimestamp(t / 1000, UTC):{stamp}}.csv"
        )
        return StreamingResponse(
            stream(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return HTMLResponse(
                "<h1>JK BMS Monitor</h1><p>The dashboard has not been built. "
                "Run <code>npm run build</code> in <code>web/</code>. The API is at <a href='/docs'>/docs</a>.</p>"
            )

    return app
