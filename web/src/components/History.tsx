"use client";

import { useMemo, useState } from "react";
import { Line } from "react-chartjs-2";

import { api, type HistoryPoint } from "@/lib/api";
import { baseTimeOptions, series, withGaps } from "@/lib/chartSetup";
import { useChartColors, type ChartColors } from "@/lib/hooks";
import { useHistory, type HistoryData } from "@/lib/queries";

import { ChartCard, HistoryFrame, NoData, useRangeSelection } from "./HistoryFrame";
import { Segmented } from "./ui";

/** BMS history: voltage/SOC, current/power, temperatures, per-cell voltages. */
export function History() {
  const colors = useChartColors();
  const range = useRangeSelection();
  const [cellMode, setCellMode] = useState<"cells" | "delta">("cells");
  const query = useHistory(range.sel);
  const data = query.data;

  return (
    <HistoryFrame
      title="Battery history"
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
      exportUrl={api.exportUrl}
    >
      <ChartCard title="Voltage & SOC">{data ? <VoltageSoc d={data} c={colors} /> : null}</ChartCard>
      <ChartCard title="Current & power">{data ? <CurrentPower d={data} c={colors} /> : null}</ChartCard>
      <ChartCard title="Temperatures">{data ? <Temps d={data} c={colors} /> : null}</ChartCard>
      <ChartCard
        title="Cells"
        extra={
          <Segmented
            label="Cell chart mode"
            value={cellMode}
            onChange={setCellMode}
            options={[
              { value: "cells", label: "Cells + Δ" },
              { value: "delta", label: "Δ only" },
            ]}
          />
        }
      >
        {data ? <Cells d={data} c={colors} mode={cellMode} /> : null}
      </ChartCard>
    </HistoryFrame>
  );
}

type ChartProps = { d: HistoryData; c: ChartColors };

function usePoints(d: HistoryData) {
  return useMemo(() => withGaps<HistoryPoint>(d.history.points, d.history.bucket_s), [d]);
}

function VoltageSoc({ d, c }: ChartProps) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          {
            label: "V max",
            data: series(pts, (p) => p.voltage_max),
            borderWidth: 0,
            backgroundColor: c.voltageBand,
            fill: "+1",
            yAxisID: "y",
          },
          {
            label: "V min",
            data: series(pts, (p) => p.voltage_min),
            borderWidth: 0,
            backgroundColor: c.voltageBand,
            fill: false,
            yAxisID: "y",
          },
          { label: "Voltage", data: series(pts, (p) => p.voltage), borderColor: c.voltage, yAxisID: "y" },
          { label: "SOC", data: series(pts, (p) => p.soc), borderColor: c.soc, yAxisID: "y1" },
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
        scales: {
          ...base.scales,
          y: {
            position: "left",
            ticks: { color: c.muted },
            grid: { color: c.grid },
            title: { display: true, text: "V", color: c.muted },
          },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            ticks: { color: c.muted },
            grid: { display: false },
            title: { display: true, text: "SOC %", color: c.muted },
          },
        },
      }}
    />
  );
}

function CurrentPower({ d, c }: ChartProps) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          { label: "Current", data: series(pts, (p) => p.current), borderColor: c.current, yAxisID: "y" },
          { label: "Power", data: series(pts, (p) => p.power), borderColor: c.power, yAxisID: "y1" },
        ],
      }}
      options={{
        ...base,
        scales: {
          ...base.scales,
          y: {
            position: "left",
            ticks: { color: c.muted },
            grid: { color: c.grid },
            title: { display: true, text: "A", color: c.muted },
          },
          y1: {
            position: "right",
            ticks: { color: c.muted },
            grid: { display: false },
            title: { display: true, text: "W", color: c.muted },
          },
        },
      }}
    />
  );
}

function Temps({ d, c }: ChartProps) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  return (
    <Line
      data={{
        datasets: [
          { label: "T1", data: series(pts, (p) => p.temp_t1), borderColor: c.t1 },
          { label: "T2", data: series(pts, (p) => p.temp_t2), borderColor: c.t2 },
          { label: "MOS", data: series(pts, (p) => p.temp_mos), borderColor: c.mos },
        ],
      }}
      options={{
        ...base,
        scales: {
          ...base.scales,
          y: {
            ticks: { color: c.muted },
            grid: { color: c.grid },
            title: { display: true, text: "°C", color: c.muted },
          },
        },
      }}
    />
  );
}

function cellColor(i: number, n: number, dark: boolean) {
  return `hsl(${Math.round((i * 360) / Math.max(n, 1))}, 65%, ${dark ? 65 : 45}%)`;
}

function Cells({ d, c, mode }: ChartProps & { mode: "cells" | "delta" }) {
  const deltaPts = usePoints(d);
  const cellPts = useMemo(() => withGaps(d.cells.points, d.cells.bucket_s), [d]);
  if (!d.history.points.length && !d.cells.points.length) return <NoData />;
  const base = baseTimeOptions(c, d.from, d.to);
  const n = d.cells.cell_count;
  const delta = {
    label: "Δ mV",
    data: series(deltaPts, (p) => (p.delta_cell_voltage == null ? null : p.delta_cell_voltage * 1000)),
    borderColor: c.delta,
    borderWidth: 2,
    yAxisID: mode === "cells" ? "y1" : "y",
  };
  const cellSets =
    mode === "cells"
      ? Array.from({ length: n }, (_, i) => ({
          label: `Cell ${i + 1}`,
          data: series(cellPts, (p) => p.cells[i]),
          borderColor: cellColor(i, n, c.scheme === "dark"),
          borderWidth: 1,
          yAxisID: "y",
        }))
      : [];
  return (
    <Line
      data={{ datasets: [...cellSets, delta] }}
      options={{
        ...base,
        plugins: {
          ...base.plugins,
          legend: { display: mode === "delta" || n <= 8, labels: base.plugins.legend.labels },
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              label: (item) =>
                `${item.dataset.label}: ${item.parsed.y === null ? "–" : item.dataset.label === "Δ mV" ? item.parsed.y.toFixed(0) : item.parsed.y.toFixed(3)}`,
            },
          },
        },
        scales:
          mode === "cells"
            ? {
                ...base.scales,
                y: {
                  position: "left",
                  ticks: { color: c.muted },
                  grid: { color: c.grid },
                  title: { display: true, text: "V", color: c.muted },
                },
                y1: {
                  position: "right",
                  min: 0,
                  ticks: { color: c.muted },
                  grid: { display: false },
                  title: { display: true, text: "Δ mV", color: c.muted },
                },
              }
            : {
                ...base.scales,
                y: {
                  min: 0,
                  ticks: { color: c.muted },
                  grid: { color: c.grid },
                  title: { display: true, text: "Δ mV", color: c.muted },
                },
              },
      }}
    />
  );
}
