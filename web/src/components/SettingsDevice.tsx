"use client";

import { fmtTime, humanize } from "@/lib/format";
import { useDevice, useSettings } from "@/lib/queries";

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

function Collapsible({ title, ts, children }: { title: string; ts?: number | null; children: React.ReactNode }) {
  return (
    <details className="group rounded-xl border border-border bg-surface p-4">
      <summary className="flex cursor-pointer list-none items-center gap-3 group-open:mb-3 [&::-webkit-details-marker]:hidden">
        <h2 className="text-[13px] font-semibold tracking-wide text-muted uppercase">{title}</h2>
        {ts ? <span className="ml-auto text-xs text-muted">read {fmtTime(ts)}</span> : <span className="ml-auto" />}
        <span aria-hidden className="text-muted transition-transform group-open:rotate-90">
          ▸
        </span>
      </summary>
      {children}
    </details>
  );
}

function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-6 tabular-nums">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b border-border py-1.5">
          <dt className="text-muted">{k}</dt>
          <dd className="text-right font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SettingsDevice() {
  const settings = useSettings().data;
  const device = useDevice().data;
  const s = settings?.settings;
  const d = device?.device;
  return (
    <>
      <Collapsible title="BMS settings" ts={settings?.ts}>
        {s ? (
          <KV
            rows={Object.entries(s).map(([k, v]) => [
              humanize(k),
              SWITCHES.has(k) ? (v ? "On" : "Off") : `${v}${SETTING_UNITS[k] ? ` ${SETTING_UNITS[k]}` : ""}`,
            ])}
          />
        ) : (
          <p className="text-xs text-muted">No settings received yet</p>
        )}
      </Collapsible>
      <Collapsible title="Device" ts={device?.ts}>
        {d ? (
          <KV rows={Object.entries(d).map(([k, v]) => [humanize(k), String(v)])} />
        ) : (
          <p className="text-xs text-muted">No device info received yet</p>
        )}
      </Collapsible>
    </>
  );
}
