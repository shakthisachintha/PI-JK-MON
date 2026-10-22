"use client";

import Link from "next/link";
import { useMemo } from "react";
import { Line } from "react-chartjs-2";

import { EventsTable } from "@/components/EventsTable";
import { formatSetting } from "@/components/InverterControls";
import { SignalBars } from "@/components/SignalBars";
import { Banner, Card, Empty, Meter, PageMain, SectionHead, Tile, TileGrid, type Tone } from "@/components/ui";
import type { InverterHistoryPoint, SettingItem } from "@/lib/api";
import { baseTimeOptions, series, withGaps } from "@/lib/chartSetup";
import { fmt } from "@/lib/format";
import { useChartColors } from "@/lib/hooks";
import { useInverterHistory, useInverterSettings, useOverview } from "@/lib/queries";
import type { RangeSel } from "@/lib/range";

const LAST_24H: RangeSel = { key: "24h" };

function watts(w: number | null | undefined): { value: string; unit: string } {
  if (w == null) return { value: "–", unit: "W" };
  return Math.abs(w) >= 1000 ? { value: fmt(w / 1000, 2), unit: "kW" } : { value: fmt(w, 0), unit: "W" };
}

function PowerToday() {
  const c = useChartColors();
  const { data } = useInverterHistory(LAST_24H);
  const pts = useMemo(
    () => (data ? withGaps<InverterHistoryPoint>(data.history.points, data.history.bucket_s) : []),
    [data],
  );
  if (!data) return <Empty>Loading…</Empty>;
  if (!data.history.points.length) return <Empty>No inverter data in the last 24 h</Empty>;
  const base = baseTimeOptions(c, data.from, data.to);
  return (
    <Line
      data={{
        datasets: [
          {
            label: "Solar",
            data: series(pts, (p) => p.pv_power),
            borderColor: c.soc,
            backgroundColor: c.soc + "22",
            fill: "origin",
          },
          { label: "Load", data: series(pts, (p) => p.load_power), borderColor: c.power },
          { label: "Battery (+ charging)", data: series(pts, (p) => p.battery_power), borderColor: c.current },
        ],
      }}
      options={{
        ...base,
        scales: {
          ...base.scales,
          y: {
            ticks: { color: c.muted },
            grid: { color: c.grid },
            title: { display: true, text: "W", color: c.muted },
          },
        },
      }}
    />
  );
}

export default function OverviewPage() {
  const { data: o, isError } = useOverview();
  const settings = useInverterSettings().data;
  const priorities = settings?.groups.find((g) => g.name === "Priorities")?.settings ?? [];
  const setting = (k: string): SettingItem | undefined => priorities.find((s) => s.key === k);

  const soc = o?.soc ?? null;
  const socTone = soc === null ? "ok" : soc < 10 ? "bad" : soc < 25 ? "warn" : "ok";
  const bw = o?.battery_power ?? null;
  const idle = bw === null || Math.abs(bw) < 10;
  const batteryTone: Tone = idle ? "" : bw > 0 ? "charge" : "discharge";
  const invSoc = o?.inverter.soc ?? null;
  const socMismatch = o?.soc_source === "bms" && soc != null && invSoc != null && Math.abs(soc - invSoc) >= 10;
  const pv = watts(o?.pv_power);
  const load = watts(o?.load_power);
  const batt = watts(bw);
  const grid = watts(o?.grid_power);
  const faults = o?.inverter.fault_codes ?? [];

  return (
    <PageMain>
      {isError ? <Banner tone="bad">Cannot reach the Ray Vault service. Retrying…</Banner> : null}
      {faults.length ? <Banner tone="bad">Inverter fault codes: {faults.join(", ")}</Banner> : null}

      <TileGrid label="Overview">
        <Tile label="State of charge" className="col-span-2">
          <div className="text-[44px] leading-tight font-semibold tabular-nums">
            {soc ?? "–"}
            {soc !== null ? <span className="ml-1 text-base font-medium text-muted">%</span> : null}
          </div>
          <Meter percent={soc ?? 0} tone={socTone} className="mt-2.5" />
          <div className="mt-1 text-xs text-muted tabular-nums">
            {o?.soc_source === "bms"
              ? "from the BMS"
              : o?.soc_source === "inverter"
                ? "inverter estimate (BMS offline)"
                : "–"}
            {o?.soc_source === "bms" && invSoc != null ? (
              <span className={socMismatch ? "text-warn" : undefined}> · inverter says {invSoc}%</span>
            ) : null}
          </div>
        </Tile>
        <Tile label="Solar" value={pv.value} unit={pv.unit} tone={o?.pv_power ? "charge" : ""} />
        <Tile label="Load" value={load.value} unit={load.unit} />
        <Tile
          label="Battery"
          value={batt.value}
          unit={batt.unit}
          tone={batteryTone}
          sub={idle ? "idle" : bw! > 0 ? "charging" : "discharging"}
        />
        <Tile
          label="Grid"
          value={fmt(o?.grid_voltage, 0)}
          unit="V"
          sub={o?.grid_power != null ? `drawing ${grid.value} ${grid.unit}` : undefined}
        />
        <Tile
          label="Inverter"
          value={o?.machine_state ?? "–"}
          sub={
            setting("output_priority") ? (
              <>
                {formatSetting(setting("output_priority")!, setting("output_priority")!.value)} ·{" "}
                <Link href="/inverter/" className="text-accent">
                  change
                </Link>
              </>
            ) : undefined
          }
        />
        <Tile label="Links">
          <div className="flex flex-col gap-1 text-[13px]">
            <span className="inline-flex items-center justify-between gap-2">
              BMS
              <span className="inline-flex items-center gap-1.5 font-medium">
                {o?.bms.rssi != null ? <SignalBars rssi={o.bms.rssi} /> : null}
                {o?.bms.link ?? "–"}
              </span>
            </span>
            <span className="inline-flex items-center justify-between gap-2">
              Inverter <span className="font-medium">{o?.inverter.link.replace("_", " ") ?? "–"}</span>
            </span>
          </div>
        </Tile>
      </TileGrid>

      <Card>
        <SectionHead title="Power, last 24 h">
          <Link href="/inverter/#range=24h" className="text-xs text-accent">
            More history →
          </Link>
        </SectionHead>
        <div className="relative h-56 sm:h-64">
          <PowerToday />
        </div>
      </Card>

      <EventsTable limit={8} />
    </PageMain>
  );
}
