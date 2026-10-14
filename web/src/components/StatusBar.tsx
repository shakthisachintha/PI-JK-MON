"use client";

import type { Current } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useNow } from "@/lib/hooks";

import { SignalBars } from "./SignalBars";
import { cx } from "./ui";

const LINK_LABEL: Record<string, string> = {
  connected: "Connected",
  connecting: "Connecting…",
  scanning: "Scanning…",
  disconnected: "Disconnected",
};

const DOT: Record<string, string> = {
  connected: "bg-ok shadow-[0_0_0_3px_color-mix(in_srgb,var(--ok)_25%,transparent)]",
  connecting: "bg-warn animate-pulse",
  scanning: "bg-warn animate-pulse",
  disconnected: "bg-bad",
};

export function StatusBar({ current, deviceName }: { current: Current | null; deviceName?: string }) {
  const now = useNow(1000);
  const link = current?.link ?? "disconnected";
  const age = current?.ts ? Math.max(0, (now - current.ts) / 1000) : null;

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-300 flex-wrap items-center gap-3 px-4 py-2.5">
        <div className="mr-auto text-[15px] font-semibold">
          JK BMS Monitor
          {deviceName ? <small className="ml-2 text-xs font-normal text-muted">{deviceName}</small> : null}
        </div>
        <span className="inline-flex items-center gap-1.5 font-medium" title="BLE link status">
          <span aria-hidden className={cx("size-2.5 flex-none rounded-full", current ? DOT[link] : "bg-muted")} />
          {current ? (LINK_LABEL[link] ?? link) : "No connection to service"}
        </span>
        {current?.rssi != null ? (
          <span className="inline-flex items-center gap-1.5 font-medium tabular-nums" title="BMS Bluetooth signal strength">
            <SignalBars rssi={current.rssi} />
            {current.rssi} dBm
          </span>
        ) : null}
        <span className="text-muted tabular-nums">Updated {fmtAgo(age)}</span>
      </div>
    </header>
  );
}
