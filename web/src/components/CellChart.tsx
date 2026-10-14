"use client";

import { Bar } from "react-chartjs-2";

import type { Current } from "@/lib/api";
import "@/lib/chartSetup";
import { fmt } from "@/lib/format";
import { useChartColors } from "@/lib/hooks";

import { Card, Empty, SectionHead } from "./ui";

const CHART = "relative h-56 sm:h-64";

export function CellChart({ c }: { c: Current | null }) {
  const colors = useChartColors();
  const volts = c?.cell_voltages ?? [];
  const resist = c?.wire_resist ?? [];

  // Unused slots read 0.000 V when the BMS has not reported its cell count (spec §5.5).
  const cells = volts
    .map((v, i) => ({ n: i + 1, v, r: resist[i] ?? null }))
    .filter((x) => x.v > 0);

  if (!cells.length) {
    return (
      <Card>
        <SectionHead title="Cell voltages" />
        <div className={CHART}>
          <Empty>No cell data yet</Empty>
        </div>
      </Card>
    );
  }

  const vs = cells.map((x) => x.v);
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const lowIdx = c?.min_cell_index ?? cells[vs.indexOf(min)].n;
  const highIdx = c?.max_cell_index ?? cells[vs.indexOf(max)].n;
  const deltaMv = (c?.delta_cell_voltage ?? max - min) * 1000;

  const barColors = cells.map((x) =>
    x.n === lowIdx ? colors.cellLow : x.n === highIdx ? colors.cellHigh : colors.cellBar,
  );

  return (
    <Card>
      <SectionHead title="Cell voltages">
        <div className="flex flex-wrap gap-4 text-muted tabular-nums">
          <span>
            Low <b className="font-semibold text-cell-low">{fmt(min, 3)} V</b> (#{lowIdx})
          </span>
          <span>
            High <b className="font-semibold text-cell-high">{fmt(max, 3)} V</b> (#{highIdx})
          </span>
          <span>
            Δ <b className="font-semibold text-fg">{deltaMv.toFixed(0)} mV</b>
          </span>
        </div>
      </SectionHead>
      <div className={CHART}>
        <Bar
          data={{
            labels: cells.map((x) => String(x.n)),
            datasets: [
              {
                label: "Cell voltage",
                data: vs,
                backgroundColor: barColors,
                borderRadius: 3,
                maxBarThickness: 40,
              },
            ],
          }}
          options={{
            responsive: true,
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: colors.surface,
                titleColor: colors.text,
                bodyColor: colors.text,
                borderColor: colors.grid,
                borderWidth: 1,
                callbacks: {
                  title: (items) => `Cell ${items[0].label}`,
                  label: (item) => {
                    const cell = cells[item.dataIndex];
                    const lines = [`${cell.v.toFixed(3)} V`];
                    if (cell.r !== null) lines.push(`Wire resistance ${(cell.r * 1000).toFixed(0)} mΩ`);
                    return lines;
                  },
                },
              },
            },
            scales: {
              x: { ticks: { color: colors.muted }, grid: { display: false } },
              y: {
                min: Math.floor((min - 0.02) * 100) / 100,
                max: Math.ceil((max + 0.02) * 100) / 100,
                ticks: { color: colors.muted, callback: (v) => Number(v).toFixed(2) },
                grid: { color: colors.grid },
                title: { display: true, text: "V", color: colors.muted },
              },
            },
          }}
        />
      </div>
    </Card>
  );
}
