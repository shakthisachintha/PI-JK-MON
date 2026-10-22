"use client";

import { useEffect, useState } from "react";

import { ApiError, type InverterSettings, type SettingItem } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useInverterSettings, useSetInverterSetting } from "@/lib/queries";

import { Banner, buttonClass, Card, cx, Dialog, inputClass, primaryButtonClass, SectionHead } from "./ui";

const PIN_KEY = "ray-vault-control-pin"; // sessionStorage: forgotten when the tab closes

function storedPin(): string | null {
  try {
    return sessionStorage.getItem(PIN_KEY);
  } catch {
    return null;
  }
}

function storePin(pin: string | null) {
  try {
    if (pin) sessionStorage.setItem(PIN_KEY, pin);
    else sessionStorage.removeItem(PIN_KEY);
  } catch {}
}

function decimals(step: number | undefined): number {
  if (!step || Number.isInteger(step)) return 0;
  return Math.min(3, String(step).split(".")[1]?.length ?? 0);
}

export function formatSetting(item: SettingItem, value: number | null | undefined): string {
  if (value == null) return "–";
  if (item.kind === "enum") return item.options?.find((o) => o.value === value)?.label ?? String(value);
  return `${value.toFixed(decimals(item.step))}${item.unit ? ` ${item.unit}` : ""}`;
}

/** Client-side check mirroring the server's; the server re-validates everything. */
function validate(item: SettingItem, value: number): string | null {
  if (Number.isNaN(value)) return "Enter a number";
  if (item.kind === "enum") return item.options?.some((o) => o.value === value) ? null : "Pick an option";
  if (item.min != null && value < item.min - 1e-9) return `Minimum ${item.min} ${item.unit}`;
  if (item.max != null && value > item.max + 1e-9) return `Maximum ${item.max} ${item.unit}`;
  if (item.step && item.min != null) {
    const k = (value - item.min) / item.step;
    if (Math.abs(k - Math.round(k)) > 1e-6) return `Use steps of ${item.step} ${item.unit}`;
  }
  return null;
}

