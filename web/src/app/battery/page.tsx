"use client";

import { CellChart } from "@/components/CellChart";
import { History } from "@/components/History";
import { LiveTiles } from "@/components/LiveTiles";
import { SettingsDevice } from "@/components/SettingsDevice";
import { Banner, PageMain } from "@/components/ui";
import { useCurrent } from "@/lib/queries";

export default function BatteryPage() {
  const current = useCurrent();
  const offline = current.isError;
  const c = offline ? null : (current.data ?? null);

  return (
    <PageMain>
      {offline ? (
        <Banner tone="bad">Cannot reach the Ray Vault service. Retrying…</Banner>
      ) : c?.link === "disabled" ? (
        <Banner tone="warn">The BMS is disabled in the configuration.</Banner>
      ) : c?.stale ? (
        <Banner tone="warn">
          {c.ts
            ? "BMS data is stale: no fresh readings. Values below are the last ones received."
            : "Waiting for the first reading from the BMS…"}
        </Banner>
      ) : null}
      <LiveTiles c={c} />
      <CellChart c={c} />
      <History />
      <SettingsDevice />
    </PageMain>
  );
}
