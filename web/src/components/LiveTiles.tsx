"use client";

import type { Current } from "@/lib/api";
import { fmt, fmtDuration } from "@/lib/format";

import { cx, Meter, Tile, TileGrid, type Tone } from "./ui";

function Switch({ label, on }: { label: string; on: boolean | null }) {
  return (
    <div className="flex justify-between gap-2">
      <span>{label}</span>
      <span
        className={cx(
          "rounded-full border px-2 text-[11px] font-semibold",
          on ? "border-ok/35 bg-ok/15 text-ok" : "border-border bg-surface-2 text-muted",
        )}
      >
        {on === null ? "–" : on ? "ON" : "OFF"}
      </span>
    </div>
  );
}

const BALANCING = ["Idle", "Charge balancing", "Discharge balancing"];

export function LiveTiles({ c }: { c: Current | null }) {
  const soc = c?.soc ?? null;
  const flow = c?.current ?? null;
  const idle = flow === null || Math.abs(flow) < 0.05;
  const flowTone: Tone = idle ? "" : flow > 0 ? "charge" : "discharge";
  const flowLabel = idle ? "Idle" : flow > 0 ? "Charging" : "Discharging";
  const socTone = soc === null ? "ok" : soc < 10 ? "bad" : soc < 25 ? "warn" : "ok";
  const power = c?.power ?? null;
  const kw = power !== null && Math.abs(power) >= 1000;

  return (
    <TileGrid label="Live values">
      <Tile label="State of charge" className="col-span-2">
        <div className="text-[44px] leading-tight font-semibold tabular-nums">
          {soc ?? "–"}
          {soc !== null ? <span className="ml-1 text-base font-medium text-muted">%</span> : null}
        </div>
        <Meter percent={soc ?? 0} tone={socTone} className="mt-2.5" />
        <div className="mt-1 text-xs text-muted tabular-nums">
          {fmt(c?.capacity_remain, 1)} / {fmt(c?.nominal_capacity, 0)} Ah remaining
        </div>
      </Tile>

      <Tile
        label="Pack voltage"
        value={fmt(c?.voltage, 2)}
        unit="V"
        sub={`avg cell ${fmt(c?.avg_cell_voltage, 3)} V`}
      />
      <Tile label="Current" value={fmt(flow, 2)} unit="A" tone={flowTone} sub={flowLabel} />
      <Tile
        label="Power"
        value={power === null ? "–" : kw ? fmt(power / 1000, 2) : fmt(power, 0)}
        unit={kw ? "kW" : "W"}
        tone={flowTone}
      />
      <Tile
        label="Cell spread"
        value={c?.delta_cell_voltage == null ? "–" : (c.delta_cell_voltage * 1000).toFixed(0)}
        unit="mV"
        sub={`${fmt(c?.min_cell_voltage, 3)} – ${fmt(c?.max_cell_voltage, 3)} V`}
      />

      <Tile label="Temperatures (°C)">
        <div className="flex gap-3 text-lg font-semibold tabular-nums">
          {(
            [
              ["T1", c?.temp_t1],
              ["T2", c?.temp_t2],
              ["MOS", c?.temp_mos],
            ] as const
          ).map(([k, v]) => (
            <span key={k}>
              <small className="block text-[11px] font-medium text-muted">{k}</small>
              {fmt(v, 1)}
            </span>
          ))}
        </div>
      </Tile>

      <Tile label="Switches">
        <div className="flex flex-col gap-1 text-[13px]">
          <Switch label="Charge" on={c?.charge_on ?? null} />
          <Switch label="Discharge" on={c?.discharge_on ?? null} />
          <Switch label="Balance" on={c?.balance_on ?? null} />
        </div>
      </Tile>

      <Tile
        label="Balancer"
        value={fmt(c?.balance_current, 3)}
        unit="A"
        sub={c?.balancing_action != null ? (BALANCING[c.balancing_action] ?? `Mode ${c.balancing_action}`) : "–"}
      />
      <Tile
        label="Cycles"
        value={c?.cycle_count == null ? "–" : String(c.cycle_count)}
        sub={`${fmt(c?.cycle_capacity, 0)} Ah total · up ${fmtDuration(c?.bms_uptime_s)}`}
      />
    </TileGrid>
  );
}
