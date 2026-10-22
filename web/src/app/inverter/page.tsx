"use client";

import { InverterControls } from "@/components/InverterControls";
import { InverterHistory } from "@/components/InverterHistory";
import { InverterTiles } from "@/components/InverterTiles";
import { Banner, Card, PageMain, SectionHead } from "@/components/ui";
import { humanize } from "@/lib/format";
import { useInverterCurrent, useInverterDevice } from "@/lib/queries";

const LINK_MSG: Record<string, string> = {
  connecting: "Connecting to the inverter…",
  no_response: "The inverter isn't responding. Values below are the last ones received.",
  port_missing: "The RS485 adapter isn't present. Check the USB cable and the configured port.",
  disabled: "The inverter is disabled in the configuration ([inverter] enabled = false).",
};

export default function InverterPage() {
  const current = useInverterCurrent();
  const device = useInverterDevice();
  const c = current.data ?? null;
  const d = device.data?.device;

  return (
    <PageMain>
      {current.isError ? (
        <Banner tone="bad">Cannot reach the Ray Vault service. Retrying…</Banner>
      ) : c && c.link !== "connected" ? (
        <Banner tone={c.link === "connecting" ? "warn" : "bad"}>
          {LINK_MSG[c.link] ?? c.link}
          {c.last_error ? <span className="mt-1 block text-xs font-normal">{c.last_error}</span> : null}
        </Banner>
      ) : c?.stale ? (
        <Banner tone="warn">Inverter data is stale.</Banner>
      ) : null}
      {c?.link !== "disabled" ? (
        <>
          <InverterTiles c={c} />
          <InverterControls />
          <InverterHistory />
          {d ? (
            <Card>
              <SectionHead title="Device" />
              <dl className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-6 tabular-nums">
                {Object.entries(d).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-b border-border py-1.5">
                    <dt className="text-muted">{humanize(k)}</dt>
                    <dd className="text-right font-medium">{v == null ? "–" : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          ) : null}
        </>
      ) : null}
    </PageMain>
  );
}
