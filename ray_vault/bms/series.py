"""BMS time series: the `samples` / `samples_1m` tables described for core/timeseries.py."""

from __future__ import annotations

import json

from ..core.timeseries import Agg, Field, SeriesSpec
from .state import CellSnapshot

COLUMNS = (
    "ts",
    "voltage",
    "current",
    "power",
    "soc",
    "capacity_remain",
    "nominal_capacity",
    "cycle_count",
    "cycle_capacity",
    "avg_cell_voltage",
    "delta_cell_voltage",
    "min_cell_voltage",
    "max_cell_voltage",
    "balance_current",
    "temp_t1",
    "temp_t2",
    "temp_mos",
    "charge_on",
    "discharge_on",
    "balance_on",
    "balancing_action",
    "bms_uptime_s",
    "cell_voltages",
    "wire_resist",
)

SPEC = SeriesSpec(
    raw_table="samples",
    rollup_table="samples_1m",
    columns=COLUMNS,
    aggs=(
        Agg("voltage", "avg", "voltage_avg"),
        Agg("voltage", "min", "voltage_min"),
        Agg("voltage", "max", "voltage_max"),
        Agg("current", "avg", "current_avg"),
        Agg("current", "min", "current_min"),
        Agg("current", "max", "current_max"),
        Agg("power", "avg", "power_avg", digits=2),
        Agg("soc", "last", "soc_last"),
        Agg("temp_t1", "max", "temp_t1_max"),
        Agg("temp_t2", "max", "temp_t2_max"),
        Agg("temp_mos", "max", "temp_mos_max"),
        Agg("min_cell_voltage", "min", "cell_min"),
        Agg("max_cell_voltage", "max", "cell_max"),
        Agg("delta_cell_voltage", "max", "delta_cell_max"),
        Agg("balance_current", "avg", "balance_current_avg"),
        Agg("capacity_remain", "last", "capacity_remain_last"),
    ),
    fields=(
        Field("voltage", "voltage_avg", band=True),
        Field("current", "current_avg", band=True),
        Field("power", "power_avg", digits=2),
        Field("soc", "soc_last", digits=1),
        Field("temp_t1", "temp_t1_max"),
        Field("temp_t2", "temp_t2_max"),
        Field("temp_mos", "temp_mos_max"),
        Field("delta_cell_voltage", "delta_cell_max"),
        Field("min_cell_voltage", "cell_min"),
        Field("max_cell_voltage", "cell_max"),
        Field("balance_current", "balance_current_avg"),
        Field("capacity_remain", "capacity_remain_last"),
    ),
    default_fields=("voltage", "current", "power", "soc"),
    arrays={"cell_voltages": "cell_voltages_avg"},
    export_prefix={"cell_voltages": "cell", "wire_resist": "wire_resist"},
)


def sample_row(ts: int, snap: CellSnapshot) -> tuple:
    c = snap.cells
    return (
        ts,
        c.voltage,
        c.current,
        c.power,
        c.soc,
        c.capacity_remain,
        c.nominal_capacity,
        c.cycle_count,
        c.cycle_capacity,
        c.avg_cell_voltage,
        c.delta_cell_voltage,
        snap.min_cell_voltage,
        snap.max_cell_voltage,
        c.balance_current,
        c.temp_t1,
        c.temp_t2,
        c.temp_mos,
        int(c.charge_on),
        int(c.discharge_on),
        int(c.balance_on),
        c.balancing_action,
        c.bms_uptime_s,
        json.dumps(snap.cell_voltages),
        json.dumps(snap.wire_resist),
    )
