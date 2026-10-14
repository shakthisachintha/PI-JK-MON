"use client";

import type { Current } from "@/lib/api";
import { fmt, fmtDuration } from "@/lib/format";

function Tile({
  label,
  value,
  unit,
  sub,
  className = "",
  valueClass = "",
}: {
  label: string;
  value: string;
  unit?: string;
  sub?: React.ReactNode;
  className?: string;
  valueClass?: string;
}) {
  return (
    <div className={`tile ${className}`}>
      <div className="label">{label}</div>
      <div className={`value num ${valueClass}`}>
        {value}
        {unit && value !== "–" ? <span className="unit">{unit}</span> : null}
      </div>
      {sub ? <div className="sub num">{sub}</div> : null}
    </div>
  );
}

function Switch({ label, on }: { label: string; on: boolean | null }) {
  return (
    <div className="switch">
      <span>{label}</span>
      <span className={`pill ${on ? "on" : ""}`}>{on === null ? "–" : on ? "ON" : "OFF"}</span>
    </div>
  );
}

const BALANCING = ["Idle", "Charge balancing", "Discharge balancing"];

export function LiveTiles({ c }: { c: Current | null }) {
  const soc = c?.soc ?? null;
  const flow = c?.current ?? null;
  const flowClass = flow === null || Math.abs(flow) < 0.05 ? "" : flow > 0 ? "charging" : "discharging";
  const flowLabel = flow === null || Math.abs(flow) < 0.05 ? "Idle" : flow > 0 ? "Charging" : "Discharging";
  const level = soc === null ? "" : soc < 10 ? "critical" : soc < 25 ? "low" : "";

  return (
    <section className="tiles" aria-label="Live values">
      <div className="tile soc">
        <div className="label">State of charge</div>
        <div className="value num">
          {soc === null ? "–" : soc}
          {soc !== null ? <span className="unit">%</span> : null}
        </div>
        <div className={`capbar ${level}`} role="meter" aria-valuenow={soc ?? 0} aria-valuemin={0} aria-valuemax={100}>
          <div style={{ width: `${Math.max(0, Math.min(100, soc ?? 0))}%` }} />
        </div>
        <div className="sub num">
          {fmt(c?.capacity_remain, 1)} / {fmt(c?.nominal_capacity, 0)} Ah remaining
        </div>
      </div>

      <Tile label="Pack voltage" value={fmt(c?.voltage, 2)} unit="V" sub={`avg cell ${fmt(c?.avg_cell_voltage, 3)} V`} />
      <Tile label="Current" value={fmt(flow, 2)} unit="A" valueClass={flowClass} sub={flowLabel} />
      <Tile
        label="Power"
        value={c?.power === null || c?.power === undefined ? "–" : Math.abs(c.power) >= 1000 ? fmt(c.power / 1000, 2) : fmt(c.power, 0)}
        unit={c?.power !== null && c?.power !== undefined && Math.abs(c.power) >= 1000 ? "kW" : "W"}
        valueClass={flowClass}
      />
      <Tile
        label="Cell spread"
        value={c?.delta_cell_voltage === null || c?.delta_cell_voltage === undefined ? "–" : (c.delta_cell_voltage * 1000).toFixed(0)}
        unit="mV"
        sub={`${fmt(c?.min_cell_voltage, 3)} – ${fmt(c?.max_cell_voltage, 3)} V`}
      />

      <div className="tile">
        <div className="label">Temperatures (°C)</div>
        <div className="temps num">
          <span>
            <small>T1</small>
            {fmt(c?.temp_t1, 1)}
          </span>
          <span>
            <small>T2</small>
            {fmt(c?.temp_t2, 1)}
          </span>
          <span>
            <small>MOS</small>
            {fmt(c?.temp_mos, 1)}
          </span>
        </div>
      </div>

      <div className="tile">
        <div className="label">Switches</div>
        <div className="switches">
          <Switch label="Charge" on={c?.charge_on ?? null} />
          <Switch label="Discharge" on={c?.discharge_on ?? null} />
          <Switch label="Balance" on={c?.balance_on ?? null} />
        </div>
      </div>

      <Tile
        label="Balancer"
        value={fmt(c?.balance_current, 3)}
        unit="A"
        sub={c?.balancing_action !== null && c?.balancing_action !== undefined ? BALANCING[c.balancing_action] ?? `Mode ${c.balancing_action}` : "–"}
      />
      <Tile
        label="Cycles"
        value={c?.cycle_count === null || c?.cycle_count === undefined ? "–" : String(c.cycle_count)}
        sub={`${fmt(c?.cycle_capacity, 0)} Ah total · up ${fmtDuration(c?.bms_uptime_s)}`}
      />
    </section>
  );
}
