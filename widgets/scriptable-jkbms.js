// Ray Vault widget for Scriptable (iOS): battery SOC and flow, solar, load, grid and inverter status.
//
// Setup:
//   1. Install "Scriptable" from the App Store, create a new script and paste this file into it.
//   2. Add a Scriptable widget (small, medium, or lock-screen rectangular/circular) to your screen.
//   3. Long-press the widget → Edit Widget → Script: this script.
//      Parameter: your dashboard URL, e.g. http://raspberryss.local:8080 (or set DEFAULT_URL below).
//   Tapping the widget opens the dashboard.

const DEFAULT_URL = "http://raspberryss.local:8080";
const BASE = ((args.widgetParameter || DEFAULT_URL) + "").trim().replace(/\/+$/, "");
const CACHE = "ray-vault-widget-cache.json";

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
  solar: Color.dynamic(new Color("#ca8a04"), new Color("#facc15")),
};

// --- data ---------------------------------------------------------------------

const fm = FileManager.local();
const cachePath = fm.joinPath(fm.cacheDirectory(), CACHE);

async function load() {
  try {
    const req = new Request(`${BASE}/api/overview`);
    req.timeoutInterval = 8;
    const o = await req.loadJSON();
    if (req.response.statusCode !== 200 || !o.bms) throw new Error(`HTTP ${req.response.statusCode}`);
    const r = { o, fetched: Date.now(), offline: false };
    fm.writeString(cachePath, JSON.stringify(r));
    return r;
  } catch (e) {
    // Away from home WiFi (or Pi down): show the last values we saw.
    if (fm.fileExists(cachePath)) return { ...JSON.parse(fm.readString(cachePath)), offline: true };
    return { o: null, fetched: null, offline: true };
  }
}

// --- helpers ------------------------------------------------------------------

function fmtPower(w, signed = false) {
  if (w === null || w === undefined) return "–";
  const s = Math.abs(w) >= 1000 ? `${(Math.abs(w) / 1000).toFixed(2)} kW` : `${Math.round(Math.abs(w))} W`;
  return signed && Math.round(w) !== 0 ? `${w > 0 ? "+" : "−"}${s}` : s;
}

function flow(o) {
  const w = o?.battery_power;
  if (w === null || w === undefined || Math.abs(w) < 10) return { label: "idle", color: C.muted };
  return w > 0 ? { label: "charging", color: C.charge } : { label: "discharging", color: C.discharge };
}

function socColor(soc) {
  if (soc === null || soc === undefined) return C.muted;
  return soc < 10 ? C.bad : soc < 25 ? C.warn : C.ok;
}

/** "Solar → battery → mains (SBU)" -> "SBU" */
function shortLabel(label) {
  const m = /\(([^)]+)\)\s*$/.exec(label || "");
  return m ? m[1] : label || "";
}

function hasInverter(o) {
  return o && o.inverter && o.inverter.link !== "disabled";
}

