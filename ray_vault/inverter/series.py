"""Inverter time series: the `inverter_samples` / `inverter_samples_1m` tables (created by migration 2)."""

from __future__ import annotations

from ..core.timeseries import Agg, Field, SeriesSpec

COLUMNS = (
    "ts",
    "battery_voltage",
    "battery_current",
    "battery_power",
    "soc",
    "pv_voltage",
    "pv_current",
    "pv_power",
    "grid_voltage",
    "grid_current",
    "grid_frequency",
    "grid_charge_current",
    "output_voltage",
    "output_current",
    "output_frequency",
    "load_current",
    "load_power",
    "load_apparent_power",
    "load_percent",
    "temp_dcdc",
    "temp_dcac",
    "temp_transformer",
    "temp_ambient",
    "machine_state",
    "fault_bits",
)

SPEC = SeriesSpec(
    raw_table="inverter_samples",
    rollup_table="inverter_samples_1m",
    columns=COLUMNS,
    aggs=(
        Agg("battery_voltage", "avg", "battery_voltage_avg"),
        Agg("battery_voltage", "min", "battery_voltage_min"),
        Agg("battery_voltage", "max", "battery_voltage_max"),
        Agg("battery_current", "avg", "battery_current_avg"),
        Agg("battery_power", "avg", "battery_power_avg", digits=1),
        Agg("soc", "last", "soc_last"),
        Agg("pv_voltage", "avg", "pv_voltage_avg", digits=1),
        Agg("pv_power", "avg", "pv_power_avg", digits=1),
        Agg("pv_power", "min", "pv_power_min"),
        Agg("pv_power", "max", "pv_power_max"),
        Agg("grid_voltage", "avg", "grid_voltage_avg", digits=1),
        Agg("grid_frequency", "avg", "grid_frequency_avg", digits=2),
        Agg("grid_charge_current", "avg", "grid_charge_current_avg", digits=2),
        Agg("output_voltage", "avg", "output_voltage_avg", digits=1),
        Agg("load_power", "avg", "load_power_avg", digits=1),
        Agg("load_power", "min", "load_power_min"),
        Agg("load_power", "max", "load_power_max"),
        Agg("load_percent", "avg", "load_percent_avg", digits=1),
        Agg("temp_dcdc", "max", "temp_dcdc_max"),
        Agg("temp_dcac", "max", "temp_dcac_max"),
        Agg("temp_transformer", "max", "temp_transformer_max"),
        Agg("temp_ambient", "max", "temp_ambient_max"),
    ),
    fields=(
        Field("battery_voltage", "battery_voltage_avg", digits=2, band=True),
        Field("battery_current", "battery_current_avg", digits=2),
        Field("battery_power", "battery_power_avg", digits=1),
        Field("soc", "soc_last", digits=1),
        Field("pv_voltage", "pv_voltage_avg", digits=1),
        Field("pv_power", "pv_power_avg", digits=1, band=True),
        Field("grid_voltage", "grid_voltage_avg", digits=1),
        Field("grid_frequency", "grid_frequency_avg", digits=2),
        Field("grid_charge_current", "grid_charge_current_avg", digits=2),
        Field("output_voltage", "output_voltage_avg", digits=1),
        Field("load_power", "load_power_avg", digits=1, band=True),
        Field("load_percent", "load_percent_avg", digits=1),
        Field("temp_dcdc", "temp_dcdc_max", digits=1),
        Field("temp_dcac", "temp_dcac_max", digits=1),
        Field("temp_transformer", "temp_transformer_max", digits=1),
        Field("temp_ambient", "temp_ambient_max", digits=1),
    ),
    default_fields=("pv_power", "load_power", "battery_power", "soc"),
)


def sample_row(ts: int, live: dict) -> tuple:
    return (ts, *(live.get(c) for c in COLUMNS[1:]))
