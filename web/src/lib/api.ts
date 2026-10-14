// Typed client for the JK BMS monitor REST API (spec §8).

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type LinkStatus = "scanning" | "connecting" | "connected" | "disconnected";

export interface Current {
  ts: number | null;
  age_s: number | null;
  stale: boolean;
  link: LinkStatus;
  voltage: number | null;
  current: number | null;
  power: number | null;
  soc: number | null;
  capacity_remain: number | null;
  nominal_capacity: number | null;
  cycle_count: number | null;
  cycle_capacity: number | null;
  avg_cell_voltage: number | null;
  delta_cell_voltage: number | null;
  min_cell_voltage: number | null;
  max_cell_voltage: number | null;
  min_cell_index: number | null; // 1-based
  max_cell_index: number | null; // 1-based
  balance_current: number | null;
  balancing_action: number | null;
  temp_t1: number | null;
  temp_t2: number | null;
  temp_mos: number | null;
  charge_on: boolean | null;
  discharge_on: boolean | null;
  balance_on: boolean | null;
  bms_uptime_s: number | null;
  cell_voltages: number[] | null;
  wire_resist: number[] | null;
}

export interface HistoryPoint {
  ts: number;
  voltage?: number | null;
  voltage_min?: number | null;
  voltage_max?: number | null;
  current?: number | null;
  current_min?: number | null;
  current_max?: number | null;
  power?: number | null;
  soc?: number | null;
  temp_t1?: number | null;
  temp_t2?: number | null;
  temp_mos?: number | null;
  delta_cell_voltage?: number | null;
}

export interface History {
  from: number;
  to: number;
  bucket_s: number;
  source: "samples" | "samples_1m";
  points: HistoryPoint[];
}

export interface CellHistory {
  from: number;
  to: number;
  bucket_s: number;
  cell_count: number;
  points: { ts: number; cells: (number | null)[] }[];
}

export interface BmsEvent {
  id: number;
  ts: number;
  type: string;
  detail: string | null;
}

export interface SettingsResponse {
  ts: number | null;
  settings: Record<string, number> | null;
}

export interface DeviceResponse {
  ts: number | null;
  device: Record<string, string | number> | null;
}

export class ApiError extends Error {}

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
    : "";
  const res = await fetch(`${API_BASE}${path}${qs}`, { cache: "no-store" });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      msg = (await res.json()).error ?? msg;
    } catch {}
    throw new ApiError(msg);
  }
  return res.json() as Promise<T>;
}

export const HISTORY_FIELDS = "voltage,current,power,soc,temp_t1,temp_t2,temp_mos,delta_cell_voltage";

export const api = {
  current: () => get<Current>("/api/current"),
  settings: () => get<SettingsResponse>("/api/settings"),
  device: () => get<DeviceResponse>("/api/device"),
  events: (limit = 20) => get<{ events: BmsEvent[] }>("/api/events", { limit }),
  history: (from: number, to: number, maxPoints = 600) =>
    get<History>("/api/history", { from, to, fields: HISTORY_FIELDS, max_points: maxPoints }),
  cellHistory: (from: number, to: number, maxPoints = 400) =>
    get<CellHistory>("/api/history/cells", { from, to, max_points: maxPoints }),
  exportUrl: (from: number, to: number) => `${API_BASE}/api/export.csv?from=${from}&to=${to}`,
};
