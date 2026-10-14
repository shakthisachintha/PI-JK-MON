# JK BMS Monitor for Raspberry Pi: Specification

**Version:** 1.0 · **Date:** 2026-10-14 · **Status:** Ready to implement

This document is self-contained. It describes a Raspberry Pi service that reads a JK BMS over Bluetooth Low Energy, stores every reading in a local SQLite database, and serves a REST API and web dashboard on the local network. It replaces the ESP32 + HiveMQ setup. All protocol details below are taken from the working ESP32 sketch (`jkbms-ble`), so the Pi project doesn't depend on that code.

---

## 1. Goals and non-goals

**Goals**
1. Keep one persistent BLE connection to a single JK BMS and decode its data.
2. Store a reading every 3 s (configurable) in SQLite on the Pi, with full history.
3. Serve a REST API and a single-page web dashboard on the LAN, showing live values plus history charts.
4. Run unattended as a `systemd` service, recover automatically from BLE drops, and survive reboots.

**Non-goals (v1)**
- Writing settings or toggling charge/discharge switches on the BMS. The service is read-only.
- Internet or cloud access, user accounts, HTTPS.
- More than one BMS. The code should not prevent adding this later (see §11).

## 2. Architecture

```
 JK BMS ──BLE──▶ ┌──────────────── Raspberry Pi 3 B ─────────────────┐
                 │  one Python process (asyncio event loop)          │
                 │                                                   │
                 │  BLE task ──frames──▶ Protocol decoder            │
                 │                          │ latest state (memory)  │
                 │  Sampler task (every 3 s) ─▶ SQLite (WAL)         │
                 │  Maintenance task (rollup + retention)            │
                 │  HTTP server (FastAPI/uvicorn) ◀── reads ─────────│
                 └───────────────────────────────────▲───────────────┘
                                                     │ HTTP :8080
                                          Browser on the local WiFi
```

- **One process, one event loop.** The BLE client, sampler, maintenance job and HTTP server are asyncio tasks in the same process and share the latest decoded state in memory. There's no message broker and no separate database server.
- The **decoder updates in-memory state** on every frame. The **sampler** writes that state to the database on a fixed interval. This separates the BMS frame rate from the storage rate.

