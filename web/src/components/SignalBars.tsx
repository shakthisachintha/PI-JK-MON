// Four-bar signal indicator for a BLE RSSI in dBm.

export function rssiBars(rssi: number | null | undefined): number {
  if (rssi === null || rssi === undefined) return 0;
  return rssi >= -60 ? 4 : rssi >= -70 ? 3 : rssi >= -80 ? 2 : 1;
}

export function SignalBars({ rssi, title }: { rssi: number | null | undefined; title?: string }) {
  const n = rssiBars(rssi);
  const level = n >= 3 ? "good" : n === 2 ? "fair" : n === 1 ? "poor" : "none";
  return (
    <span className={`bars ${level}`} title={title ?? (rssi == null ? "No signal reading" : `${rssi} dBm`)} aria-hidden>
      {[1, 2, 3, 4].map((i) => (
        <i key={i} className={i <= n ? "on" : ""} style={{ height: `${25 * i}%` }} />
      ))}
    </span>
  );
}
