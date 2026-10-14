"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { isLive, windowFor, type RangeSel } from "./range";

// Poll intervals. React Query pauses them while the tab is hidden (see providers.tsx).
const LIVE_MS = 3_000; // spec §9.2
const SYSTEM_MS = 10_000;
const EVENTS_MS = 15_000;
const SLOW_MS = 60_000;

export const queryKeys = {
  current: ["current"] as const,
  system: ["system"] as const,
  events: (limit: number) => ["events", limit] as const,
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

export function useEvents(limit = 20) {
  return useQuery({
    queryKey: queryKeys.events(limit),
    queryFn: async () => (await api.events(limit)).events,
    refetchInterval: EVENTS_MS,
  });
}

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
