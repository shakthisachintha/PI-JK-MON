"use client";

import type { BmsEvent } from "@/lib/api";
import { fmtTime } from "@/lib/format";

export function EventsTable({ events }: { events: BmsEvent[] | null }) {
  return (
    <section className="card">
      <div className="section-head">
        <h2>Events</h2>
        <span className="meta">last 20</span>
      </div>
      {events === null ? (
        <p className="meta">Loading…</p>
      ) : events.length === 0 ? (
        <p className="meta">No events yet</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="ts num">{fmtTime(e.ts)}</td>
                  <td>
                    <span className={`ev ${e.type}`}>{e.type}</span>
                  </td>
                  <td className="detail">{e.detail ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
