"use client";

import { useEffect, useMemo, useState } from "react";
import { Line } from "react-chartjs-2";

import { api, type HistoryPoint } from "@/lib/api";
import { baseTimeOptions, series, withGaps } from "@/lib/chartSetup";
import { useChartColors, type ChartColors } from "@/lib/hooks";
import { useHistory, type HistoryData } from "@/lib/queries";
import {
  DEFAULT_RANGE,
  EXPORT_MAX_SPAN,
  RANGE_KEYS,
  readHash,
  toLocalInput,
  windowFor,
  writeHash,
  type RangeKey,
  type RangeSel,
} from "@/lib/range";

import { Banner, buttonClass, Card, Empty, SectionHead, Segmented } from "./ui";

export function History() {
  const colors = useChartColors();
  const [sel, setSel] = useState<RangeSel | null>(null); // null until the URL hash is read on the client
  const [cellMode, setCellMode] = useState<"cells" | "delta">("cells");
  const [draft, setDraft] = useState<{ from: string; to: string }>({ from: "", to: "" });
  const query = useHistory(sel);
  const data = query.data;

  useEffect(() => {
    const init = readHash();
    setSel(init);
    const [f, t] = windowFor(init);
    setDraft({ from: toLocalInput(f), to: toLocalInput(t) });
    const onHash = () => setSel(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const choose = (next: RangeSel) => {
    writeHash(next);
    setSel(next);
  };

  const pickRange = (k: RangeKey) => {
    if (k !== "custom") return choose({ key: k });
    const [f, t] = windowFor(sel ?? DEFAULT_RANGE);
    setDraft({ from: toLocalInput(f), to: toLocalInput(t) });
    choose({ key: "custom", from: f, to: t });
  };

  const span = data ? data.to - data.from : 0;

  return (
    <Card>
      <SectionHead title="History">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label="Time range"
            value={sel?.key}
            onChange={pickRange}
            options={RANGE_KEYS.map((k) => ({ value: k, label: k }))}
          />
          {data ? (
            <a
              className={buttonClass}
              href={api.exportUrl(data.from, data.to)}
              aria-disabled={span > EXPORT_MAX_SPAN}
              title={span > EXPORT_MAX_SPAN ? "CSV export is limited to 31 days" : "Download raw samples as CSV"}
            >
              CSV
            </a>
          ) : null}
        </div>
      </SectionHead>

      {sel?.key === "custom" ? (
        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const from = new Date(draft.from).getTime();
            const to = new Date(draft.to).getTime();
            if (from > 0 && to > from) choose({ key: "custom", from, to });
          }}
        >
          <input
            type="datetime-local"
            className={buttonClass}
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
            aria-label="From"
          />
          <span className="text-xs text-muted">to</span>
          <input
            type="datetime-local"
            className={buttonClass}
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            aria-label="To"
          />
          <button className={buttonClass} type="submit">
            Apply
          </button>
        </form>
      ) : null}

      {query.isError ? (
        <div className="mb-3">
          <Banner tone="bad">Could not load history: {query.error.message}</Banner>
        </div>
      ) : null}

      <p className="mb-3 text-xs text-muted">
        {data
          ? `${data.history.points.length} points · ${data.history.bucket_s} s buckets from ${
              data.history.source === "samples" ? "raw samples" : "1-minute rollups"
            }`
          : "Loading…"}
        {query.isFetching && data ? " · refreshing…" : ""}
      </p>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,520px),1fr))] gap-4">
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
      </div>
    </Card>
  );
}

function ChartCard({ title, extra, children }: { title: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 flex items-center justify-between gap-2 text-sm font-semibold">
        {title}
        {extra}
      </h3>
      <div className="relative h-56 sm:h-64">{children ?? <Empty>Loading…</Empty>}</div>
    </div>
  );
}

function NoData() {
  return <Empty>No data in this range</Empty>;
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
          legend: { ...base.plugins.legend, labels: { ...base.plugins.legend.labels, filter: (i) => !i.text.startsWith("V m") } },
        },
        scales: {
          ...base.scales,
          y: { position: "left", ticks: { color: c.muted }, grid: { color: c.grid }, title: { display: true, text: "V", color: c.muted } },
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
          y: { position: "left", ticks: { color: c.muted }, grid: { color: c.grid }, title: { display: true, text: "A", color: c.muted } },
          y1: { position: "right", ticks: { color: c.muted }, grid: { display: false }, title: { display: true, text: "W", color: c.muted } },
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
          y: { ticks: { color: c.muted }, grid: { color: c.grid }, title: { display: true, text: "°C", color: c.muted } },
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
                y: { position: "left", ticks: { color: c.muted }, grid: { color: c.grid }, title: { display: true, text: "V", color: c.muted } },
                y1: { position: "right", min: 0, ticks: { color: c.muted }, grid: { display: false }, title: { display: true, text: "Δ mV", color: c.muted } },
              }
            : {
                ...base.scales,
                y: { min: 0, ticks: { color: c.muted }, grid: { color: c.grid }, title: { display: true, text: "Δ mV", color: c.muted } },
              },
      }}
    />
  );
}
