"""BMS endpoints, under /api/bms/*. The old single-device paths (/api/current, …) are aliases."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..bms.series import SPEC
from ..bms.state import snapshot
from ..core.clock import now_ms
from .common import MAX_POINTS_CAP, AppContext, age_s, csv_response, parse_fields, parse_range

CURRENT_FIELDS = (
    "voltage", "current", "power", "soc", "capacity_remain", "nominal_capacity", "cycle_count",
    "cycle_capacity", "avg_cell_voltage", "delta_cell_voltage", "min_cell_voltage", "max_cell_voltage",
    "min_cell_index", "max_cell_index", "balance_current", "balancing_action", "temp_t1", "temp_t2",
    "temp_mos", "charge_on", "discharge_on", "balance_on", "bms_uptime_s", "cell_voltages", "wire_resist",
)  # fmt: skip
HISTORY_FIELDS = tuple(f.name for f in SPEC.fields)


def bms_current(ctx: AppContext) -> dict:
    if ctx.bms is None:
        return {"ts": None, "age_s": None, "stale": True, "link": "disabled", "rssi": None} | dict.fromkeys(
            CURRENT_FIELDS
        )
    state = ctx.bms.state
    at = now_ms()
    age = state.cells_age_s(at)
    out: dict = {
        "ts": state.latest_cells_ts,
        "age_s": None if age is None else round(age, 1),
        "stale": age is None or age > ctx.cfg.storage.stale_s,
        "link": state.link_status,
        "rssi": state.ble.rssi,
    }
    cells = state.latest_cells
    if cells is None:
        out.update(dict.fromkeys(CURRENT_FIELDS))
        return out
    snap = snapshot(cells, state.cell_count, state.cell_count_known)
    for k in CURRENT_FIELDS:
        out[k] = getattr(snap, k) if hasattr(snap, k) else getattr(cells, k)
    return out


def make_router(ctx: AppContext) -> APIRouter:
    r = APIRouter(tags=["bms"])

    def driver():
        if ctx.bms is None:
            raise HTTPException(404, "BMS is disabled")
        return ctx.bms

    @r.get("/current")
    def current():
        return bms_current(ctx)

    @r.get("/settings")
    def settings():
        if ctx.bms is not None and ctx.bms.state.latest_settings is not None:
            return {"ts": ctx.bms.state.latest_settings_ts, "settings": ctx.bms.state.latest_settings}
        row = ctx.store.latest_settings("bms")
        return {"ts": row[0], "settings": row[1]} if row else {"ts": None, "settings": None}

    @r.get("/device")
    def device():
        if ctx.bms is not None and ctx.bms.state.device_info is not None:
            return {"ts": ctx.bms.state.device_info_ts, "device": ctx.bms.state.device_info}
        row = ctx.store.device_info("bms")
        return {"ts": row[0], "device": row[1]} if row else {"ts": None, "device": None}

    @r.get("/history")
    def history(
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        fields: str | None = None,
        max_points: int = Query(600, ge=1, le=MAX_POINTS_CAP),
    ):
        f, t = parse_range(from_, to)
        names = parse_fields(fields, HISTORY_FIELDS, SPEC.default_fields)
        return driver().series.history(f, t, names, max_points, ctx.cfg.storage.sample_interval_s)

    @r.get("/history/cells")
    def history_cells(
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        max_points: int = Query(600, ge=1, le=MAX_POINTS_CAP),
    ):
        f, t = parse_range(from_, to)
        h = driver().series.array_history("cell_voltages", f, t, max_points, ctx.cfg.storage.sample_interval_s)
        return {
            "from": h["from"],
            "to": h["to"],
            "bucket_s": h["bucket_s"],
            "source": h["source"],
            "cell_count": h["width"],
            "points": [{"ts": p["ts"], "cells": p["values"]} for p in h["points"]],
        }

    @r.get("/export.csv")
    def export_csv(from_: str | None = Query(None, alias="from"), to: str | None = None):
        f, t = parse_range(from_, to)
        return csv_response(driver().series.export_rows(f, t), "bms", f, t)

    @r.get("/link")
    def link():
        """BLE link diagnostics."""
        d = driver()
        b = d.state.ble
        return {
            "link": d.state.link_status,
            "rssi": b.rssi,
            "rssi_quality": rssi_quality(b.rssi),
            "rssi_source": b.rssi_source,
            "rssi_age_s": age_s(b.rssi_ts),
            "recovery_stage": b.recovery_stage,
            "adapter_resets": b.adapter_resets,
            "last_adapter_reset_ts": b.last_adapter_reset_ts,
            "counters": vars(d.state.counters),
        }

    return r


def rssi_quality(rssi: int | None) -> str | None:
    if rssi is None:
        return None
    return "excellent" if rssi >= -60 else "good" if rssi >= -70 else "fair" if rssi >= -80 else "poor"
