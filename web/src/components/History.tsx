"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Line } from "react-chartjs-2";

import { api, type CellHistory, type History as HistoryData, type HistoryPoint } from "@/lib/api";
import { baseTimeOptions, series, withGaps } from "@/lib/chartSetup";
import { usePolling, useChartColors, type ChartColors } from "@/lib/hooks";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const PRESETS = { "1h": HOUR, "6h": 6 * HOUR, "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY } as const;
type Preset = keyof typeof PRESETS;
type RangeKey = Preset | "custom";
const RANGE_KEYS: RangeKey[] = ["1h", "6h", "24h", "7d", "30d", "custom"];
const EXPORT_MAX_SPAN = 31 * DAY;

interface RangeSel {
  key: RangeKey;
  from?: number; // custom only
  to?: number;
}

// --- URL hash: #range=24h or #range=custom&from=<ms>&to=<ms> ---

function readHash(): RangeSel {
  const h = new URLSearchParams(window.location.hash.slice(1));
  const key = h.get("range") as RangeKey | null;
  if (key === "custom") {
    const from = Number(h.get("from"));
    const to = Number(h.get("to"));
    if (from > 0 && to > from) return { key, from, to };
  } else if (key && key in PRESETS) {
    return { key };
  }
  return { key: "24h" };
}

function writeHash(sel: RangeSel) {
  const h = new URLSearchParams({ range: sel.key });
  if (sel.key === "custom" && sel.from && sel.to) {
    h.set("from", String(sel.from));
    h.set("to", String(sel.to));
  }
  history.replaceState(null, "", `#${h.toString()}`);
}

function windowFor(sel: RangeSel): [number, number] {
  if (sel.key === "custom" && sel.from && sel.to) return [sel.from, sel.to];
  const now = Date.now();
  return [now - PRESETS[sel.key as Preset], now];
}

function toLocalInput(ms: number): string {
  const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
  return d.toISOString().slice(0, 16);
}

// --- component ---

interface Loaded {
  from: number;
  to: number;
  history: HistoryData;
  cells: CellHistory;
}

export function History() {
  const colors = useChartColors();
  const [sel, setSel] = useState<RangeSel | null>(null);
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cellMode, setCellMode] = useState<"cells" | "delta">("cells");
  const [draft, setDraft] = useState<{ from: string; to: string }>({ from: "", to: "" });

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

  const load = useCallback(async () => {
    if (!sel) return;
    const [from, to] = windowFor(sel);
    setLoading(true);
    try {
      const [history, cells] = await Promise.all([api.history(from, to), api.cellHistory(from, to)]);
      setData({ from, to, history, cells });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sel]);

  // Refresh every 60 s for live ranges up to 24 h (spec §9.2).
  const live = sel !== null && sel.key !== "custom" && PRESETS[sel.key as Preset] <= DAY;
  usePolling(load, live ? 60_000 : null, [load]);

  const span = data ? data.to - data.from : 0;

  return (
    <section className="card">
      <div className="section-head">
        <h2>History</h2>
        <div className="controls">
          <div className="segmented" role="group" aria-label="Time range">
            {RANGE_KEYS.map((k) => (
              <button
                key={k}
                aria-pressed={sel?.key === k}
                onClick={() => {
                  if (k === "custom") {
                    const [f, t] = sel ? windowFor(sel) : windowFor({ key: "24h" });
                    setDraft({ from: toLocalInput(f), to: toLocalInput(t) });
                    choose({ key: "custom", from: f, to: t });
                  } else {
                    choose({ key: k });
                  }
                }}
              >
                {k}
              </button>
            ))}
          </div>
          {data ? (
            <a
              className="btn"
              href={api.exportUrl(data.from, data.to)}
              aria-disabled={span > EXPORT_MAX_SPAN}
              title={span > EXPORT_MAX_SPAN ? "CSV export is limited to 31 days" : "Download raw samples as CSV"}
            >
              CSV
            </a>
          ) : null}
        </div>
      </div>

      {sel?.key === "custom" ? (
        <form
          className="custom-range"
          onSubmit={(e) => {
            e.preventDefault();
            const from = new Date(draft.from).getTime();
            const to = new Date(draft.to).getTime();
            if (from > 0 && to > from) choose({ key: "custom", from, to });
          }}
        >
          <input
            type="datetime-local"
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
            aria-label="From"
          />
          <span className="meta">to</span>
          <input
            type="datetime-local"
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            aria-label="To"
          />
          <button className="btn" type="submit">
            Apply
          </button>
        </form>
      ) : null}

      {error ? <div className="banner error">Could not load history: {error}</div> : null}

      <p className="meta" style={{ margin: "0 0 12px" }}>
        {data
          ? `${data.history.points.length} points · ${data.history.bucket_s} s buckets from ${
              data.history.source === "samples" ? "raw samples" : "1-minute rollups"
            }`
          : "Loading…"}
        {loading && data ? " · refreshing…" : ""}
      </p>

      <div className="chart-grid">
        <ChartCard title="Voltage & SOC">
          {data ? <VoltageSoc d={data} c={colors} /> : null}
        </ChartCard>
        <ChartCard title="Current & power">
          {data ? <CurrentPower d={data} c={colors} /> : null}
        </ChartCard>
        <ChartCard title="Temperatures">{data ? <Temps d={data} c={colors} /> : null}</ChartCard>
        <ChartCard
          title="Cells"
          extra={
            <div className="segmented" role="group" aria-label="Cell chart mode">
              <button aria-pressed={cellMode === "cells"} onClick={() => setCellMode("cells")}>
                Cells + Δ
              </button>
              <button aria-pressed={cellMode === "delta"} onClick={() => setCellMode("delta")}>
                Δ only
              </button>
            </div>
          }
        >
          {data ? <Cells d={data} c={colors} mode={cellMode} /> : null}
        </ChartCard>
      </div>
    </section>
  );
}

function ChartCard({ title, extra, children }: { title: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="chart-card">
      <h3>
        {title}
        {extra}
      </h3>
      <div className="chart">{children ?? <div className="empty">Loading…</div>}</div>
    </div>
  );
}

function Empty() {
  return <div className="empty">No data in this range</div>;
}

type ChartProps = { d: Loaded; c: ChartColors };

function usePoints(d: Loaded) {
  return useMemo(() => withGaps<HistoryPoint>(d.history.points, d.history.bucket_s), [d]);
}

function VoltageSoc({ d, c }: ChartProps) {
  const pts = usePoints(d);
  if (!d.history.points.length) return <Empty />;
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
  if (!d.history.points.length) return <Empty />;
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
  if (!d.history.points.length) return <Empty />;
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
  if (!d.history.points.length && !d.cells.points.length) return <Empty />;
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
