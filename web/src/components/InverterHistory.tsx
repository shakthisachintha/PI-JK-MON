"use client";

import { useMemo } from "react";
import { Line } from "react-chartjs-2";

import { api, type InverterHistoryPoint } from "@/lib/api";
import { baseTimeOptions, series, withGaps } from "@/lib/chartSetup";
import { useChartColors, type ChartColors } from "@/lib/hooks";
import { useInverterHistory, type InverterHistoryData } from "@/lib/queries";

import { ChartCard, HistoryFrame, NoData, useRangeSelection } from "./HistoryFrame";

type Props = { d: InverterHistoryData; c: ChartColors };

export function InverterHistory() {
  const colors = useChartColors();
  const range = useRangeSelection();
  const query = useInverterHistory(range.sel);
  const data = query.data;

  return (
    <HistoryFrame
      title="Inverter history"
      range={range}
      data={
        data && {
          from: data.from,
          to: data.to,
          meta: { points: data.history.points.length, bucket_s: data.history.bucket_s, source: data.history.source },
        }
      }
      error={query.error}
      fetching={query.isFetching}
      exportUrl={api.inverterExportUrl}
    >
      <ChartCard title="Power">{data ? <Power d={data} c={colors} /> : null}</ChartCard>
      <ChartCard title="Battery">{data ? <Battery d={data} c={colors} /> : null}</ChartCard>
      <ChartCard title="Grid">{data ? <Grid d={data} c={colors} /> : null}</ChartCard>
      <ChartCard title="Temperatures">{data ? <Temps d={data} c={colors} /> : null}</ChartCard>
    </HistoryFrame>
  );
}

function usePoints(d: InverterHistoryData) {
  return useMemo(() => withGaps<InverterHistoryPoint>(d.history.points, d.history.bucket_s), [d]);
}

const axis = (c: ChartColors, text: string, position: "left" | "right" = "left", grid = true) => ({
  position,
  ticks: { color: c.muted },
  grid: grid ? { color: c.grid } : { display: false },
  title: { display: true, text, color: c.muted },
});

function Power({ d, c }: Props) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          {
            label: "Solar",
            data: series(pts, (p) => p.pv_power),
            borderColor: c.soc,
            backgroundColor: c.soc + "22",
            fill: "origin",
          },
          { label: "Load", data: series(pts, (p) => p.load_power), borderColor: c.power },
          { label: "Battery (+ charging)", data: series(pts, (p) => p.battery_power), borderColor: c.current },
        ],
      }}
      options={{ ...base, scales: { ...base.scales, y: axis(c, "W") } }}
    />
  );
}

function Battery({ d, c }: Props) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          {
            label: "V max",
            data: series(pts, (p) => p.battery_voltage_max),
            borderWidth: 0,
            backgroundColor: c.voltageBand,
            fill: "+1",
          },
          {
            label: "V min",
            data: series(pts, (p) => p.battery_voltage_min),
            borderWidth: 0,
            backgroundColor: c.voltageBand,
            fill: false,
          },
          { label: "Voltage", data: series(pts, (p) => p.battery_voltage), borderColor: c.voltage, yAxisID: "y" },
          { label: "Inverter SOC", data: series(pts, (p) => p.soc), borderColor: c.soc, yAxisID: "y1" },
        ],
      }}
      options={{
        ...base,
        plugins: {
          ...base.plugins,
          legend: {
            ...base.plugins.legend,
            labels: { ...base.plugins.legend.labels, filter: (i) => !i.text.startsWith("V m") },
          },
        },
        scales: { ...base.scales, y: axis(c, "V"), y1: { ...axis(c, "SOC %", "right", false), min: 0, max: 100 } },
      }}
    />
  );
}

function Grid({ d, c }: Props) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          { label: "Voltage", data: series(pts, (p) => p.grid_voltage), borderColor: c.voltage, yAxisID: "y" },
          { label: "Frequency", data: series(pts, (p) => p.grid_frequency), borderColor: c.delta, yAxisID: "y1" },
        ],
      }}
      options={{ ...base, scales: { ...base.scales, y: axis(c, "V"), y1: axis(c, "Hz", "right", false) } }}
    />
  );
}

function Temps({ d, c }: Props) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          { label: "DC-DC", data: series(pts, (p) => p.temp_dcdc), borderColor: c.t1 },
          { label: "DC-AC", data: series(pts, (p) => p.temp_dcac), borderColor: c.mos },
          { label: "Ambient", data: series(pts, (p) => p.temp_ambient), borderColor: c.t2 },
        ],
      }}
      options={{ ...base, scales: { ...base.scales, y: axis(c, "°C") } }}
    />
  );
}
