#!/bin/bash
# <swiftbar.title>JK BMS</swiftbar.title>
# <swiftbar.desc>Battery SOC, power and cell data from the jkbms-pi service.</swiftbar.desc>
# <swiftbar.dependencies>curl, jq (both ship with macOS 15+)</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>
#
# SwiftBar menu-bar plugin. The "10s" in the filename is the refresh interval; rename to change it.
# Set your dashboard URL below, or export JKBMS_URL via <swiftbar.environment>.

JKBMS_URL="${JKBMS_URL:-http://raspberryss.local:8080}"
JKBMS_URL="${JKBMS_URL%/}"

PATH="/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CACHE_DIR="${SWIFTBAR_PLUGIN_CACHE_PATH:-${TMPDIR:-/tmp}}"
CACHE="$CACHE_DIR/jkbms-current.json"
mkdir -p "$CACHE_DIR" 2>/dev/null

if ! command -v jq >/dev/null; then
  echo "🔋 jq? | color=#d97706"
  echo "---"
  echo "This plugin needs jq: brew install jq"
  exit 0
fi

offline=false
if body=$(curl -fsS --max-time 4 "$JKBMS_URL/api/current" 2>/dev/null) && jq -e . >/dev/null 2>&1 <<<"$body"; then
  printf '%s' "$body" >"$CACHE"
  fetched=$(date +%s)
  printf '%s' "$fetched" >"$CACHE.ts"
elif [[ -s "$CACHE" ]]; then
  offline=true
  body=$(cat "$CACHE")
  fetched=$(cat "$CACHE.ts" 2>/dev/null || echo 0)
else
  echo ":bolt.house: – | symbolize=true"
  echo "---"
  echo "Cannot reach $JKBMS_URL | color=#dc2626"
  echo "Check the Pi, or that you're on the home network | size=12"
  echo "---"
  echo "Open dashboard | href=$JKBMS_URL"
  echo "Refresh | refresh=true"
  exit 0
fi

jq -r \
  --arg url "$JKBMS_URL" \
  --argjson offline "$offline" \
  --argjson fetched "${fetched:-0}" \
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
def onoff: if . == null then "–" elif . then "on" else "off" end;

# colours: light,dark
def green: "#16a34a,#22c55e";
def orange: "#ea580c,#fb923c";
def amber: "#d97706,#f59e0b";
def red: "#dc2626,#f87171";
def grey: "#667085,#8d99a8";

. as $d
| ($d.current // 0) as $a
| (if $offline then grey elif ($a | fabs) < 0.05 then grey elif $a > 0 then green else orange end) as $flow
| ($d.soc) as $soc
| (if $soc == null then grey elif $soc < 10 then red elif $soc < 25 then amber else green end) as $socColor
| (if $offline then "offline"
   elif $d.link != "connected" then "BMS \($d.link)"
   elif $d.stale then "stale"
   else null end) as $problem

# --- menu bar line ---
# Plain text with the house symbol inline (":bolt.house:"), so SwiftBar renders it as part of the text and it
# sits on the same line as the numbers. A separate sfimage is drawn as its own image and ends up misaligned.
# A ⚠︎ marks a BMS problem, offline data, or SOC below 25%. Power is signed: negative while discharging.
| ($problem != null or ($soc != null and $soc < 25)) as $alert
| ":bolt.house: \($soc // "–")%  \($d.power | power)\(if $alert then " ⚠︎" else "" end) | symbolize=true",
  "---",

# --- dropdown ---
  (if $offline then "Offline · showing data from \((($now - $fetched) / 60) | floor) min ago | color=\(amber)"
   elif $problem then "⚠︎ \($problem) | color=\(amber)"
   else empty end),

  "State of charge   \($soc // "–")%  ·  \($d.capacity_remain | fx(1)) / \($d.nominal_capacity | fx(0)) Ah | font=Menlo size=12 color=\($socColor)",
  "Power             \($d.power | power) | font=Menlo size=12 color=\($flow)",
  "Current           \($d.current | fx(2)) A  (\(if ($a | fabs) < 0.05 then "idle" elif $a > 0 then "charging" else "discharging" end)) | font=Menlo size=12 color=\($flow)",
  "Voltage           \($d.voltage | fx(2)) V | font=Menlo size=12",
  "---",
  "Cell spread       \(if $d.delta_cell_voltage == null then "–" else ($d.delta_cell_voltage * 1000 | fx(0)) end) mV | font=Menlo size=12",
  "Lowest cell       \($d.min_cell_voltage | fx(3)) V  (#\($d.min_cell_index // "–")) | font=Menlo size=12 color=#2563eb,#60a5fa",
  "Highest cell      \($d.max_cell_voltage | fx(3)) V  (#\($d.max_cell_index // "–")) | font=Menlo size=12 color=\(red)",
  (if ($d.cell_voltages // []) | length > 0 then
     "All cells",
     ($d.cell_voltages | to_entries[] |
       "--#\(.key + 1 | tostring | if length < 2 then " " + . else . end)  \(.value | fx(3)) V | font=Menlo size=12\(
         if (.key + 1) == $d.min_cell_index then " color=#2563eb,#60a5fa"
         elif (.key + 1) == $d.max_cell_index then " color=\(red)"
         else "" end)")
   else empty end),
  "---",
  "Temperatures      T1 \($d.temp_t1 | fx(1))°  T2 \($d.temp_t2 | fx(1))°  MOS \($d.temp_mos | fx(1))° | font=Menlo size=12",
  "Switches          charge \($d.charge_on | onoff) · discharge \($d.discharge_on | onoff) · balance \($d.balance_on | onoff) | font=Menlo size=12 color=\(if ($d.charge_on and $d.discharge_on) then "" else amber end)",
  "Cycles            \($d.cycle_count // "–") | font=Menlo size=12",
  "Link              \($d.link)\(if $d.age_s != null then " · \($d.age_s | fx(0)) s ago" else "" end) | font=Menlo size=12 color=\(if $d.link == "connected" and ($d.stale | not) then green else amber end)",
  "---",
  "Open dashboard | href=\($url) sfimage=safari",
  "Refresh now | refresh=true sfimage=arrow.clockwise"
' <<<"$body" | sed 's/color= / /; s/color=$//'
