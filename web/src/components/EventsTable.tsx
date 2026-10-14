"use client";

import { fmtTime } from "@/lib/format";
import { useEvents } from "@/lib/queries";

import { Card, cx, SectionHead } from "./ui";

const EVENT_TONE: Record<string, string> = {
  ble_connected: "text-ok",
  ble_disconnected: "text-bad",
  ble_timeout: "text-bad",
  crc_error: "text-bad",
  service_restart: "text-bad",
  adapter_reset: "text-warn",
  cell_info_rerequest: "text-warn",
  settings_changed: "text-warn",
};

export function EventsTable() {
  const { data: events, isPending } = useEvents(20);
  return (
    <Card>
      <SectionHead title="Events">
        <span className="text-xs text-muted">last 20</span>
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
                {["Time", "Event", "Detail"].map((h) => (
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
