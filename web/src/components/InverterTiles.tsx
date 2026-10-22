"use client";

import type { InverterCurrent } from "@/lib/api";
import { fmt } from "@/lib/format";

import { Tile, TileGrid, type Tone } from "./ui";

function watts(w: number | null | undefined): { value: string; unit: string } {
  if (w == null) return { value: "–", unit: "W" };
  return Math.abs(w) >= 1000 ? { value: fmt(w / 1000, 2), unit: "kW" } : { value: fmt(w, 0), unit: "W" };
}

export function InverterTiles({ c }: { c: InverterCurrent | null }) {
  const bw = c?.battery_power ?? null;
  const idle = bw === null || Math.abs(bw) < 10;
  const batteryTone: Tone = idle ? "" : bw > 0 ? "charge" : "discharge";
  const pv = watts(c?.pv_power);
  const load = watts(c?.load_power);
  const batt = watts(bw);
  const faults = c?.fault_codes ?? [];
  const loadPct = c?.load_percent ?? null;

  return (
    <TileGrid label="Inverter live values">
      <Tile
        label="Solar"
        value={pv.value}
        unit={pv.unit}
        tone={c?.pv_power ? "charge" : ""}
        sub={`${fmt(c?.pv_voltage, 1)} V · ${fmt(c?.pv_current, 1)} A`}
      />
      <Tile
        label="Load"
        value={load.value}
        unit={load.unit}
        meter={loadPct}
        tone={loadPct != null && loadPct >= 90 ? "bad" : loadPct != null && loadPct >= 75 ? "warn" : ""}
        sub={`${fmt(loadPct, 0)} % of rating · ${fmt(c?.load_apparent_power, 0)} VA`}
      />
      <Tile
        label="Battery"
        value={batt.value}
        unit={batt.unit}
        tone={batteryTone}
        sub={`${fmt(c?.battery_voltage, 1)} V · ${fmt(c?.battery_current, 1)} A · ${idle ? "idle" : bw! > 0 ? "charging" : "discharging"}`}
      />
      <Tile
        label="Inverter SOC"
        value={c?.soc == null ? "–" : String(c.soc)}
        unit="%"
        sub="as estimated by the inverter"
      />
      <Tile
        label="Grid"
        value={fmt(c?.grid_voltage, 1)}
        unit="V"
        sub={`${fmt(c?.grid_frequency, 2)} Hz · in ${fmt(c?.grid_current, 1)} A · charging ${fmt(c?.grid_charge_current, 1)} A`}
      />
      <Tile
        label="Output"
        value={fmt(c?.output_voltage, 1)}
        unit="V"
        sub={`${fmt(c?.output_frequency, 2)} Hz · ${fmt(c?.output_current, 1)} A`}
      />
      <Tile
        label="State"
        value={c?.machine_state_label ?? "–"}
        tone={faults.length ? "bad" : c?.machine_state_label === "Fault" ? "bad" : ""}
        sub={faults.length ? `Fault codes: ${faults.join(", ")}` : "No faults"}
      />
      <Tile label="Temperatures (°C)">
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm font-semibold tabular-nums">
          {(
            [
              ["DC-DC", c?.temp_dcdc],
              ["DC-AC", c?.temp_dcac],
              ["Sink C", c?.temp_transformer],
              ["Ambient", c?.temp_ambient],
            ] as const
          ).map(([k, v]) => (
            <span key={k}>
              <small className="block text-[11px] font-medium text-muted">{k}</small>
              {fmt(v, 1)}
            </span>
          ))}
        </div>
      </Tile>
    </TileGrid>
  );
}
