// JK BMS widget for Scriptable (iOS). Shows SOC, power, voltage, current, cell spread and link status.
//
// Setup:
//   1. Install "Scriptable" from the App Store, create a new script and paste this file into it.
//   2. Add a Scriptable widget (small, medium, or lock-screen rectangular/circular) to your screen.
//   3. Long-press the widget → Edit Widget → Script: this script.
//      Parameter: your dashboard URL, e.g. http://raspberrypi.local:8080 (or set DEFAULT_URL below).
//   Tapping the widget opens the dashboard.

const DEFAULT_URL = "http://raspberryss.local:8080";
const BASE = ((args.widgetParameter || DEFAULT_URL) + "").trim().replace(/\/+$/, "");
const CACHE = "jkbms-widget-cache.json";

const C = {
  bg: Color.dynamic(new Color("#ffffff"), new Color("#141a21")),
  text: Color.dynamic(new Color("#14181f"), new Color("#e6ebf1")),
  muted: Color.dynamic(new Color("#667085"), new Color("#8d99a8")),
  track: Color.dynamic(new Color("#e4e7ec"), new Color("#253039")),
  ok: Color.dynamic(new Color("#16a34a"), new Color("#22c55e")),
  warn: Color.dynamic(new Color("#d97706"), new Color("#f59e0b")),
  bad: Color.dynamic(new Color("#dc2626"), new Color("#f87171")),
  charge: Color.dynamic(new Color("#16a34a"), new Color("#22c55e")),
  discharge: Color.dynamic(new Color("#ea580c"), new Color("#fb923c")),
};

// --- data ---------------------------------------------------------------------

const fm = FileManager.local();
const cachePath = fm.joinPath(fm.cacheDirectory(), CACHE);

async function load() {
  try {
    const req = new Request(`${BASE}/api/current`);
    req.timeoutInterval = 8;
    const data = await req.loadJSON();
    if (req.response.statusCode !== 200) throw new Error(`HTTP ${req.response.statusCode}`);
    fm.writeString(cachePath, JSON.stringify({ fetched: Date.now(), data }));
    return { data, fetched: Date.now(), offline: false };
  } catch (e) {
    // Away from home WiFi (or Pi down): show the last values we saw.
    if (fm.fileExists(cachePath)) {
      const cached = JSON.parse(fm.readString(cachePath));
      return { ...cached, offline: true };
    }
    return { data: null, fetched: null, offline: true, error: String(e) };
  }
}

// --- helpers ------------------------------------------------------------------

