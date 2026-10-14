"use client";

import type { DeviceResponse, SettingsResponse } from "@/lib/api";
import { fmtTime, humanize } from "@/lib/format";

// Units for settings fields (spec §5.5).
const SETTING_UNITS: Record<string, string> = {
  cell_uvp: "V",
  cell_uvpr: "V",
  cell_ovp: "V",
  cell_ovpr: "V",
  balance_trigger_voltage: "V",
  power_off_voltage: "V",
  max_charge_current: "A",
  charge_ocp_delay: "s",
  charge_ocp_recovery: "s",
  max_discharge_current: "A",
  discharge_ocp_delay: "s",
  discharge_ocp_recovery: "s",
  short_circuit_recovery: "s",
  max_balance_current: "A",
  charge_otp: "°C",
  charge_otpr: "°C",
  discharge_otp: "°C",
  discharge_otpr: "°C",
  charge_utp: "°C",
  charge_utpr: "°C",
  mos_otp: "°C",
  mos_otpr: "°C",
  total_battery_capacity: "Ah",
  short_circuit_delay: "µs",
  balance_starting_voltage: "V",
};

const SWITCHES = new Set(["charge_switch", "discharge_switch", "balancer_switch"]);

function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv num">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SettingsDevice({ settings, device }: { settings: SettingsResponse | null; device: DeviceResponse | null }) {
  const s = settings?.settings;
  const d = device?.device;
  return (
    <>
      <details className="card">
        <summary>
          <h2>BMS settings</h2>
          {settings?.ts ? <span className="meta">read {fmtTime(settings.ts)}</span> : null}
        </summary>
        {s ? (
          <KV
            rows={Object.entries(s).map(([k, v]) => [
              humanize(k),
              SWITCHES.has(k) ? (v ? "On" : "Off") : `${v}${SETTING_UNITS[k] ? ` ${SETTING_UNITS[k]}` : ""}`,
            ])}
          />
        ) : (
          <p className="meta">No settings received yet</p>
        )}
      </details>
      <details className="card">
        <summary>
          <h2>Device</h2>
          {device?.ts ? <span className="meta">read {fmtTime(device.ts)}</span> : null}
        </summary>
        {d ? (
          <KV rows={Object.entries(d).map(([k, v]) => [humanize(k), String(v)])} />
        ) : (
          <p className="meta">No device info received yet</p>
        )}
      </details>
    </>
  );
}