function statusLine(r) {
  const o = r.o;
  if (!o) return { text: "No data", color: C.bad };
  if (r.offline) {
    const age = r.fetched ? Math.round((Date.now() - r.fetched) / 60000) : null;
    return { text: age === null ? "Offline" : `Offline · ${age} min old`, color: C.warn };
  }
  if (o.inverter?.fault_codes?.length) return { text: `Inverter fault ${o.inverter.fault_codes.join(", ")}`, color: C.bad };
  if (o.bms.link !== "connected") return { text: `BMS ${o.bms.link}`, color: C.warn };
  if (o.bms.stale) return { text: "BMS data stale", color: C.warn };
  if (hasInverter(o) && o.inverter.link !== "connected")
    return { text: `Inverter ${o.inverter.link.replace("_", " ")}`, color: C.warn };
  const t = new Date(o.ts);
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

function icon(stack, name, size, color) {
  const sym = SFSymbol.named(name);
  if (!sym) return;
  sym.applyFont(Font.semiboldSystemFont(size));
  const img = stack.addImage(sym.image);
  img.imageSize = new Size(size + 2, size + 2);
  img.tintColor = color;
}

/** A row like "☀ 402 W" (icon + value). */
function iconValue(stack, name, value, color, size = 12) {
  const row = stack.addStack();
  row.centerAlignContent();
  row.spacing = 3;
  icon(row, name, size - 1, color);
  text(row, value, size, C.text, "semibold");
  return row;
}

// --- layouts ------------------------------------------------------------------

function socBlock(w, o, big = 40) {
  text(w, "RAY VAULT", 11, C.muted, "semibold");
  w.addSpacer(2);
  const soc = w.addStack();
  soc.bottomAlignContent();
  text(soc, o?.soc === null || o?.soc === undefined ? "–" : String(o.soc), big, C.text, "bold");
  text(soc, " %", 16, C.muted, "semibold");
  w.addSpacer(4);
  w.addImage(bar(120, 8, (o?.soc ?? 0) / 100, socColor(o?.soc)));
  w.addSpacer(6);
  const f = flow(o);
  text(w, `${fmtPower(o?.battery_power, true)} ${f.label}`, 13, f.color, "semibold");
}

function small(w, r) {
  const o = r.o;
  socBlock(w, o);
  if (hasInverter(o)) {
    w.addSpacer(4);
    const row = w.addStack();
    row.centerAlignContent();
    iconValue(row, "sun.max.fill", fmtPower(o.pv_power), C.solar, 11);
    row.addSpacer(8);
    iconValue(row, "house.fill", fmtPower(o.load_power), C.muted, 11);
  }
  w.addSpacer();
  const s = statusLine(r);
  text(w, s.text, 10, s.color);
}

function medium(w, r) {
  const o = r.o;
  const row = w.addStack();

  const left = row.addStack();
  left.layoutVertically();
  socBlock(left, o);
  left.addSpacer();
  const s = statusLine(r);
  text(left, s.text, 10, s.color);

  row.addSpacer(16);

  const right = row.addStack();
  right.layoutVertically();
  const kv = (name, k, v, color = C.text, tint = C.muted) => {
    const line = right.addStack();
    line.centerAlignContent();
    icon(line, name, 11, tint);
    line.addSpacer(5);
    text(line, k, 12, C.muted);
    line.addSpacer();
    text(line, v, 12, color, "semibold");
    right.addSpacer(4);
  };
  if (hasInverter(o)) {
    kv("sun.max.fill", "Solar", fmtPower(o.pv_power), o.pv_power > 0 ? C.text : C.muted, C.solar);
    kv("house.fill", "Load", fmtPower(o.load_power));
    kv("powerplug.fill", "Grid", o.grid_voltage == null ? "–" : `${Math.round(o.grid_voltage)} V`);
    const prio = o.priorities?.output_priority;
    kv("bolt.fill", "Inverter", `${o.machine_state ?? "–"}${prio ? ` · ${shortLabel(prio.label)}` : ""}`);
  }
  const b = o?.bms;
  kv("battery.75percent", "Pack", b?.voltage == null ? "–" : `${b.voltage.toFixed(2)} V · ${b.current.toFixed(1)} A`);
}

function lockRect(w, r) {
  const o = r.o;
  const white = Color.white();
  text(w, `🔋 ${o?.soc ?? "–"}%  ${fmtPower(o?.battery_power, true)}`, 14, white, "semibold");
  if (hasInverter(o)) text(w, `☀︎ ${fmtPower(o.pv_power)}   ⌂ ${fmtPower(o.load_power)}`, 12, white);
  const s = statusLine(r);
  text(w, r.offline || s.color !== C.muted ? s.text : o?.machine_state ?? s.text, 11, white);
}

function lockCircle(w, r) {
  w.addSpacer();
  const t = text(w, `${r.o?.soc ?? "–"}%`, 16, Color.white(), "bold");
  t.centerAlignText();
  w.addSpacer();
}

// --- main ---------------------------------------------------------------------

const r = await load();
const w = new ListWidget();
w.url = BASE + "/";
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
