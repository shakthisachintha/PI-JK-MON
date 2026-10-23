#!/bin/bash
# <swiftbar.title>Ray Vault</swiftbar.title>
# <swiftbar.desc>Battery SOC and power, solar, load, grid and inverter status from the Ray Vault service.</swiftbar.desc>
# <swiftbar.dependencies>curl, jq (both ship with macOS 15+)</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>
#
# SwiftBar menu-bar plugin. The "10s" in the filename is the refresh interval; rename to change it.
# Settings (edit here, or set them via <swiftbar.environment>):
#   RAYVAULT_URL  dashboard URL
#   SHOW_SOLAR    1 = also show solar power in the menu bar while the panels produce, 0 = SOC + battery only

RAYVAULT_URL="${RAYVAULT_URL:-${JKBMS_URL:-http://raspberryss.local:8080}}"
RAYVAULT_URL="${RAYVAULT_URL%/}"
SHOW_SOLAR="${SHOW_SOLAR:-1}"

PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CACHE_DIR="${SWIFTBAR_PLUGIN_CACHE_PATH:-${TMPDIR:-/tmp}}"
CACHE="$CACHE_DIR/ray-vault.json"
mkdir -p "$CACHE_DIR" 2>/dev/null

if ! command -v jq >/dev/null; then
  echo "🔋 jq? | color=#d97706"
  echo "---"
  echo "This plugin needs jq: brew install jq"
  exit 0
fi

fetch() { curl -fsS --max-time "$2" "$RAYVAULT_URL$1" 2>/dev/null; }

offline=false
if ov=$(fetch /api/overview 4) && jq -e .ts >/dev/null 2>&1 <<<"$ov"; then
  bms=$(fetch /api/bms/current 3) && jq -e . >/dev/null 2>&1 <<<"$bms" || bms=null
  body=$(jq -n --argjson ov "$ov" --argjson bms "$bms" --argjson fetched "$(date +%s)" \
    '{ov: $ov, bms: $bms, fetched: $fetched}')
  printf '%s' "$body" >"$CACHE"
elif [[ -s "$CACHE" ]]; then
  offline=true
  body=$(cat "$CACHE")
else
  echo ":bolt.house: – | symbolize=true"
  echo "---"
  echo "Cannot reach $RAYVAULT_URL | color=#dc2626"
  echo "Check the Pi, or that you're on the home network | size=12"
  echo "---"
  echo "Open dashboard | href=$RAYVAULT_URL"
  echo "Refresh | refresh=true"
  exit 0
fi

jq -r \
  --arg url "$RAYVAULT_URL" \
  --argjson offline "$offline" \
  --argjson showSolar "$([[ $SHOW_SOLAR == 1 ]] && echo true || echo false)" \
  --argjson now "$(date +%s)" '
# fixed-point formatting: 53.1 | fx(2) -> "53.10"; null -> "–"
def fx(n):
  if . == null then "–"
  else (. * pow(10; n) | round) as $i
    | ($i | tostring) as $s
    | if n == 0 then $s
      else ($s | ltrimstr("-")) as $u
        | ("0" * (n + 1 - ($u | length)) + $u) as $p
        | (if $i < 0 then "-" else "" end) + $p[0:($p | length) - n] + "." + $p[($p | length) - n:]
      end
  end;
def power: if . == null then "–" elif (. | fabs) >= 1000 then "\(. / 1000 | fx(2)) kW" else "\(. | fx(0)) W" end;
def signed_power: if . == null then "–" elif . > 0 then "+" + (. | power) else (. | power) end;
def onoff: if . == null then "–" elif . then "on" else "off" end;
def words: if . == null then "–" else gsub("_"; " ") end;
def row($l; $v): "\($l + "                  " | .[0:16]) \($v)";

# colours: light,dark
def green: "#16a34a,#22c55e";
def orange: "#ea580c,#fb923c";
def amber: "#d97706,#f59e0b";
def red: "#dc2626,#f87171";
def blue: "#2563eb,#60a5fa";
def mono: "font=Menlo size=12";

