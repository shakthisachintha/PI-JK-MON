"use client";

import type { Current } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useNow } from "@/lib/hooks";

import { SignalBars } from "./SignalBars";

const LINK_LABEL: Record<string, string> = {
  connected: "Connected",
  connecting: "Connecting…",
  scanning: "Scanning…",
  disconnected: "Disconnected",
};

export function StatusBar({ current, deviceName }: { current: Current | null; deviceName?: string }) {
  const now = useNow(1000);
  const link = current?.link ?? "disconnected";
  const age = current?.ts ? Math.max(0, (now - current.ts) / 1000) : null;

  return (
    <header className="statusbar">
      <div className="statusbar-inner">
        <div className="brand">
          JK BMS Monitor
          {deviceName ? <small>{deviceName}</small> : null}
        </div>
        <span className="link" title="BLE link status">
          <span className={`dot ${current ? link : ""}`} aria-hidden />
          {current ? LINK_LABEL[link] ?? link : "No connection to service"}
        </span>
        {current?.rssi != null ? (
          <span className="link num" title="BMS Bluetooth signal strength">
            <SignalBars rssi={current.rssi} />
            {current.rssi} dBm
          </span>
        ) : null}
        <span className="updated num">Updated {fmtAgo(age)}</span>
      </div>
    </header>
  );
}
