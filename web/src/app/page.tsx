"use client";

import { CellChart } from "@/components/CellChart";
import { EventsTable } from "@/components/EventsTable";
import { History } from "@/components/History";
import { LiveTiles } from "@/components/LiveTiles";
import { SettingsDevice } from "@/components/SettingsDevice";
import { StatusBar } from "@/components/StatusBar";
import { SystemPanel } from "@/components/SystemPanel";
import { Banner } from "@/components/ui";
import { useCurrent, useDevice } from "@/lib/queries";

export default function Dashboard() {
  const current = useCurrent();
  const device = useDevice();
  const offline = current.isError;
  const c = offline ? null : (current.data ?? null);
  const deviceName = device.data?.device?.device_name;

  return (
    <>
      <StatusBar current={c} deviceName={typeof deviceName === "string" ? deviceName : undefined} />
      <main className="mx-auto flex max-w-300 flex-col gap-4 px-4 pt-4 pb-12">
        {offline ? (
          <Banner tone="bad">Cannot reach the monitor service. Retrying…</Banner>
        ) : c?.stale ? (
          <Banner tone="warn">
            {c.ts
              ? "Data is stale: no fresh readings from the BMS. Values below are the last ones received."
              : "Waiting for the first reading from the BMS…"}
          </Banner>
        ) : null}
        <LiveTiles c={c} />
        <CellChart c={c} />
        <History />
        <SystemPanel />
        <EventsTable />
        <SettingsDevice />
      </main>
    </>
  );
}