.ov as $o | .bms as $d | .fetched as $fetched
| $o.soc as $soc
| $o.battery_power as $bw
| ($o.pv_power // 0) as $pv
| (if $bw == null or ($bw | fabs) < 10 then "idle" elif $bw > 0 then "charging" else "discharging" end) as $flow
| (if $flow == "charging" then green elif $flow == "discharging" then orange else "" end) as $flowColor
| (if $soc != null and $soc < 10 then red elif $soc != null and $soc < 25 then amber else $flowColor end) as $batteryColor
| ($o.inverter.link != "disabled") as $hasInv
| ($o.inverter.fault_codes // []) as $faults
| [
    (if $offline then "offline" else empty end),
    (if ($faults | length) > 0 then "inverter fault \($faults | map(tostring) | join(", "))" else empty end),
    (if $o.bms.link != "connected" then "BMS \($o.bms.link | words)" elif $o.bms.stale then "BMS data stale" else empty end),
    (if $hasInv and $o.inverter.link != "connected" then "inverter \($o.inverter.link | words)"
     elif $hasInv and $o.inverter.stale then "inverter data stale" else empty end),
    (if $soc != null and $soc < 25 then "low battery" else empty end)
  ] as $problems

# --- menu bar line (plain text; the inline :symbol: keeps the icon aligned with the numbers) ---
| ":bolt.house: \($soc // "–")%  \($bw | signed_power)\(
    if $showSolar and $hasInv and $pv > 0 then "  :sun.max: \($pv | power)" else "" end)\(
    if ($problems | length) > 0 then " ⚠︎" else "" end) | symbolize=true",
  "---",

# --- problems ---
  (if $offline then "Offline · showing data from \((($now - $fetched) / 60) | floor) min ago | color=\(amber)" else empty end),
  ($problems[] | select(. != "offline") | "⚠︎ \(.) | color=\(if test("fault") then red else amber end)"),
  (if ($problems | length) > 0 then "---" else empty end),

# --- power flows ---
  "\(row("Battery"; "\($soc // "–")%  ·  \($bw | signed_power) \($flow)")) | \(mono) color=\($batteryColor)",
  (if $hasInv then
     "\(row("Solar"; $o.pv_power | power)) | \(mono) color=\(if $pv > 0 then green else "" end)",
     "\(row("Load"; $o.load_power | power)) | \(mono)",
     "\(row("Grid"; "\($o.grid_voltage | fx(0)) V\(if ($o.grid_power // 0) > 0 then "  ·  drawing \($o.grid_power | power)" else "" end)")) | \(mono)",
     "\(row("Inverter"; "\($o.machine_state // "–")\(if $o.priorities.output_priority then "  ·  " + $o.priorities.output_priority.label else "" end)")) | \(mono)",
     (if $o.priorities.charge_priority then "\(row("Charger"; $o.priorities.charge_priority.label)) | \(mono)" else empty end)
   else empty end),
  "---",

# --- battery details (submenu) ---
  (if $d != null and $d.voltage != null then
     "Battery details",
     "--\(row("Voltage"; "\($d.voltage | fx(2)) V  ·  \($d.current | fx(1)) A")) | \(mono)",
     "--\(row("Remaining"; "\($d.capacity_remain | fx(1)) / \($d.nominal_capacity | fx(0)) Ah")) | \(mono)",
     "--\(row("Cell spread"; "\(if $d.delta_cell_voltage == null then "–" else ($d.delta_cell_voltage * 1000 | fx(0)) end) mV")) | \(mono)",
     "--\(row("Lowest cell"; "\($d.min_cell_voltage | fx(3)) V  (#\($d.min_cell_index // "–"))")) | \(mono) color=\(blue)",
     "--\(row("Highest cell"; "\($d.max_cell_voltage | fx(3)) V  (#\($d.max_cell_index // "–"))")) | \(mono) color=\(red)",
     "-----",
     ($d.cell_voltages // [] | to_entries[] |
       "--\(row("Cell \(.key + 1)"; "\(.value | fx(3)) V")) | \(mono)\(
         if (.key + 1) == $d.min_cell_index then " color=" + blue
         elif (.key + 1) == $d.max_cell_index then " color=" + red
         else "" end)"),
     "-----",
     "--\(row("Temperatures"; "T1 \($d.temp_t1 | fx(1))°  T2 \($d.temp_t2 | fx(1))°  MOS \($d.temp_mos | fx(1))°")) | \(mono)",
     "--\(row("Switches"; "charge \($d.charge_on | onoff) · discharge \($d.discharge_on | onoff) · balance \($d.balance_on | onoff)")) | \(mono)\(if ($d.charge_on and $d.discharge_on) then "" else " color=" + amber end)",
     "--\(row("Cycles"; $d.cycle_count // "–")) | \(mono)"
   else empty end),

# --- links ---
  "\(row("BMS link"; "\($o.bms.link | words)\(if $o.bms.rssi != null then "  ·  \($o.bms.rssi) dBm" else "" end)")) | \(mono) color=\(if $o.bms.link == "connected" and ($o.bms.stale | not) then green else amber end)",
  (if $hasInv then
     "\(row("Inverter link"; $o.inverter.link | words)) | \(mono) color=\(if $o.inverter.link == "connected" and ($o.inverter.stale | not) then green else amber end)"
   else empty end),
  "---",
  "Open dashboard | href=\($url)/ sfimage=safari",
  (if $hasInv then "Inverter settings | href=\($url)/inverter/ sfimage=slider.horizontal.3" else empty end),
  "Refresh now | refresh=true sfimage=arrow.clockwise"
' <<<"$body" | sed 's/ color= / /; s/ color=$//'
