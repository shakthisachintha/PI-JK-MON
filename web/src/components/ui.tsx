// Small shared building blocks so components stay consistent.

export type Tone = "" | "ok" | "warn" | "bad" | "charge" | "discharge";

export const toneText: Record<Tone, string> = {
  "": "",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  charge: "text-charge",
  discharge: "text-discharge",
};

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return <section className={cx("rounded-xl border border-border bg-surface p-4", className)}>{children}</section>;
}

export function SectionHead({ title, children }: { title: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-[13px] font-semibold tracking-wide text-muted uppercase">{title}</h2>
      {children}
    </div>
  );
}

export function TileGrid({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div aria-label={label} className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))]">
      {children}
    </div>
  );
}

export function Tile({
  label,
  value,
  unit,
  sub,
  tone = "",
  meter,
  className,
  children,
}: {
  label: string;
  value?: React.ReactNode;
  unit?: string;
  sub?: React.ReactNode;
  tone?: Tone;
  meter?: number | null;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={cx("min-w-0 rounded-xl border border-border bg-surface px-4 py-3.5", className)}>
      <div className="mb-1 text-xs text-muted">{label}</div>
      {value !== undefined ? (
        <div className={cx("truncate text-xl leading-tight font-semibold tabular-nums sm:text-2xl", toneText[tone])}>
          {value}
          {unit && value !== "–" ? <span className="ml-1 text-sm font-medium text-muted">{unit}</span> : null}
        </div>
      ) : null}
      {children}
      {meter !== undefined && meter !== null ? (
        <Meter percent={meter} tone={tone === "bad" ? "bad" : tone === "warn" ? "warn" : "ok"} className="mt-2.5" />
      ) : null}
      {sub ? <div className="mt-1 text-xs text-muted tabular-nums">{sub}</div> : null}
    </div>
  );
}

const meterFill = { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad" } as const;

export function Meter({
  percent,
  tone = "ok",
  className,
}: {
  percent: number;
  tone?: keyof typeof meterFill;
  className?: string;
}) {
  const p = Math.max(0, Math.min(100, percent));
  return (
    <div
      role="meter"
      aria-valuenow={p}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cx("h-2.5 overflow-hidden rounded-full border border-border bg-surface-2", className)}
    >
      <div className={cx("h-full transition-[width] duration-500", meterFill[tone])} style={{ width: `${p}%` }} />
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: React.ReactNode }[];
  value: T | undefined;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap rounded-[9px] border border-border bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className="cursor-pointer rounded-[7px] px-3 py-1 text-sm font-medium text-muted aria-pressed:bg-surface aria-pressed:text-fg aria-pressed:shadow-sm"
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const buttonClass =
  "inline-block cursor-pointer rounded-lg border border-border bg-surface px-2.5 py-1 font-medium text-fg no-underline hover:border-muted aria-disabled:pointer-events-none aria-disabled:opacity-50";

export function Banner({ tone, children }: { tone: "warn" | "bad"; children: React.ReactNode }) {
  return (
    <div
      className={cx(
        "rounded-xl border px-3.5 py-2.5 font-medium",
        tone === "warn" ? "border-warn/45 bg-warn/15" : "border-bad/45 bg-bad/12",
      )}
    >
      {children}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="grid h-full place-items-center text-muted">{children}</div>;
}
