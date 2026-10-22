"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { InverterLink, LinkStatus } from "@/lib/api";
import { fmtAgo } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { useOverview } from "@/lib/queries";

import { SignalBars } from "./SignalBars";
import { cx } from "./ui";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/battery/", label: "Battery" },
  { href: "/inverter/", label: "Inverter" },
  { href: "/system/", label: "System" },
];

type Tone = "ok" | "warn" | "bad" | "off";

const DOT: Record<Tone, string> = {
  ok: "bg-ok shadow-[0_0_0_3px_color-mix(in_srgb,var(--ok)_25%,transparent)]",
  warn: "bg-warn animate-pulse",
  bad: "bg-bad",
  off: "bg-muted",
};

function bmsTone(link: LinkStatus | "disabled" | undefined, stale: boolean): Tone {
  if (!link || link === "disabled") return "off";
  if (link === "connected") return stale ? "warn" : "ok";
  return link === "disconnected" ? "bad" : "warn";
}

function inverterTone(link: InverterLink | undefined, stale: boolean): Tone {
  if (!link || link === "disabled") return "off";
  if (link === "connected") return stale ? "warn" : "ok";
  return link === "connecting" ? "warn" : "bad";
}

const INVERTER_LABEL: Record<InverterLink, string> = {
  connected: "Connected",
  connecting: "Connecting…",
  no_response: "Not responding",
  port_missing: "Adapter missing",
  disabled: "Disabled",
};

function Indicator({
  name,
  tone,
  title,
  children,
}: {
  name: string;
  tone: Tone;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] font-medium" title={title}>
      <span aria-hidden className={cx("size-2.5 flex-none rounded-full", DOT[tone])} />
      {name}
      {children}
    </span>
  );
}

export function AppHeader() {
  const pathname = usePathname();
  const now = useNow(1000);
  const { data, isError } = useOverview();
  const bms = data?.bms;
  const inv = data?.inverter;
  const freshest = Math.min(bms?.age_s ?? Infinity, inv?.age_s ?? Infinity);
  const ageS = data && Number.isFinite(freshest) ? Math.max(0, (now - data.ts) / 1000 + freshest) : null;

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-300 flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
        <Link href="/" className="text-[15px] font-semibold text-fg no-underline">
          Ray Vault
        </Link>
        <nav className="flex gap-1 overflow-x-auto" aria-label="Pages">
          {NAV.map((n) => {
            const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href.replace(/\/$/, ""));
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "rounded-lg px-2.5 py-1 text-sm font-medium no-underline",
                  active ? "bg-surface-2 text-fg" : "text-muted hover:text-fg",
                )}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
          {isError ? (
            <Indicator name="Service unreachable" tone="bad" title="Cannot reach the Ray Vault service" />
          ) : (
            <>
              <Indicator
                name="BMS"
                tone={bmsTone(bms?.link, bms?.stale ?? true)}
                title={`BMS: ${bms?.link ?? "unknown"}${bms?.stale ? " (stale)" : ""}`}
              >
                {bms?.rssi != null ? <SignalBars rssi={bms.rssi} /> : null}
              </Indicator>
              <Indicator
                name="Inverter"
                tone={inverterTone(inv?.link, inv?.stale ?? true)}
                title={`Inverter: ${inv ? INVERTER_LABEL[inv.link] : "unknown"}${inv?.stale ? " (stale)" : ""}`}
              />
              <span className="text-[13px] text-muted tabular-nums">Updated {fmtAgo(ageS)}</span>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