const fmt = (v, d = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

function socColor(soc) {
  if (soc === null || soc === undefined) return C.muted;
  return soc < 10 ? C.bad : soc < 25 ? C.warn : C.ok;
}

function flowColor(a) {
  if (a === null || a === undefined || Math.abs(a) < 0.05) return C.muted;
  return a > 0 ? C.charge : C.discharge;
}

function fmtPower(w) {
  if (w === null || w === undefined) return "–";
  return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`;
}

function statusLine(r) {
  const d = r.data;
  if (!d) return { text: "No data", color: C.bad };
  if (r.offline) {
    const age = r.fetched ? Math.round((Date.now() - r.fetched) / 60000) : null;
    return { text: age === null ? "Offline" : `Offline · ${age} min old`, color: C.warn };
  }
  if (d.link !== "connected") return { text: `BMS ${d.link}`, color: C.warn };
  if (d.stale) return { text: "Stale data", color: C.warn };
  const t = new Date(d.ts);
  return { text: `${t.getHours()}:${String(t.getMinutes()).padStart(2, "0")}`, color: C.muted };
}

function bar(width, height, fraction, color) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const r = height / 2;
  const track = new Path();
  track.addRoundedRect(new Rect(0, 0, width, height), r, r);
  ctx.addPath(track);
  ctx.setFillColor(C.track);
  ctx.fillPath();
  const w = Math.max(height, width * Math.max(0, Math.min(1, fraction)));
  const fill = new Path();
  fill.addRoundedRect(new Rect(0, 0, w, height), r, r);
  ctx.addPath(fill);
  ctx.setFillColor(color);
  ctx.fillPath();
  return ctx.getImage();
}

function text(stack, s, size, color, weight = "regular") {
  const t = stack.addText(s);
  t.textColor = color;
  t.font =
    weight === "bold" ? Font.boldRoundedSystemFont(size)
    : weight === "semibold" ? Font.semiboldRoundedSystemFont(size)
    : Font.regularRoundedSystemFont(size);
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.6;
  return t;
}

// --- layouts ------------------------------------------------------------------

function small(w, r) {
  const d = r.data || {};
  text(w, "BATTERY", 11, C.muted, "semibold");
  w.addSpacer(2);
  const soc = w.addStack();
  soc.bottomAlignContent();
  text(soc, d.soc === null || d.soc === undefined ? "–" : String(d.soc), 40, C.text, "bold");
  text(soc, " %", 16, C.muted, "semibold");
  w.addSpacer(4);
  w.addImage(bar(120, 8, (d.soc ?? 0) / 100, socColor(d.soc)));
  w.addSpacer(6);
  text(w, fmtPower(d.power), 15, flowColor(d.current), "semibold");
  text(w, `${fmt(d.voltage, 2)} V · ${fmt(d.current, 1)} A`, 11, C.muted);
  w.addSpacer();
  const s = statusLine(r);
  text(w, s.text, 10, s.color);
}

function medium(w, r) {
  const d = r.data || {};
  const row = w.addStack();
  row.centerAlignContent();

  const left = row.addStack();
  left.layoutVertically();
  small(left, r);

  row.addSpacer(16);

  const right = row.addStack();
  right.layoutVertically();
  const kv = (k, v, color = C.text) => {
    const line = right.addStack();
    text(line, k, 12, C.muted);
    line.addSpacer();
    text(line, v, 12, color, "semibold");
    right.addSpacer(3);
  };
  kv("Remaining", `${fmt(d.capacity_remain, 0)} / ${fmt(d.nominal_capacity, 0)} Ah`);
  kv("Cell spread", d.delta_cell_voltage == null ? "–" : `${Math.round(d.delta_cell_voltage * 1000)} mV`);
  kv("Cells", `${fmt(d.min_cell_voltage, 3)}–${fmt(d.max_cell_voltage, 3)}`);
  kv("Temp", `${fmt(d.temp_t1)}° / ${fmt(d.temp_mos)}°`);
  const sw = (on) => (on ? "on" : "off");
  kv("Chg / Dis", `${sw(d.charge_on)} / ${sw(d.discharge_on)}`, d.charge_on && d.discharge_on ? C.ok : C.warn);
}

function lockRect(w, r) {
  const d = r.data || {};
  text(w, `🔋 ${d.soc ?? "–"}%  ${fmtPower(d.power)}`, 14, Color.white(), "semibold");
  text(w, `${fmt(d.voltage, 2)} V  ${fmt(d.current, 1)} A`, 12, Color.white());
  text(w, r.offline ? "offline" : d.stale ? "stale" : `Δ ${d.delta_cell_voltage == null ? "–" : Math.round(d.delta_cell_voltage * 1000)} mV`, 11, Color.white());
}

function lockCircle(w, r) {
  const d = r.data || {};
  w.addSpacer();
  const t = text(w, `${d.soc ?? "–"}%`, 16, Color.white(), "bold");
  t.centerAlignText();
  w.addSpacer();
}

// --- main ---------------------------------------------------------------------

const r = await load();
const w = new ListWidget();
w.url = BASE;
w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000); // iOS treats this as a hint

const family = config.widgetFamily || "medium";
if (family === "accessoryRectangular") lockRect(w, r);
else if (family === "accessoryCircular") lockCircle(w, r);
else {
  w.backgroundColor = C.bg;
  w.setPadding(14, 14, 14, 14);
  if (family === "small") small(w, r);
  else medium(w, r);
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else {
  family === "small" ? await w.presentSmall() : await w.presentMedium();
}
Script.complete();