function SettingRow({
  item,
  disabled,
  onApply,
}: {
  item: SettingItem;
  disabled: boolean;
  onApply: (item: SettingItem, value: number) => void;
}) {
  const [draft, setDraft] = useState<string>(item.value == null ? "" : String(item.value));
  useEffect(() => setDraft(item.value == null ? "" : String(item.value)), [item.value]);

  const value = Number(draft);
  const changed = draft !== "" && value !== item.value;
  const error = changed ? validate(item, value) : null;
  const editable = item.writable && !disabled;

  return (
    <div className="grid gap-x-4 gap-y-1.5 border-b border-border py-3 last:border-0 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 font-medium">
          {item.label}
          {item.danger ? (
            <span className="rounded-full border border-warn/40 bg-warn/15 px-1.5 text-[10px] font-semibold text-warn uppercase">
              battery safety
            </span>
          ) : null}
        </div>
        {item.description ? <div className="text-xs text-muted">{item.description}</div> : null}
        {item.writable && item.kind === "number" && item.min != null ? (
          <div className="text-xs text-muted tabular-nums">
            Range {formatSetting(item, item.min)} – {formatSetting(item, item.max)}, step {item.step}
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {!item.writable ? (
          <span className="font-semibold tabular-nums">{formatSetting(item, item.value)}</span>
        ) : (
          <>
            {item.kind === "enum" ? (
              <select
                className={inputClass}
                value={draft}
                disabled={!editable}
                onChange={(e) => setDraft(e.target.value)}
                aria-label={item.label}
              >
                {item.value == null ? <option value="">–</option> : null}
                {item.options?.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <span className="inline-flex items-center gap-1.5">
                <input
                  type="number"
                  inputMode="decimal"
                  className={cx(inputClass, "w-24 text-right", error && "border-bad")}
                  value={draft}
                  min={item.min}
                  max={item.max}
                  step={item.step}
                  disabled={!editable}
                  onChange={(e) => setDraft(e.target.value)}
                  aria-label={item.label}
                  aria-invalid={!!error}
                />
                <span className="text-xs text-muted">{item.unit}</span>
              </span>
            )}
            <button
              type="button"
              className={buttonClass}
              disabled={!editable || !changed || !!error}
              aria-disabled={!editable || !changed || !!error}
              onClick={() => onApply(item, value)}
            >
              Apply
            </button>
          </>
        )}
        {error ? <div className="w-full text-right text-xs text-bad">{error}</div> : null}
      </div>
    </div>
  );
}

interface Pending {
  item: SettingItem;
  value: number;
}

const CONFIRM_WORD = "CHANGE";

function ConfirmDialog({
  pending,
  settings,
  onClose,
  onDone,
}: {
  pending: Pending | null;
  settings: InverterSettings | undefined;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const mutation = useSetInverterSetting();
  const [pin, setPin] = useState("");
  const [askPin, setAskPin] = useState(false);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pending) return;
    const p = storedPin();
    setPin(p ?? "");
    setAskPin(!p);
    setTyped("");
    setError(null);
    mutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);

  if (!pending) return null;
  const { item, value } = pending;
  const limits = settings?.bms_limits;
  const needsWord = item.danger;
  const ready = pin.length > 0 && (!needsWord || typed.trim().toUpperCase() === CONFIRM_WORD) && !mutation.isPending;

  const submit = () => {
    setError(null);
    mutation.mutate(
      { key: item.key, value, pin, confirm: item.danger },
      {
        onSuccess: (r) => {
          storePin(pin);
          onDone(`${item.label}: ${formatSetting(item, r.old)} → ${formatSetting(item, r.new)}`);
        },
        onError: (e) => {
          const status = e instanceof ApiError ? e.status : 0;
          if (status === 401 || status === 423) {
            storePin(null);
            setAskPin(true);
            setPin("");
          }
          setError(e.message);
        },
      },
    );
  };

  return (
    <Dialog open title={`Change ${item.label.toLowerCase()}?`} onClose={() => !mutation.isPending && onClose()}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) submit();
        }}
      >
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-surface-2 p-3 tabular-nums">
          <span className="text-muted">Current</span>
          <span className="font-medium">{formatSetting(item, item.value)}</span>
          <span className="text-muted">New</span>
          <span className="font-semibold">{formatSetting(item, value)}</span>
        </div>
        {item.danger ? (
          <Banner tone="warn">
            This is a battery protection voltage. A wrong value can over-charge or over-discharge the pack.
            {limits ? (
              <span className="mt-1 block text-xs font-normal">
                Checked against the BMS: {limits.cell_count} cells, over-voltage {limits.cell_ovp.toFixed(3)} V/cell,
                under-voltage {limits.cell_uvp.toFixed(3)} V/cell (new value = {(value / limits.cell_count).toFixed(3)}{" "}
                V/cell).
              </span>
            ) : null}
          </Banner>
        ) : null}
        {askPin ? (
          <label className="flex flex-col gap-1 text-sm">
            Control PIN
            <input
              className={inputClass}
              type="password"
              inputMode="numeric"
              autoComplete="off"
              autoFocus
              value={pin}
              onChange={(e) => setPin(e.target.value)}
            />
          </label>
        ) : (
          <p className="text-xs text-muted">
            Using the PIN saved for this tab.{" "}
            <button type="button" className="cursor-pointer text-accent underline" onClick={() => setAskPin(true)}>
              Enter a different PIN
            </button>
          </p>
        )}
        {needsWord ? (
          <label className="flex flex-col gap-1 text-sm">
            Type {CONFIRM_WORD} to confirm
            <input className={inputClass} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </label>
        ) : null}
        {error ? <Banner tone="bad">{error}</Banner> : null}
        <div className="flex justify-end gap-2">
          <button type="button" className={buttonClass} onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </button>
          <button type="submit" className={primaryButtonClass} disabled={!ready}>
            {mutation.isPending ? "Writing…" : "Write to inverter"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function InverterControls() {
  const { data, isError, error } = useInverterSettings();
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [hasPin, setHasPin] = useState(false);
  useEffect(() => setHasPin(!!storedPin()), [pending]);

  const disabled = !data?.writable || !data?.connected;

  return (
    <Card>
      <SectionHead title="Inverter settings">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {data ? (
            <>
              <span>
                {data.profile}
                {data.system_voltage ? ` · ${data.system_voltage} V system` : ""}
                {data.read_ts ? ` · read ${fmtTime(data.read_ts)}` : ""}
              </span>
              {!data.writable ? (
                <span
                  className="rounded-full border border-border bg-surface-2 px-2 py-0.5 font-semibold"
                  title="Set [control] pin in the config to allow changes"
                >
                  Read-only
                </span>
              ) : hasPin ? (
                <button
                  type="button"
                  className={buttonClass}
                  onClick={() => {
                    storePin(null);
                    setHasPin(false);
                  }}
                >
                  Forget PIN
                </button>
              ) : null}
            </>
          ) : null}
        </div>
      </SectionHead>

      {isError ? <Banner tone="bad">Could not load settings: {error.message}</Banner> : null}
      {data && !data.connected ? (
        <div className="mb-3">
          <Banner tone="warn">The inverter isn&apos;t connected; settings can&apos;t be changed right now.</Banner>
        </div>
      ) : null}
      {data && !data.writable ? (
        <p className="mb-3 text-xs text-muted">
          Changes are disabled. Set <code>pin</code> under <code>[control]</code> in the config to enable them.
        </p>
      ) : null}
      {notice ? (
        <div className="mb-3">
          <Banner tone="ok">✓ {notice}</Banner>
        </div>
      ) : null}

      {!data ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.groups.map((g) => (
            <section key={g.name} className="rounded-lg border border-border p-3">
              <h3 className="mb-1 text-sm font-semibold">{g.name}</h3>
              {g.settings.map((item) => (
                <SettingRow
                  key={item.key}
                  item={item}
                  disabled={disabled}
                  onApply={(i, v) => setPending({ item: i, value: v })}
                />
              ))}
            </section>
          ))}
        </div>
      )}

      <ConfirmDialog
        pending={pending}
        settings={data}
        onClose={() => setPending(null)}
        onDone={(msg) => {
          setPending(null);
          setNotice(msg);
        }}
      />
    </Card>
  );
}
