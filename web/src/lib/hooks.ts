"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

/** Re-renders every `ms` so relative times ("3 s ago") stay current. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

const darkQuery = "(prefers-color-scheme: dark)";

/** "dark" | "light", following the OS setting live. */
export function useColorScheme(): "dark" | "light" {
  const subscribe = useCallback((cb: () => void) => {
    const mq = window.matchMedia(darkQuery);
    mq.addEventListener("change", cb);
    return () => mq.removeEventListener("change", cb);
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => (window.matchMedia(darkQuery).matches ? "dark" : "light"),
    () => "light",
  );
}

/** Read a CSS custom property from :root (charts need concrete colour strings). */
export function cssVar(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Concrete colours for Chart.js, recomputed when the colour scheme flips. */
export function useChartColors() {
  const scheme = useColorScheme();
  return useMemo(() => {
    const v = (n: string) => cssVar(`--${n}`);
    return {
      scheme,
      text: v("text"),
      muted: v("muted"),
      grid: v("grid"),
      surface: v("surface"),
      voltage: v("c-voltage"),
      voltageBand: v("c-voltage-band"),
      soc: v("c-soc"),
      current: v("c-current"),
      power: v("c-power"),
      t1: v("c-t1"),
      t2: v("c-t2"),
      mos: v("c-mos"),
      delta: v("c-delta"),
      cellLow: v("cell-low"),
      cellHigh: v("cell-high"),
      cellBar: v("cell-bar"),
    };
  }, [scheme]);
}

export type ChartColors = ReturnType<typeof useChartColors>;
