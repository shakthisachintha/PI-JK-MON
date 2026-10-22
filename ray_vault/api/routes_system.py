"""Cross-device endpoints: health, Pi stats, events and the combined overview."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import __version__
from ..core.clock import now_ms
from ..core.store import QueryError
from .common import AppContext, parse_time
from .routes_bms import bms_current, rssi_quality
from .routes_inverter import inverter_current

EVENT_SOURCES = ("bms", "inverter", "system")


def make_router(ctx: AppContext) -> APIRouter:
    r = APIRouter(prefix="/api", tags=["system"])

    @r.get("/health")
    def health():
        """Liveness for monitoring. Always 200; `status` is "degraded" if any enabled device is stale."""
        db_ok = ctx.store.check_writable()
        devices = {}
        bms = bms_current(ctx)
        if ctx.bms is not None:
            devices["bms"] = {"link": bms["link"], "age_s": bms["age_s"], "stale": bms["stale"]}
        if ctx.inverter is not None:
            inv = inverter_current(ctx)
            devices["inverter"] = {"link": inv["link"], "age_s": inv["age_s"], "stale": inv["stale"]}
        degraded = not db_ok or any(d["stale"] for d in devices.values())
        return {
            "status": "degraded" if degraded else "ok",
            "db_ok": db_ok,
            "devices": devices,
            "service_uptime_s": int((now_ms() - ctx.started_ms) / 1000),
            "version": __version__,
            # kept for older clients
            "ble": bms["link"],
            "last_frame_age_s": bms["age_s"],
        }

    @r.get("/system")
    def system():
        """Raspberry Pi health and link diagnostics for both devices."""
        out: dict = {"host": ctx.monitor.read(), "ble": None, "inverter": None}
        if ctx.bms is not None:
            st = ctx.bms.state
            b = st.ble
            out["ble"] = {
                "link": st.link_status,
                "rssi": b.rssi,
                "rssi_quality": rssi_quality(b.rssi),
                "rssi_source": b.rssi_source,
                "rssi_age_s": None if b.rssi_ts is None else round((now_ms() - b.rssi_ts) / 1000, 1),
                "recovery_stage": b.recovery_stage,
                "adapter_resets": b.adapter_resets,
                "last_adapter_reset_ts": b.last_adapter_reset_ts,
                "counters": vars(st.counters),
            }
        if ctx.inverter is not None:
            st = ctx.inverter.state
            out["inverter"] = {
                "link": st.link,
                "last_error": st.last_error,
                "port": getattr(ctx.inverter.bus, "port", None),
                "counters": vars(st.counters),
            }
        return out

    @r.get("/events")
    def events(
        limit: int = Query(100, ge=1, le=1000),
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        source: str | None = None,
    ):
        f = parse_time(from_, None, "from")
        t = parse_time(to, None, "to")
        if f is not None and t is not None and f > t:
            raise QueryError("'from' must be <= 'to'")
        if source is not None and source not in EVENT_SOURCES:
            raise QueryError(f"source must be one of {', '.join(EVENT_SOURCES)}")
        return {"events": ctx.store.events(limit, f, t, source)}

    @r.get("/overview")
    def overview():
        """Everything a glance view needs: SOC, power flows, link states."""
        b = bms_current(ctx)
        i = inverter_current(ctx)
        grid_v, grid_a = i.get("grid_voltage"), i.get("grid_current")
        bms_live = not b["stale"]
        return {
            "ts": now_ms(),
            "soc": b["soc"] if bms_live and b["soc"] is not None else i.get("soc"),
            "soc_source": "bms"
            if bms_live and b["soc"] is not None
            else ("inverter" if i.get("soc") is not None else None),
            "battery_power": b["power"] if bms_live else i.get("battery_power"),
            "pv_power": i.get("pv_power"),
            "load_power": i.get("load_power"),
            "grid_voltage": grid_v,
            "grid_power": round(grid_v * grid_a) if grid_v is not None and grid_a is not None else None,
            "machine_state": i.get("machine_state_label"),
            "bms": {k: b[k] for k in ("link", "stale", "age_s", "soc", "voltage", "current", "power", "rssi")},
            "inverter": {
                "link": i["link"],
                "stale": i["stale"],
                "age_s": i["age_s"],
                **{k: i.get(k) for k in ("soc", "battery_voltage", "battery_current", "battery_power")},
                "fault_codes": i.get("fault_codes", []),
            },
        }

    return r
