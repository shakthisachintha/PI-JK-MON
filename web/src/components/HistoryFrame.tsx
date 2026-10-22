"use client";

import { useEffect, useState } from "react";

import {
  DEFAULT_RANGE,
  EXPORT_MAX_SPAN,
  RANGE_KEYS,
  readHash,
  toLocalInput,
  windowFor,
  writeHash,
  type RangeKey,
  type RangeSel,
} from "@/lib/range";

import { Banner, buttonClass, Card, Empty, SectionHead, Segmented } from "./ui";

/** The selected history range, kept in the URL hash (#range=24h) so links can be shared. */
export function useRangeSelection() {
  const [sel, setSel] = useState<RangeSel | null>(null); // null until the hash is read on the client
  const [draft, setDraft] = useState<{ from: string; to: string }>({ from: "", to: "" });

  useEffect(() => {
    const init = readHash();
    setSel(init);
    const [f, t] = windowFor(init);
    setDraft({ from: toLocalInput(f), to: toLocalInput(t) });
    const onHash = () => setSel(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const choose = (next: RangeSel) => {
    writeHash(next);
    setSel(next);
  };

  const pick = (k: RangeKey) => {
    if (k !== "custom") return choose({ key: k });
    const [f, t] = windowFor(sel ?? DEFAULT_RANGE);
    setDraft({ from: toLocalInput(f), to: toLocalInput(t) });
    choose({ key: "custom", from: f, to: t });
  };

  return { sel, choose, pick, draft, setDraft };
}

interface Loaded {
  from: number;
  to: number;
  meta: { points: number; bucket_s: number; source: string };
}

export function HistoryFrame({
  title = "History",
  range,
  data,
  error,
  fetching,
  exportUrl,
  children,
}: {
  title?: string;
  range: ReturnType<typeof useRangeSelection>;
  data: Loaded | undefined;
  error: Error | null;
  fetching: boolean;
  exportUrl?: (from: number, to: number) => string;
  children: React.ReactNode;
}) {
  const { sel, choose, pick, draft, setDraft } = range;
  const span = data ? data.to - data.from : 0;

  return (
    <Card>
      <SectionHead title={title}>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label="Time range"
            value={sel?.key}
            onChange={pick}
            options={RANGE_KEYS.map((k) => ({ value: k, label: k }))}
          />
          {data && exportUrl ? (
            <a
              className={buttonClass}
              href={exportUrl(data.from, data.to)}
              aria-disabled={span > EXPORT_MAX_SPAN}
              title={span > EXPORT_MAX_SPAN ? "CSV export is limited to 31 days" : "Download raw samples as CSV"}
            >
              CSV
            </a>
          ) : null}
        </div>
      </SectionHead>

      {sel?.key === "custom" ? (
        <form
          className="mb-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const from = new Date(draft.from).getTime();
            const to = new Date(draft.to).getTime();
            if (from > 0 && to > from) choose({ key: "custom", from, to });
          }}
        >
          <input
            type="datetime-local"
            className={buttonClass}
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
            aria-label="From"
          />
          <span className="text-xs text-muted">to</span>
          <input
            type="datetime-local"
            className={buttonClass}
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            aria-label="To"
          />
          <button className={buttonClass} type="submit">
            Apply
          </button>
        </form>
      ) : null}

      {error ? (
        <div className="mb-3">
          <Banner tone="bad">Could not load history: {error.message}</Banner>
        </div>
      ) : null}

      <p className="mb-3 text-xs text-muted">
        {data
          ? `${data.meta.points} points · ${data.meta.bucket_s} s buckets from ${
              data.meta.source === "samples" ? "raw samples" : "1-minute rollups"
            }`
          : "Loading…"}
        {fetching && data ? " · refreshing…" : ""}
      </p>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,520px),1fr))] gap-4">{children}</div>
    </Card>
  );
}

export function ChartCard({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="mb-2 flex items-center justify-between gap-2 text-sm font-semibold">
        {title}
        {extra}
      </h3>
      <div className="relative h-56 sm:h-64">{children ?? <Empty>Loading…</Empty>}</div>
    </div>
  );
}

export function NoData() {
  return <Empty>No data in this range</Empty>;
}
