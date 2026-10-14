// Four-bar signal indicator for a BLE RSSI in dBm.

import { cx } from "./ui";

export function rssiBars(rssi: number | null | undefined): number {
  if (rssi === null || rssi === undefined) return 0;
  return rssi >= -60 ? 4 : rssi >= -70 ? 3 : rssi >= -80 ? 2 : 1;
}

const fill = ["", "bg-bad", "bg-warn", "bg-ok", "bg-ok"];

export function SignalBars({
  rssi,
  title,
  size = "sm",
}: {
  rssi: number | null | undefined;
  title?: string;
  size?: "sm" | "lg";
}) {
  const n = rssiBars(rssi);
  return (
    <span
      title={title ?? (rssi == null ? "No signal reading" : `${rssi} dBm`)}
      aria-hidden
      className={cx("inline-flex flex-none items-end gap-0.5", size === "lg" ? "h-4.5 w-6" : "h-3 w-4.25")}
    >
      {[1, 2, 3, 4].map((i) => (
        <i
          key={i}
          className={cx("block rounded-[1px]", size === "lg" ? "w-[4.5px]" : "w-0.75", i <= n ? fill[n] : "bg-border")}
          style={{ height: `${25 * i}%` }}
        />
      ))}
    </span>
  );
}
