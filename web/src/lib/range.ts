// History range selection, persisted in the URL hash: #range=24h or #range=custom&from=<ms>&to=<ms>

export const HOUR = 3_600_000;
export const DAY = 24 * HOUR;
export const PRESETS = { "1h": HOUR, "6h": 6 * HOUR, "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY } as const;
export type Preset = keyof typeof PRESETS;
export type RangeKey = Preset | "custom";
export const RANGE_KEYS: RangeKey[] = ["1h", "6h", "24h", "7d", "30d", "custom"];
export const EXPORT_MAX_SPAN = 31 * DAY;

export interface RangeSel {
  key: RangeKey;
  from?: number; // custom only
  to?: number;
}

export const DEFAULT_RANGE: RangeSel = { key: "24h" };

export function readHash(): RangeSel {
  const h = new URLSearchParams(window.location.hash.slice(1));
  const key = h.get("range") as RangeKey | null;
  if (key === "custom") {
    const from = Number(h.get("from"));
    const to = Number(h.get("to"));
    if (from > 0 && to > from) return { key, from, to };
  } else if (key && key in PRESETS) {
    return { key };
  }
  return DEFAULT_RANGE;
}

export function writeHash(sel: RangeSel) {
  const h = new URLSearchParams({ range: sel.key });
  if (sel.key === "custom" && sel.from && sel.to) {
    h.set("from", String(sel.from));
    h.set("to", String(sel.to));
  }
  window.history.replaceState(null, "", `#${h.toString()}`);
}

/** Concrete [from, to] for a selection; presets end at "now". */
export function windowFor(sel: RangeSel): [number, number] {
  if (sel.key === "custom" && sel.from && sel.to) return [sel.from, sel.to];
  const now = Date.now();
  return [now - PRESETS[sel.key as Preset], now];
}

/** Presets up to 24 h refresh every minute (spec §9.2). */
export function isLive(sel: RangeSel): boolean {
  return sel.key !== "custom" && PRESETS[sel.key as Preset] <= DAY;
}

export function toLocalInput(ms: number): string {
  const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
  return d.toISOString().slice(0, 16);
}
