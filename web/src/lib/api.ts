// Typed client for the Ray Vault REST API (BMS, inverter, system).

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type LinkStatus = "scanning" | "connecting" | "connected" | "disconnected";

export interface Current {
  ts: number | null;
  age_s: number | null;
  stale: boolean;
  link: LinkStatus | "disabled";
  rssi: number | null; // BMS signal strength, dBm
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

export type EventSource = "bms" | "inverter" | "system";

export interface BmsEvent {
  id: number;
  ts: number;
  source: EventSource;
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

export interface PowerFlags {
  raw: string | null;
  under_voltage_now?: boolean;
  under_voltage_occurred?: boolean;
  freq_capped_now?: boolean;
  freq_capped_occurred?: boolean;
  throttled_now?: boolean;
  throttled_occurred?: boolean;
  soft_temp_limit_now?: boolean;
  soft_temp_limit_occurred?: boolean;
}

export interface SystemInfo {
  host: {
    hostname: string;
    model: string | null;
    uptime_s: number | null;
    cpu_temp_c: number | null;
    cpu_percent: number | null;
    cpu_count: number | null;
    load: number[] | null;
    memory: { total_mb: number; used_mb: number; available_mb: number; percent: number; swap_used_mb: number } | null;
    disk: { total_gb: number; free_gb: number; percent: number } | null;
    db_size_mb: number | null;
    power: PowerFlags | null;
    core_volts: number | null;
    wifi: { interface: string; link_quality: number; signal_dbm: number } | null;
    process_rss_mb: number | null;
  };
  ble: {
    link: LinkStatus;
    rssi: number | null;
    rssi_quality: "excellent" | "good" | "fair" | "poor" | null;
    rssi_source: "connection" | "scan" | null;
    rssi_age_s: number | null;
    recovery_stage: "ok" | "rerequesting" | "reconnecting" | "adapter_reset" | "bluetoothd_restart";
    adapter_resets: number;
    last_adapter_reset_ts: number | null;
    counters: { frames_ok: number; crc_errors: number; reconnects: number; unknown_frames: number };
  } | null;
  inverter: {
    link: InverterLink;
    last_error: string | null;
    port: string | null;
    counters: { polls_ok: number; polls_failed: number; reopens: number; writes: number };
  } | null;
}

// --- inverter ---

export type InverterLink = "connecting" | "connected" | "no_response" | "port_missing" | "disabled";

export interface InverterCurrent {
  ts: number | null;
  age_s: number | null;
  stale: boolean;
  link: InverterLink;
  last_error?: string | null;
  fault_codes?: number[];
  soc?: number | null;
  battery_voltage?: number | null;
  battery_current?: number | null;
  battery_power?: number | null;
  pv_voltage?: number | null;
  pv_current?: number | null;
  pv_power?: number | null;
  pv_charge_current?: number | null;
  machine_state?: number | null;
  machine_state_label?: string | null;
  bus_voltage?: number | null;
  grid_voltage?: number | null;
  grid_current?: number | null;
  grid_frequency?: number | null;
  grid_charge_current?: number | null;
  output_voltage?: number | null;
  output_current?: number | null;
  output_frequency?: number | null;
  load_current?: number | null;
  load_power?: number | null;
  load_apparent_power?: number | null;
  load_percent?: number | null;
  temp_dcdc?: number | null;
  temp_dcac?: number | null;
  temp_transformer?: number | null;
  temp_ambient?: number | null;
  fault_bits?: string | null;
}

export interface InverterHistoryPoint {
  ts: number;
  [field: string]: number | null;
}

export interface InverterHistory {
  from: number;
  to: number;
  bucket_s: number;
  source: "samples" | "samples_1m";
  points: InverterHistoryPoint[];
}

export interface SettingItem {
  key: string;
  label: string;
  group: string;
  kind: "enum" | "number";
  unit: string;
  danger: boolean;
  writable: boolean;
  description: string;
  value: number | null;
  options?: { value: number; label: string }[];
  min?: number;
  max?: number;
  step?: number;
}

export interface InverterSettings {
  writable: boolean;
  connected: boolean;
  read_ts: number | null;
  system_voltage: number | null;
  profile: string;
  bms_limits: { cell_count: number; cell_ovp: number; cell_uvp: number } | null;
  groups: { name: string; settings: SettingItem[] }[];
}

export interface SettingWriteResult {
  key: string;
  old: number | null;
  new: number | null;
}

export interface Overview {
  ts: number;
  soc: number | null;
  soc_source: "bms" | "inverter" | null;
  battery_power: number | null;
  pv_power: number | null;
  load_power: number | null;
  grid_voltage: number | null;
  grid_power: number | null;
  machine_state: string | null;
  priorities: Partial<Record<"output_priority" | "charge_priority", { value: number; label: string }>>;
  bms: {
    link: LinkStatus | "disabled";
    stale: boolean;
    age_s: number | null;
    soc: number | null;
    voltage: number | null;
    current: number | null;
    power: number | null;
    rssi: number | null;
  };
  inverter: {
    link: InverterLink;
    stale: boolean;
    age_s: number | null;
    soc?: number | null;
    battery_voltage?: number | null;
    battery_current?: number | null;
    battery_power?: number | null;
    fault_codes: number[];
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, params?: Record<string, string | number>): Promise<T> {
  const qs = params ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString() : "";
  const res = await fetch(`${API_BASE}${path}${qs}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = body.error ?? body.detail ?? msg;
    } catch {}
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

const get = <T>(path: string, params?: Record<string, string | number>) => request<T>(path, undefined, params);

export const HISTORY_FIELDS = "voltage,current,power,soc,temp_t1,temp_t2,temp_mos,delta_cell_voltage";
export const INVERTER_HISTORY_FIELDS =
  "pv_power,load_power,battery_power,soc,battery_voltage,grid_voltage,grid_frequency,temp_dcdc,temp_dcac,temp_ambient";

export const api = {
  current: () => get<Current>("/api/bms/current"),
  settings: () => get<SettingsResponse>("/api/bms/settings"),
  device: () => get<DeviceResponse>("/api/bms/device"),
  system: () => get<SystemInfo>("/api/system"),
  events: (limit = 20, source?: EventSource) =>
    get<{ events: BmsEvent[] }>("/api/events", source ? { limit, source } : { limit }),
  overview: () => get<Overview>("/api/overview"),
  inverterCurrent: () => get<InverterCurrent>("/api/inverter/current"),
  inverterDevice: () =>
    get<{ ts: number | null; device: Record<string, string | number | null> | null }>("/api/inverter/device"),
  inverterSettings: () => get<InverterSettings>("/api/inverter/settings"),
  inverterHistory: (from: number, to: number, maxPoints = 600) =>
    get<InverterHistory>("/api/inverter/history", { from, to, fields: INVERTER_HISTORY_FIELDS, max_points: maxPoints }),
  setInverterSetting: (key: string, value: number, pin: string, confirm = false) =>
    request<SettingWriteResult>(`/api/inverter/settings/${encodeURIComponent(key)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Pin": pin },
      body: JSON.stringify({ value, confirm }),
    }),
  inverterExportUrl: (from: number, to: number) => `${API_BASE}/api/inverter/export.csv?from=${from}&to=${to}`,
  history: (from: number, to: number, maxPoints = 600) =>
    get<History>("/api/bms/history", { from, to, fields: HISTORY_FIELDS, max_points: maxPoints }),
  cellHistory: (from: number, to: number, maxPoints = 400) =>
    get<CellHistory>("/api/bms/history/cells", { from, to, max_points: maxPoints }),
  exportUrl: (from: number, to: number) => `${API_BASE}/api/bms/export.csv?from=${from}&to=${to}`,
};