## 3. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| OS | Raspberry Pi OS Lite (Bookworm), 64-bit or 32-bit | Ships BlueZ and Python 3.11 |
| Language | Python ≥ 3.11 | Pi 3 B handles it easily, quick to iterate |
| BLE | [`bleak`](https://github.com/hbldh/bleak) (BlueZ over D-Bus) | asyncio-native, standard on Linux |
| Database | SQLite 3 via stdlib `sqlite3`, WAL mode | No server process, a single file, easy to back up |
| Web/API | FastAPI + uvicorn | Small, async, automatic OpenAPI docs at `/docs` |
| Frontend | One static HTML page, vanilla JS, Chart.js (vendored locally) | No build step, works without internet |
| Process manager | systemd | Auto-start, restart on crash, journald logs |

DB writes are tiny (one row every 3 s), so wrapping synchronous `sqlite3` calls in `asyncio.to_thread()` is enough. Use a single writer connection, and a separate connection for HTTP reads.

## 4. Hardware and platform notes

- **Raspberry Pi 3 Model B:** onboard Bluetooth 4.1 (BLE supported) and 2.4 GHz WiFi only. BLE and WiFi share one radio chip. This works, but a wired Ethernet connection to the LAN makes BLE more reliable.
- **A JK BMS accepts only one BLE connection at a time.** Power off the ESP32 and close the JK phone app before the Pi tries to connect. Otherwise the Pi will find the BMS but fail to connect.
- **No real-time clock:** the Pi gets its time from NTP at boot. If the LAN has no internet, timestamps will be wrong after a power loss. Either make sure NTP is reachable or add an RTC module. Store all timestamps as UTC epoch.
- The service user must be in the `bluetooth` group. Bluetooth must be unblocked (`rfkill unblock bluetooth`).
- **SD card wear:** about 28,800 small inserts a day in WAL mode is fine for a decent card. Use a good-quality card and keep the retention limits in §7.4.

## 5. BMS BLE protocol (JK02, as used by the ESP32 sketch)

### 5.1 GATT

| Item | Value |
|---|---|
| Service UUID | `0000ffe0-0000-1000-8000-00805f9b34fb` |
| Characteristic UUID | `0000ffe1-0000-1000-8000-00805f9b34fb` (notify + write) |
| Writes | Write **without response** (`response=False` in bleak) |
| Target device | Matched by MAC address from config, case-insensitive (e.g. `c8:47:80:71:44:d4`) |

### 5.2 Commands (Pi → BMS)

A command is a 20-byte frame written to `ffe1`:

| Byte | Value |
|---|---|
| 0–3 | `AA 55 90 EB` |
| 4 | Register / command |
| 5 | Length |
| 6–9 | Value, uint32 little-endian |
| 10–18 | `00` |
| 19 | CRC = `sum(bytes[0..18]) & 0xFF` |

| Command | Register | Length | Value | Effect |
|---|---|---|---|---|
| `COMMAND_DEVICE_INFO` | `0x97` | `0x00` | `0` | BMS replies with a device-info frame (type `0x03`) |
| `COMMAND_CELL_INFO` | `0x96` | `0x00` | `0` | BMS replies with a settings frame (type `0x01`), then streams cell-data frames (type `0x02`) |

Example `COMMAND_CELL_INFO` frame: `AA 55 90 EB 96 00 00 00 00 00 00 00 00 00 00 00 00 00 00 10`

### 5.3 Connection sequence

1. Scan until an advertisement from the configured MAC is seen, then stop scanning.
2. Connect and subscribe to notifications on `ffe1`.
3. Wait 500 ms, send `0x97` (device info).
4. Wait 500 ms, send `0x96` (cell info). Record the time this request was sent.

### 5.4 Frame assembly (BMS → Pi)

Notifications arrive in chunks of any size (it depends on the MTU). Reassemble them like this:

- A chunk that **starts with** `55 AA EB 90` begins a new frame. Discard any partial buffer and start a new one.
- Other chunks are appended only while a frame is in progress. Stray chunks are dropped.
- Cap the buffer at 320 bytes.
- When the buffer reaches **≥ 300 bytes**, the frame is complete. Process the first 300 bytes, then reset.
- **Checksum (new on the Pi; the ESP32 skipped it):** `frame[299] == sum(frame[0..298]) & 0xFF`. Log and drop frames that fail.
- The frame type is `frame[4]`: `0x01` settings, `0x02` cell data, `0x03` device info. Log anything else at debug level with a hex dump.
- Do **not** port the ESP32's `ignoreNotifyCount` throttle. Decode every frame; the sampler controls the storage rate.

### 5.5 Field encoding

All multi-byte integers are **little-endian**. `u` = unsigned, `i` = signed. Value = raw × scale.

#### Cell-data frame (type `0x02`)

| Offset | Type | Scale | Field | Unit |
|---|---|---|---|---|
| 6 + 2·n (n = 0..15) | u16 | 0.001 | `cell_voltages[n]` | V |
| 74 | u16 | 0.001 | `avg_cell_voltage` | V |
| 76 | u16 | 0.001 | `delta_cell_voltage` | V |
| 80 + 2·n (n = 0..15) | u16 | 0.001 | `wire_resist[n]` | Ω |
| 144 | i16 | 0.1 | `temp_mos` | °C |
| 150 | u32 | 0.001 | `voltage` (pack) | V |
| 158 | i32 | 0.001 | `current` (+ charging, − discharging) | A |
| 162 | i16 | 0.1 | `temp_t1` | °C |
| 164 | i16 | 0.1 | `temp_t2` | °C |
| 170 | special¹ | 0.001 | `balance_current` | A |
| 172 | u8 | 1 | `balancing_action` (0 off, 1 charge-balancing, 2 discharge-balancing) | – |
| 173 | u8 | 1 | `soc` | % |
| 174 | u32 | 0.001 | `capacity_remain` | Ah |
| 178 | u32 | 0.001 | `nominal_capacity` | Ah |
| 182 | u32 | 1 | `cycle_count` | – |
| 186 | u32 | 0.001 | `cycle_capacity` | Ah |
| 194 | u24 | 1 | `bms_uptime_s` | s |
| 198 | u8 | >0 = on | `charge_on` | bool |
| 199 | u8 | >0 = on | `discharge_on` | bool |
| 201 | u8 | >0 = on | `balance_on` | bool |
| (derived) | – | – | `power = voltage × current` | W |

¹ `balance_current`: let `hi = frame[171]`, `lo = frame[170]`. If `hi & 0xF0 == 0x00`, the value is `+((hi << 8) | lo) × 0.001`. If `hi & 0xF0 == 0xF0`, it is `−(((hi & 0x0F) << 8) | lo) × 0.001`. This matches the ESP32 behaviour. Note it is **not** plain int16.

Only the first `cell_count` cells (from the settings frame) are real. If `cell_count` is unknown (0 or > 16), keep all 16 and let the UI hide 0.000 V cells.

#### Settings frame (type `0x01`)

All fields are 4 bytes. Temperatures are signed (i32); everything else is unsigned (u32).

| Offset | Scale | Field | Unit |
|---|---|---|---|
| 10 | 0.001 | `cell_uvp` (under-voltage protection) | V |
| 14 | 0.001 | `cell_uvpr` (under-voltage recovery) | V |
| 18 | 0.001 | `cell_ovp` (over-voltage protection) | V |
| 22 | 0.001 | `cell_ovpr` (over-voltage recovery) | V |
| 26 | 0.001 | `balance_trigger_voltage` | V |
| 46 | 0.001 | `power_off_voltage` | V |
| 50 | 0.001 | `max_charge_current` | A |
| 54 | 1 | `charge_ocp_delay` | s |
| 58 | 1 | `charge_ocp_recovery` | s |
| 62 | 0.001 | `max_discharge_current` | A |
| 66 | 1 | `discharge_ocp_delay` | s |
| 70 | 1 | `discharge_ocp_recovery` | s |
| 74 | 1 | `short_circuit_recovery` | s |
| 78 | 0.001 | `max_balance_current` | A |
| 82 | 0.1 | `charge_otp` | °C |
| 86 | 0.1 | `charge_otpr` | °C |
| 90 | 0.1 | `discharge_otp` | °C |
| 94 | 0.1 | `discharge_otpr` | °C |
| 98 | 0.1 | `charge_utp` | °C |
| 102 | 0.1 | `charge_utpr` | °C |
| 106 | 0.1 | `mos_otp` | °C |
| 110 | 0.1 | `mos_otpr` | °C |
| 114 | 1 | `cell_count` | – |
| 118 | 1 | `charge_switch` (0/1) | – |
| 122 | 1 | `discharge_switch` (0/1) | – |
| 126 | 1 | `balancer_switch` (0/1) | – |
| 130 | 0.001 | `total_battery_capacity` | Ah |
| 134 | 1 | `short_circuit_delay` | µs |
| 138 | 0.001 | `balance_starting_voltage` | V |

#### Device-info frame (type `0x03`)

ASCII fields: strip trailing `\x00` and whitespace.

| Offset | Length | Field |
|---|---|---|
| 6 | 16 | `vendor_id` |
| 22 | 8 | `hardware_version` |
| 30 | 8 | `software_version` |
| 38 | u32 | `uptime_s` |
| 42 | u32 | `power_on_count` |
| 46 | 16 | `device_name` |
| 62 | 16 | `device_passcode` ⚠ |
| 78 | 8 | `manufacturing_date` |
| 86 | 11 | `serial_number` |
| 97 | 5 | `passcode` ⚠ |
| 102 | 16 | `user_data` |
| 118 | 16 | `setup_passcode` ⚠ |

⚠ **Never store or expose the three passcode fields.** Drop them in the decoder.

### 5.6 Link supervision

| Condition | Action |
|---|---|
| Connected, no cell-data frame for **5 s**, and the last `0x96` request was **> 5 s** ago | Re-send `COMMAND_CELL_INFO` (`0x96`) |
| Connected, **no notifications of any kind for 20 s** | Disconnect; go back to scanning |
| Disconnected | Scan for the MAC (5 s scan windows, 2 s gap) and reconnect |
| BlueZ / D-Bus error | Log, back off 5 s → 10 s → 30 s (max), retry indefinitely |

Every connect, disconnect, re-request and timeout is written to the `events` table (§7.1).

### 5.7 Known risk: frame layout variant

The offsets above match the **JK02 (24-cell)** layout that the ESP32 sketch uses. Newer JK hardware (v11+, "JK02_32S") moves several fields further into the frame. If voltage, current or SOC look wrong:
1. Check the hardware version in the device-info frame.
2. Run the service with `--dump-frames`. This logs each complete frame as hex, and saves it to `frames/` with its type and timestamp.
3. Adjust the offsets in `protocol.py`. All offsets must be defined in that one module, never scattered elsewhere.

## 6. Runtime behaviour

- **In-memory state:** `latest_cells` (last decoded cell-data frame, plus the time it was received), `latest_settings`, `device_info`, `link_status` (`scanning | connecting | connected | disconnected`), plus counters (frames OK, CRC failures, reconnects).
- **Sampler:** every `SAMPLE_INTERVAL_S` (default 3 s), insert one `samples` row from `latest_cells` **only if it is fresher than `STALE_S`** (default 10 s). Stale or missing data writes nothing; gaps stay as gaps.
- **Settings:** when a settings frame decodes, insert into `settings_history` only if any value differs from the previous row.
- **Device info:** upsert the single `device_info` row.

## 7. Data storage (SQLite)

File: `DB_PATH` (default `/var/lib/jkbms-pi/jkbms.db`). On open, run `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;`. Manage schema versions with `PRAGMA user_version`, applying numbered migration functions at startup.

### 7.1 Schema

```sql
-- One row per stored reading (every SAMPLE_INTERVAL_S while fresh data exists)
CREATE TABLE samples (
  ts                 INTEGER PRIMARY KEY,   -- UTC epoch milliseconds
  voltage            REAL NOT NULL,         -- V
  current            REAL NOT NULL,         -- A (+charge / -discharge)
  power              REAL NOT NULL,         -- W
  soc                INTEGER NOT NULL,      -- %
  capacity_remain    REAL,                  -- Ah
  nominal_capacity   REAL,                  -- Ah
  cycle_count        INTEGER,
  cycle_capacity     REAL,                  -- Ah
  avg_cell_voltage   REAL,                  -- V
  delta_cell_voltage REAL,                  -- V
  min_cell_voltage   REAL,                  -- V (derived over active cells)
  max_cell_voltage   REAL,                  -- V (derived over active cells)
  balance_current    REAL,                  -- A
  temp_t1            REAL,                  -- °C
  temp_t2            REAL,                  -- °C
  temp_mos           REAL,                  -- °C
  charge_on          INTEGER NOT NULL,      -- 0/1
  discharge_on       INTEGER NOT NULL,      -- 0/1
  balance_on         INTEGER NOT NULL,      -- 0/1
  balancing_action   INTEGER,
  bms_uptime_s       INTEGER,
  cell_voltages      TEXT NOT NULL,         -- JSON array of active cells, e.g. [3.321,3.322,...]
  wire_resist        TEXT NOT NULL          -- JSON array, same length as cell_voltages
);

-- One row per minute, kept indefinitely
CREATE TABLE samples_1m (
  ts                 INTEGER PRIMARY KEY,   -- UTC epoch ms, start of the minute
  n                  INTEGER NOT NULL,      -- raw samples aggregated
  voltage_avg REAL, voltage_min REAL, voltage_max REAL,
  current_avg REAL, current_min REAL, current_max REAL,
  power_avg   REAL,
  soc_last    INTEGER,
  temp_t1_max REAL, temp_t2_max REAL, temp_mos_max REAL,
  cell_min    REAL, cell_max REAL,          -- lowest / highest single cell in the minute
  delta_cell_max REAL,
  cell_voltages_avg TEXT                    -- JSON array, per-cell average
);

CREATE TABLE settings_history (
  ts   INTEGER PRIMARY KEY,                 -- UTC epoch ms
  data TEXT NOT NULL                        -- JSON object with the §5.5 settings fields
);

CREATE TABLE device_info (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  updated_ts INTEGER NOT NULL,
  data       TEXT NOT NULL                  -- JSON object, passcodes excluded
);

CREATE TABLE events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     INTEGER NOT NULL,
  type   TEXT NOT NULL,   -- service_start | ble_connected | ble_disconnected | ble_timeout |
                          -- cell_info_rerequest | crc_error | settings_changed
  detail TEXT
);
CREATE INDEX events_ts ON events(ts);
```

### 7.2 Rollup

Every 60 s, aggregate any complete minutes from `samples` that aren't yet in `samples_1m`, using `INSERT OR REPLACE`. The job must be idempotent and must catch up after downtime.

### 7.3 Size estimate

About 450 B per raw row × 28,800 rows/day ≈ **13 MB/day**. Rollup data is about 1,440 rows/day ≈ 0.6 MB/day.

### 7.4 Retention (daily job, run at startup then every 24 h)

- Delete `samples` older than `RAW_RETENTION_DAYS` (default **30**). That caps raw data at about 400 MB.
- Keep `samples_1m` forever (≈ 220 MB/year).
- Delete `events` older than 90 days.
- Run `PRAGMA wal_checkpoint(TRUNCATE)` after deleting. Run `VACUUM` at most monthly.

## 8. HTTP API

Base URL: `http://<pi-host>:8080`. All responses are JSON. Timestamps are UTC epoch **milliseconds**, and the UI converts them to local time. CORS is open (`*`), since the service is LAN-only. Interactive docs are served at `/docs` (FastAPI default).

### 8.1 Endpoints

| Method & path | Purpose |
|---|---|
| `GET /api/health` | Liveness for monitoring |
| `GET /api/current` | Latest decoded state plus link status |
| `GET /api/settings` | Latest BMS settings |
| `GET /api/device` | Device info (no passcodes) |
| `GET /api/history` | Time series of scalar fields, auto-downsampled |
| `GET /api/history/cells` | Time series of per-cell voltages, auto-downsampled |
| `GET /api/events` | Recent link and service events |
| `GET /api/export.csv` | Raw samples as CSV for a time range |

### 8.2 Details

**`GET /api/health`** → `200`
```json
{"status":"ok","ble":"connected","last_frame_age_s":0.8,"db_ok":true,"service_uptime_s":86400,"version":"1.0.0"}
```
Return `status:"degraded"` if `last_frame_age_s > STALE_S` or the DB isn't writable. The endpoint still returns 200.

**`GET /api/current`** → `200`
```json
{
  "ts": 1760430000123, "age_s": 1.2, "stale": false, "link": "connected",
  "voltage": 53.12, "current": -4.21, "power": -223.6, "soc": 87,
  "capacity_remain": 243.6, "nominal_capacity": 280.0, "cycle_count": 41, "cycle_capacity": 11234.5,
  "avg_cell_voltage": 3.320, "delta_cell_voltage": 0.004, "min_cell_voltage": 3.318, "max_cell_voltage": 3.322,
  "min_cell_index": 7, "max_cell_index": 2,
  "balance_current": 0.0, "balancing_action": 0,
  "temp_t1": 24.1, "temp_t2": 23.9, "temp_mos": 27.5,
  "charge_on": true, "discharge_on": true, "balance_on": true,
  "bms_uptime_s": 1234567,
  "cell_voltages": [3.320, 3.322, "..."], "wire_resist": [0.051, 0.049, "..."]
}
```
If nothing has been decoded since startup, return `200` with `{"ts":null,"link":"scanning",...}` and every value set to `null`. Don't return 404; the UI needs one shape to render.

**`GET /api/history`**

| Param | Default | Notes |
|---|---|---|
| `from`, `to` | `now-24h`, `now` | Epoch ms **or** ISO-8601 |
| `fields` | `voltage,current,power,soc` | Comma list from: `voltage,current,power,soc,temp_t1,temp_t2,temp_mos,delta_cell_voltage,min_cell_voltage,max_cell_voltage,balance_current,capacity_remain` |
| `max_points` | `600` | Upper bound on returned rows (cap: 5000) |

Server-side downsampling:
- Bucket size = `max(SAMPLE_INTERVAL_S, ceil((to - from) / max_points))`.
- If the range is ≤ 6 h, aggregate from `samples`. Otherwise aggregate from `samples_1m`.
- Use the bucket average for each field, and also return `_min`/`_max` for `voltage` and `current`, so the chart can show a band.
- Buckets with no data are **omitted**. The client draws gaps where the spacing exceeds 2 × bucket size.

```json
{"from":1760343600000,"to":1760430000000,"bucket_s":144,"source":"samples_1m",
 "points":[{"ts":1760343600000,"voltage":53.1,"voltage_min":53.0,"voltage_max":53.2,"current":-4.2,"current_min":-6.1,"current_max":-2.0,"power":-223.0,"soc":88}]}
```

**`GET /api/history/cells`**: same `from`/`to`/`max_points` parameters. Returns `{"bucket_s":..., "cell_count":16, "points":[{"ts":..., "cells":[3.32, ...]}]}` with per-cell averages per bucket.

**`GET /api/events?limit=100&from=&to=`** returns the newest events first.

**`GET /api/export.csv?from=&to=`** streams raw `samples` rows. Expand cells into `cell_01..cell_NN` columns, and convert `ts` to ISO-8601 UTC. Limit the range to 31 days per request.

Errors use `400` with `{"error":"message"}` for bad parameters (e.g. `from > to`, unknown field).

## 9. Web dashboard

Served from `/` as static files: `index.html`, `app.js`, `style.css`, and a vendored `chart.umd.min.js`. Don't load anything from a CDN, so the dashboard works without internet. It needs no build step. It should work on desktop and phone (≥ 360 px wide) and in light and dark mode (`prefers-color-scheme`).

### 9.1 Layout (top to bottom)

1. **Status bar:** link status dot (green connected / amber scanning / red disconnected), "updated N s ago", and a stale warning banner when `stale` is true.
2. **Live tiles:** SOC % (large, with a capacity bar), pack voltage, current (coloured green when charging, orange when discharging), power, remaining Ah / nominal Ah, T1 / T2 / MOS temperatures, charge / discharge / balance switch states, cycle count.
3. **Cell voltages:** bar chart of the active cells with a y-axis zoomed to roughly min − 20 mV … max + 20 mV. Highlight the lowest cell (blue) and highest cell (red). Show the delta in mV, and show wire resistance on hover.
4. **History:** range buttons for `1h · 6h · 24h · 7d · 30d · custom`.
   - Chart A: voltage (with a min/max band) and SOC on a second axis.
   - Chart B: current and power.
   - Chart C: temperatures.
   - Chart D: per-cell voltages (one line per cell) plus delta. The chart has a toggle to show cells or the delta only.
5. **Events:** the last 20 events in a table.
6. **Settings & device:** collapsible tables from `/api/settings` and `/api/device`.

### 9.2 Behaviour

- Poll `/api/current` every 3 s. Pause polling while the tab is hidden (`document.visibilityState`).
- Fetch history charts when the range changes, and refresh them every 60 s for ranges ≤ 24 h.
- Store the selected range in the URL hash (e.g. `#range=24h`) so links can be shared.
- Show all times in the browser's local time zone.

## 10. Configuration

Read config from a TOML file (`/etc/jkbms-pi/config.toml`). Each key can be overridden by an environment variable with the `JKBMS_` prefix, e.g. `JKBMS_BMS_MAC`.

| Key | Default | Description |
|---|---|---|
| `bms_mac` | – (required) | BMS BLE MAC address, any letter case |
| `adapter` | `hci0` | BlueZ adapter |
| `db_path` | `/var/lib/jkbms-pi/jkbms.db` | SQLite file |
| `http_host` | `0.0.0.0` | Listen on all interfaces (LAN access) |
| `http_port` | `8080` | |
| `sample_interval_s` | `3` | Storage interval |
| `stale_s` | `10` | Readings older than this are not stored, and are flagged stale in the API |
| `raw_retention_days` | `30` | Raw `samples` retention |
| `cell_rerequest_s` | `5` | §5.6 |
| `link_timeout_s` | `20` | §5.6 |
| `log_level` | `INFO` | |

Command-line flags:
- `--config PATH`
- `--dump-frames` (§5.7)
- `--fake`: generates realistic synthetic BMS data instead of using BLE, so the API and UI can be developed on a laptop with no BMS or Bluetooth.

## 11. Project layout

```
jkbms-pi/
├── pyproject.toml              # deps: bleak, fastapi, uvicorn[standard]; dev: pytest, ruff
├── README.md                   # setup, run, deploy
├── config.example.toml
├── jkbms_pi/
│   ├── __main__.py             # CLI entry: parse args, load config, start tasks + uvicorn in one loop
│   ├── config.py
│   ├── protocol.py             # PURE: command builder, FrameAssembler, decode_cells/settings/device_info
│   ├── ble_client.py           # bleak: scan, connect, subscribe, supervision (§5.6), feeds FrameAssembler
│   ├── fake_source.py          # --fake data generator with the same interface as ble_client
│   ├── state.py                # in-memory latest state + link status
│   ├── store.py                # SQLite: migrations, inserts, queries, rollup, retention
│   ├── sampler.py              # periodic writer (§6)
│   ├── api.py                  # FastAPI app and routes (§8), mounts static/
│   └── static/                 # index.html, app.js, style.css, vendor/chart.umd.min.js
├── tests/
│   ├── test_protocol.py        # CRC, command bytes, assembler chunking, decode against fixture frames
│   ├── test_store.py           # migrations, rollup idempotency, retention, downsampling
│   ├── test_api.py             # FastAPI TestClient with a temp DB and fake data
│   └── fixtures/               # captured real frames (*.hex) once available
└── deploy/
    ├── jkbms-pi.service        # systemd unit
    └── install.sh              # creates user, dirs, venv, enables the service
```

`protocol.py` must have **no I/O and no asyncio**. It takes bytes in and returns dataclasses. That keeps it fully unit-testable, and it's the one place to change if the frame layout differs (§5.7). Keep the BLE source behind a small interface (`async run(on_frame, on_link_change)`) so that `fake_source` can stand in for it, and so a second BMS could be added later.

## 12. Deployment

```ini
# deploy/jkbms-pi.service
[Unit]
Description=JK BMS monitor
After=bluetooth.target network-online.target
Wants=bluetooth.target network-online.target

[Service]
User=jkbms
Group=jkbms
SupplementaryGroups=bluetooth
ExecStart=/opt/jkbms-pi/venv/bin/python -m jkbms_pi --config /etc/jkbms-pi/config.toml
Restart=always
RestartSec=5
StateDirectory=jkbms-pi

[Install]
WantedBy=multi-user.target
```

`install.sh` does the following:
1. `apt install python3-venv bluez`.
2. Create the `jkbms` system user.
3. Create a venv in `/opt/jkbms-pi`.
4. `pip install .`
5. Copy the example config if none exists.
6. `systemctl enable --now jkbms-pi`.

**Access from the LAN:** Raspberry Pi OS runs Avahi, so the dashboard is at `http://<hostname>.local:8080` (e.g. `http://raspberrypi.local:8080`), or at the Pi's IP. Give the Pi a DHCP reservation in the router so the IP doesn't change. Logs: `journalctl -u jkbms-pi -f`.

**Backup:** `sqlite3 /var/lib/jkbms-pi/jkbms.db ".backup /path/backup.db"` is safe while the service is running.

## 13. Security

- LAN-only. There is no authentication in v1. Don't port-forward 8080 on the router.
- The service is read-only towards the BMS. It never writes registers other than `0x96` and `0x97`.
- BMS passcodes are never stored, logged or returned (§5.5).
- Optional later: a single shared token, checked by a FastAPI dependency and set in config.

## 14. Acceptance criteria

1. With the ESP32 powered off, the service finds the configured BMS, connects within 30 s of start, and `/api/current` shows `link:"connected"` and `stale:false`.
2. `samples` grows by about 20 rows per minute (one per 3 s), with no gaps longer than 10 s while the BMS is in range.
3. Values in `/api/current` match the JK phone app (after disconnecting the Pi): pack voltage within 0.01 V, SOC exact, cell voltages within 0.001 V.
4. When the BMS is powered off for 2 minutes and back on, the service reconnects without a restart, logs `ble_timeout`/`ble_disconnected`/`ble_connected` events, and the history chart shows a gap, not interpolated data.
5. After a Pi reboot, the service starts by itself and history from before the reboot is still available.
6. A phone and a laptop on the same WiFi can open the dashboard at the same time. The live tiles update every 3 s, and a 30-day history view loads in under 2 s.
7. `pytest` passes on a laptop with no Bluetooth. `python -m jkbms_pi --fake` serves a working dashboard on a laptop.
8. Raw samples older than `raw_retention_days` are removed, and `samples_1m` keeps the full history.

## 15. Suggested build order

1. `protocol.py` and its tests, using synthetic frames built from the §5.5 tables.
2. `store.py` and `sampler.py` with `--fake` → verify data lands in SQLite.
3. `api.py` endpoints → check them with `/docs`.
4. The static dashboard against `--fake`.
5. `ble_client.py` on the Pi against the real BMS. Capture real frames with `--dump-frames` into `tests/fixtures/`, and add decode tests that assert the values shown in the JK app.
6. systemd deployment, then a 24 h unattended soak test against the §14 criteria.
