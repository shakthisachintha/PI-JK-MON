"use client";

import { useState } from "react";

import type { EventSource } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useEvents } from "@/lib/queries";

import { Card, cx, SectionHead, Segmented } from "./ui";

const EVENT_TONE: Record<string, string> = {
  ble_connected: "text-ok",
  inverter_connected: "text-ok",
  inverter_fault_cleared: "text-ok",
  inverter_setting: "text-accent",
  ble_disconnected: "text-bad",
  ble_timeout: "text-bad",
  crc_error: "text-bad",
  service_restart: "text-bad",
  driver_crashed: "text-bad",
  inverter_no_response: "text-bad",
  inverter_port_missing: "text-bad",
  inverter_fault: "text-bad",
  inverter_setting_failed: "text-bad",
  adapter_reset: "text-warn",
  bluetoothd_restart: "text-warn",
  cell_info_rerequest: "text-warn",
  settings_changed: "text-warn",
};

type Filter = "all" | EventSource;

export function EventsTable({ limit = 20 }: { limit?: number }) {
  const [filter, setFilter] = useState<Filter>("all");
  const { data: events, isPending } = useEvents(limit, filter === "all" ? undefined : filter);
  return (
    <Card>
      <SectionHead title="Events">
        <Segmented
          label="Event source"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All" },
            { value: "bms", label: "BMS" },
            { value: "inverter", label: "Inverter" },
            { value: "system", label: "System" },
          ]}
        />
      </SectionHead>
      {isPending ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : !events?.length ? (
        <p className="text-xs text-muted">No events yet</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="text-xs text-muted">
                {["Time", "Source", "Event", "Detail"].map((h) => (
                  <th key={h} className="border-b border-border px-2.5 py-1.5 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-b border-border align-top last:border-0">
                  <td className="px-2.5 py-1.5 whitespace-nowrap text-muted tabular-nums">{fmtTime(e.ts)}</td>
                  <td className="px-2.5 py-1.5 text-xs text-muted uppercase">{e.source}</td>
                  <td className="px-2.5 py-1.5">
                    <span
                      className={cx(
                        "rounded-md border border-border bg-surface-2 px-1.5 py-px font-mono text-xs whitespace-nowrap",
                        EVENT_TONE[e.type],
                      )}
                    >
                      {e.type}
                    </span>
                  </td>
                  <td className="px-2.5 py-1.5 wrap-break-word">{e.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
