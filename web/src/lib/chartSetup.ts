"use client";

import {
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  TimeScale,
  Tooltip,
} from "chart.js";
import "chartjs-adapter-date-fns";

Chart.register(BarElement, CategoryScale, Filler, Legend, LinearScale, LineElement, PointElement, TimeScale, Tooltip);

Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.animation = false;
Chart.defaults.maintainAspectRatio = false;

export interface ChartTheme {
  text: string;
  muted: string;
  grid: string;
  surface: string;
}

/** Insert null points where spacing exceeds 2 × bucket, so lines break at gaps (spec §8.2). */
export function withGaps<T extends { ts: number }>(points: T[], bucketS: number): (T | { ts: number; gap: true })[] {
  const out: (T | { ts: number; gap: true })[] = [];
  const limit = 2 * bucketS * 1000;
  for (let i = 0; i < points.length; i++) {
    if (i > 0 && points[i].ts - points[i - 1].ts > limit) {
      out.push({ ts: points[i - 1].ts + bucketS * 1000, gap: true });
    }
    out.push(points[i]);
  }
  return out;
}

export function series<T extends { ts: number }>(
  points: (T | { ts: number; gap: true })[],
  pick: (p: T) => number | null | undefined,
): { x: number; y: number | null }[] {
  return points.map((p) => ({ x: p.ts, y: "gap" in p ? null : (pick(p as T) ?? null) }));
}

export function baseTimeOptions(theme: ChartTheme, from: number, to: number) {
  return {
    responsive: true,
    parsing: false as const,
    normalized: true,
    spanGaps: false,
    interaction: { mode: "index" as const, intersect: false },
    elements: { point: { radius: 0, hitRadius: 6 }, line: { borderWidth: 1.5, tension: 0 } },
    plugins: {
      legend: { labels: { color: theme.text, boxWidth: 12, boxHeight: 2 } },
      tooltip: {
        backgroundColor: theme.surface,
        titleColor: theme.text,
        bodyColor: theme.text,
        borderColor: theme.grid,
        borderWidth: 1,
      },
    },
    scales: {
      x: {
        type: "time" as const,
        min: from,
        max: to,
        ticks: { color: theme.muted, maxRotation: 0, autoSkipPadding: 16 },
        grid: { color: theme.grid },
        time: {
          tooltipFormat: "PP HH:mm:ss",
          displayFormats: { minute: "HH:mm", hour: "HH:mm", day: "MMM d" },
        },
      },
    },
  };
}
