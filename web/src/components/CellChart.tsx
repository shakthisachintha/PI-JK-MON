"use client";

import { Bar } from "react-chartjs-2";

import type { Current } from "@/lib/api";
import "@/lib/chartSetup";
import { fmt } from "@/lib/format";
import { useChartColors } from "@/lib/hooks";

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
      <section className="card">
        <div className="section-head">
          <h2>Cell voltages</h2>
        </div>
        <div className="chart">
          <div className="empty">No cell data yet</div>
        </div>
      </section>
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
    <section className="card">
      <div className="section-head">
        <h2>Cell voltages</h2>
        <div className="cell-stats num">
          <span className="low">
            Low <b>{fmt(min, 3)} V</b> (#{lowIdx})
          </span>
          <span className="high">
            High <b>{fmt(max, 3)} V</b> (#{highIdx})
          </span>
          <span>
            Δ <b>{deltaMv.toFixed(0)} mV</b>
          </span>
        </div>
      </div>
      <div className="chart">
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
    </section>
  );
}
