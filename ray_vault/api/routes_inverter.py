"""Inverter endpoints, under /api/inverter/*."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from ..inverter.series import SPEC
from .common import MAX_POINTS_CAP, AppContext, age_s, csv_response, parse_fields, parse_range

HISTORY_FIELDS = tuple(f.name for f in SPEC.fields)


class SettingWrite(BaseModel):
    value: float
    confirm: bool = False


def inverter_current(ctx: AppContext) -> dict:
    d = ctx.inverter
    if d is None:
        return {"ts": None, "age_s": None, "stale": True, "link": "disabled"}
    age = d.state.live_age_s()
    return {
        "ts": d.state.live_ts,
        "age_s": None if age is None else round(age, 1),
        "stale": age is None or age > ctx.cfg.storage.stale_s,
        "link": d.state.link,
        "last_error": d.state.last_error,
        "fault_codes": d.state.fault_codes,
        **(d.state.live or {}),
    }


def make_router(ctx: AppContext) -> APIRouter:
    r = APIRouter(prefix="/api/inverter", tags=["inverter"])

    def driver():
        if ctx.inverter is None:
            raise HTTPException(404, "inverter is disabled")
        return ctx.inverter

    @r.get("/current")
    def current():
        return inverter_current(ctx)

    @r.get("/device")
    def device():
        d = ctx.inverter
        if d is not None and d.state.device is not None:
            return {"ts": d.state.device_ts, "device": d.state.device}
        row = ctx.store.device_info("inverter")
        return {"ts": row[0], "device": row[1]} if row else {"ts": None, "device": None}

    @r.get("/link")
    def link():
        d = driver()
        return {
            "link": d.state.link,
            "last_error": d.state.last_error,
            "port": getattr(d.bus, "port", None),
            "live_age_s": age_s(d.state.live_ts),
            "settings_age_s": age_s(d.state.settings_ts),
            "counters": vars(d.state.counters),
        }

    @r.get("/settings")
    def settings():
        if ctx.controller is None:
            raise HTTPException(404, "inverter is disabled")
        out = ctx.controller.describe()
        return out

    @r.post("/settings/{key}")
    async def write_setting(key: str, body: SettingWrite, request: Request, x_control_pin: str | None = Header(None)):
        if ctx.controller is None:
            raise HTTPException(404, "inverter is disabled")
        client = request.client.host if request.client else "unknown"
        value = int(body.value) if body.value == int(body.value) else body.value
        return await ctx.controller.apply(key, value, pin=x_control_pin, client=client, confirm=body.confirm)

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

    @r.get("/export.csv")
    def export_csv(from_: str | None = Query(None, alias="from"), to: str | None = None):
        f, t = parse_range(from_, to)
        return csv_response(driver().series.export_rows(f, t), "inverter", f, t)

    return r
