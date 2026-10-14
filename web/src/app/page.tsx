"use client";

import { useState } from "react";

import { CellChart } from "@/components/CellChart";
import { EventsTable } from "@/components/EventsTable";
import { History } from "@/components/History";
import { LiveTiles } from "@/components/LiveTiles";
import { SettingsDevice } from "@/components/SettingsDevice";
import { StatusBar } from "@/components/StatusBar";
import { api, type BmsEvent, type Current, type DeviceResponse, type SettingsResponse } from "@/lib/api";
import { usePolling } from "@/lib/hooks";

export default function Dashboard() {
  const [current, setCurrent] = useState<Current | null>(null);
  const [offline, setOffline] = useState(false);
  const [events, setEvents] = useState<BmsEvent[] | null>(null);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [device, setDevice] = useState<DeviceResponse | null>(null);

  // Live values every 3 s (spec §9.2).
  usePolling(async () => {
    try {
      setCurrent(await api.current());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, 3000);

  usePolling(async () => {
    try {
      setEvents((await api.events(20)).events);
    } catch {}
  }, 15_000);

  usePolling(async () => {
    try {
      const [s, d] = await Promise.all([api.settings(), api.device()]);
      setSettings(s);
      setDevice(d);
    } catch {}
  }, 60_000);

  const deviceName = device?.device?.device_name;

  return (
    <>
      <StatusBar current={offline ? null : current} deviceName={typeof deviceName === "string" ? deviceName : undefined} />
      <main>
        {offline ? (
          <div className="banner error">Cannot reach the monitor service. Retrying…</div>
        ) : current?.stale ? (
          <div className="banner stale">
            {current.ts
              ? "Data is stale: no fresh readings from the BMS. Values below are the last ones received."
              : "Waiting for the first reading from the BMS…"}
          </div>
        ) : null}
        <LiveTiles c={current} />
        <CellChart c={current} />
        <History />
        <EventsTable events={events} />
        <SettingsDevice settings={settings} device={device} />
      </main>
    </>
  );
}
