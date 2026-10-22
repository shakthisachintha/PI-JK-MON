"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type EventSource } from "./api";
import { isLive, windowFor, type RangeSel } from "./range";

// Poll intervals. React Query pauses them while the tab is hidden (see providers.tsx).
const LIVE_MS = 3_000; // spec §9.2
const SYSTEM_MS = 10_000;
const EVENTS_MS = 15_000;
const SLOW_MS = 60_000;

export const queryKeys = {
  current: ["current"] as const,
  system: ["system"] as const,
  events: (limit: number, source?: EventSource) => ["events", limit, source ?? "all"] as const,
  overview: ["overview"] as const,
  inverterCurrent: ["inverter", "current"] as const,
  inverterSettings: ["inverter", "settings"] as const,
  inverterDevice: ["inverter", "device"] as const,
  inverterHistory: (sel: RangeSel) => ["inverter", "history", sel.key, sel.from ?? null, sel.to ?? null] as const,
  settings: ["settings"] as const,
  device: ["device"] as const,
  history: (sel: RangeSel) => ["history", sel.key, sel.from ?? null, sel.to ?? null] as const,
};

export function useCurrent() {
  // No retry: a failed poll should show "cannot reach service" right away; the next poll retries anyway.
  return useQuery({ queryKey: queryKeys.current, queryFn: api.current, refetchInterval: LIVE_MS, retry: false });
}

export function useSystem() {
  return useQuery({ queryKey: queryKeys.system, queryFn: api.system, refetchInterval: SYSTEM_MS });
}

export function useEvents(limit = 20, source?: EventSource) {
  return useQuery({
    queryKey: queryKeys.events(limit, source),
    queryFn: async () => (await api.events(limit, source)).events,
    refetchInterval: EVENTS_MS,
  });
}

export function useOverview() {
  return useQuery({ queryKey: queryKeys.overview, queryFn: api.overview, refetchInterval: LIVE_MS, retry: false });
}

export function useInverterCurrent() {
  return useQuery({ queryKey: queryKeys.inverterCurrent, queryFn: api.inverterCurrent, refetchInterval: LIVE_MS });
}

export function useInverterSettings() {
  return useQuery({ queryKey: queryKeys.inverterSettings, queryFn: api.inverterSettings, refetchInterval: 15_000 });
}

export function useInverterDevice() {
  return useQuery({ queryKey: queryKeys.inverterDevice, queryFn: api.inverterDevice, refetchInterval: SLOW_MS });
}

/** Write one inverter setting; on success refresh the settings and the event log. */
export function useSetInverterSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; value: number; pin: string; confirm?: boolean }) =>
      api.setInverterSetting(v.key, v.value, v.pin, v.confirm),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.inverterSettings });
      qc.invalidateQueries({ queryKey: ["events"] });
    },
  });
}

/** Inverter history for a range (same range semantics as the BMS history). */
export function useInverterHistory(sel: RangeSel | null) {
  const live = sel !== null && isLive(sel);
  return useQuery({
    queryKey: sel ? queryKeys.inverterHistory(sel) : ["inverter", "history", "pending"],
    enabled: sel !== null,
    queryFn: async () => {
      const [from, to] = windowFor(sel!);
      return { from, to, history: await api.inverterHistory(from, to) };
    },
    refetchInterval: live ? SLOW_MS : false,
    staleTime: live ? 30_000 : Infinity,
    placeholderData: keepPreviousData,
  });
}

export type InverterHistoryData = NonNullable<ReturnType<typeof useInverterHistory>["data"]>;

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.settings, refetchInterval: SLOW_MS });
}

export function useDevice() {
  return useQuery({ queryKey: queryKeys.device, queryFn: api.device, refetchInterval: SLOW_MS });
}

/** History + per-cell history for a range. Presets resolve to [now - span, now] on every fetch. */
export function useHistory(sel: RangeSel | null) {
  const live = sel !== null && isLive(sel);
  return useQuery({
    queryKey: sel ? queryKeys.history(sel) : ["history", "pending"],
    enabled: sel !== null,
    queryFn: async () => {
      const [from, to] = windowFor(sel!);
      const [history, cells] = await Promise.all([api.history(from, to), api.cellHistory(from, to)]);
      return { from, to, history, cells };
    },
    refetchInterval: live ? SLOW_MS : false,
    staleTime: live ? 30_000 : Infinity, // custom / long ranges don't change on refocus
    placeholderData: keepPreviousData, // keep showing the old range while the new one loads
  });
}

export type HistoryData = NonNullable<ReturnType<typeof useHistory>["data"]>;
