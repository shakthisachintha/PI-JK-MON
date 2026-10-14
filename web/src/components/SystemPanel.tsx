"use client";

import type { SystemInfo } from "@/lib/api";
import { fmt, fmtAgo, fmtDuration, fmtTime } from "@/lib/format";
import { useSystem } from "@/lib/queries";

import { SignalBars } from "./SignalBars";
import { Card, SectionHead, Tile as Stat, TileGrid, type Tone } from "./ui";

const STAGE: Record<SystemInfo["ble"]["recovery_stage"], { text: string; tone: Tone }> = {
  ok: { text: "Healthy", tone: "ok" },
  rerequesting: { text: "Re-requesting data", tone: "warn" },
  reconnecting: { text: "Reconnecting", tone: "warn" },
  adapter_reset: { text: "Resetting adapter", tone: "bad" },
};

function powerStatus(p: SystemInfo["host"]["power"]): { text: string; tone: Tone; sub: string } {
  if (!p) return { text: "–", tone: "", sub: "not available" };
  if (p.under_voltage_now) return { text: "Under-voltage", tone: "bad", sub: "Supply too weak right now" };
  const now = [p.throttled_now && "throttled", p.freq_capped_now && "freq capped", p.soft_temp_limit_now && "temp limit"].filter(
    Boolean,
  );
  if (now.length) return { text: "Limited", tone: "warn", sub: `Now: ${now.join(", ")}` };
  const past = [
    p.under_voltage_occurred && "under-voltage",
    p.throttled_occurred && "throttling",
    p.freq_capped_occurred && "freq cap",
    p.soft_temp_limit_occurred && "temp limit",
  ].filter(Boolean);
  if (past.length) return { text: "OK", tone: "warn", sub: `Since boot: ${past.join(", ")}` };
  return { text: "OK", tone: "ok", sub: "No under-voltage since boot" };
}

export function SystemPanel() {
  const sys = useSystem().data;
  const h = sys?.host;
  const b = sys?.ble;
  const temp = h?.cpu_temp_c ?? null;
  const tempTone: Tone = temp === null ? "" : temp >= 80 ? "bad" : temp >= 70 ? "warn" : "ok";
  const power = powerStatus(h?.power ?? null);
  const mem = h?.memory;
  const memTone: Tone = !mem ? "" : mem.percent >= 90 ? "bad" : mem.percent >= 75 ? "warn" : "";
  const disk = h?.disk;
  const diskTone: Tone = !disk ? "" : disk.percent >= 95 ? "bad" : disk.percent >= 85 ? "warn" : "";
  const rssi = b?.rssi ?? null;
  const rssiTone: Tone = rssi === null ? "" : rssi >= -70 ? "ok" : rssi >= -80 ? "warn" : "bad";
  const stage = b ? STAGE[b.recovery_stage] : null;
  const wifi = h?.wifi;

  return (
    <Card>
      <SectionHead title="Raspberry Pi & link">
        <span className="text-xs text-muted">
          {h ? [h.model ?? h.hostname, h.uptime_s !== null ? `up ${fmtDuration(h.uptime_s)}` : null].filter(Boolean).join(" · ") : "Loading…"}
        </span>
      </SectionHead>
      <TileGrid label="Raspberry Pi and link health">
        <Stat
          label="BMS signal"
          value={
            <span className="inline-flex items-center gap-2">
              <SignalBars rssi={rssi} size="lg" />
              {rssi ?? "–"}
            </span>
          }
          unit="dBm"
          tone={rssiTone}
          sub={
            rssi === null
              ? "No reading yet"
              : `${b?.rssi_quality ?? ""} · ${b?.rssi_source === "connection" ? "live" : "at last scan"}${
                  b?.rssi_age_s != null ? `, ${fmtAgo(b.rssi_age_s)}` : ""
                }`
          }
        />
        <Stat
          label="Link recovery"
          value={stage?.text ?? "–"}
          tone={stage?.tone}
          sub={
            b
              ? `${b.adapter_resets} adapter reset${b.adapter_resets === 1 ? "" : "s"} · ${b.counters.reconnects} reconnect${
                  b.counters.reconnects === 1 ? "" : "s"
                }${b.last_adapter_reset_ts ? ` · last ${fmtTime(b.last_adapter_reset_ts)}` : ""}`
              : undefined
          }
        />
        <Stat
          label="Power supply"
          value={power.text}
          tone={power.tone}
          sub={`${power.sub}${h?.core_volts != null ? ` · core ${fmt(h.core_volts, 2)} V` : ""}`}
        />
        <Stat label="CPU temperature" value={fmt(temp, 1)} unit="°C" tone={tempTone} sub={temp !== null && temp >= 80 ? "Throttling likely" : undefined} />
        <Stat
          label="CPU"
          value={fmt(h?.cpu_percent, 0)}
          unit="%"
          meter={h?.cpu_percent}
          sub={h?.load ? `load ${h.load.map((x) => x.toFixed(2)).join(" ")}` : undefined}
        />
        <Stat
          label="Memory"
          value={mem ? fmt(mem.percent, 0) : "–"}
          unit="%"
          tone={memTone}
          meter={mem?.percent}
          sub={mem ? `${mem.used_mb} / ${mem.total_mb} MB${mem.swap_used_mb ? ` · swap ${mem.swap_used_mb} MB` : ""}` : undefined}
        />
        <Stat
          label="Storage"
          value={disk ? fmt(disk.free_gb, 1) : "–"}
          unit="GB free"
          tone={diskTone}
          meter={disk?.percent}
          sub={h?.db_size_mb != null ? `database ${fmt(h.db_size_mb, 0)} MB` : undefined}
        />
        <Stat
          label="WiFi"
          value={wifi ? fmt(wifi.signal_dbm, 0) : "–"}
          unit="dBm"
          sub={wifi ? `${wifi.interface} · quality ${fmt(wifi.link_quality, 0)}/70` : "Not on WiFi"}
        />
      </TileGrid>
    </Card>
  );
}
