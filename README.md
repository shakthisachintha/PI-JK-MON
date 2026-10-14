# jkbms-pi

Reads a JK BMS over Bluetooth LE on a Raspberry Pi, stores every reading in SQLite, and serves a REST API
and web dashboard on the LAN. The full design is in [raspberry-pi-spec.md](raspberry-pi-spec.md).

```
JK BMS ──BLE──▶ Python service (asyncio: BLE · sampler · rollup/retention · FastAPI) ──▶ SQLite (WAL)
                                                │ HTTP :8080
                                   Next.js dashboard (static export) + /api/*
```

## Layout

| Path | What |
|---|---|
| `jkbms_pi/protocol.py` | Pure JK02 frame codec: commands, `FrameAssembler`, decoders. **All frame offsets live here.** |
| `jkbms_pi/ble_client.py` | bleak: scan, connect, subscribe, link supervision and backoff |
| `jkbms_pi/fake_source.py` | `--fake` synthetic BMS (emits real encoded frames) |
| `jkbms_pi/service.py` | Wires the source to decoder, state and store; rollup/retention loop |
| `jkbms_pi/state.py`, `sampler.py`, `store.py`, `api.py`, `config.py` | As in spec §11 |
| `jkbms_pi/static/` | Built dashboard (generated, git-ignored) |
| `web/` | Next.js dashboard source (TypeScript, Chart.js) |
| `deploy/` | systemd unit, `install.sh` (installer), `deploy.sh` (build + install, locally or over SSH) |

## Develop on a laptop (no Bluetooth needed)

```bash
# backend
uv venv && uv pip install -e ".[dev]"        # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python -m jkbms_pi --fake           # API on http://localhost:8080, docs at /docs
.venv/bin/python -m jkbms_pi --fake --seed-days 30   # prefill 30 days of synthetic history first

# dashboard, with hot reload on http://localhost:3000 (talks to the API on :8080)
cd web && bun install && bun run dev

# production build → copies the static export into jkbms_pi/static, served at http://localhost:8080/
cd web && bun run build
```

`--fake` uses `./jkbms-fake.db` unless `db_path` is set explicitly.

## Deploy to the Pi

The dashboard is built with Bun (which also needs Node.js for `next build`) and served as static files, so the
service itself never needs Bun or Node.js. Build wherever you have them:

```bash
./deploy/deploy.sh                       # on the Pi: build and install in place
./deploy/deploy.sh user@raspberrypi.local   # from another computer: build, copy to ~/jkbms-pi on the Pi, install there
```

Either way this builds `web/` and runs `deploy/install.sh` as root, which
installs `python3-venv bluez`, creates the `jkbms` user (in the `bluetooth` group), a venv in `/opt/jkbms-pi`,
copies `config.example.toml` to `/etc/jkbms-pi/config.toml` if missing, and enables the `jkbms-pi` service.

Then:

1. Set `bms_mac` in `/etc/jkbms-pi/config.toml` and `sudo systemctl restart jkbms-pi`.
2. **Power off the ESP32 and close the JK phone app**: the BMS accepts only one BLE connection.
3. Open `http://raspberrypi.local:8080`. Logs: `journalctl -u jkbms-pi -f`.

Config keys can be overridden with environment variables (`JKBMS_BMS_MAC=...`). See
[config.example.toml](config.example.toml).

Backup while running: `sqlite3 /var/lib/jkbms-pi/jkbms.db ".backup /path/backup.db"`.

### First run against real hardware

The offsets match the JK02 24-cell layout used by the ESP32 sketch. Newer hardware (v11+, JK02_32S) moves fields.
If voltage, current or SOC look wrong:

```bash
sudo systemctl stop jkbms-pi
sudo -u jkbms /opt/jkbms-pi/venv/bin/python -m jkbms_pi --config /etc/jkbms-pi/config.toml --dump-frames
```

Frames are logged as hex and saved to `/var/lib/jkbms-pi/frames/`. Copy a few into `tests/fixtures/`, add
assertions that match the JK app, and adjust offsets in `protocol.py`.

## Widgets

**iPhone (Scriptable):** [widgets/scriptable-jkbms.js](widgets/scriptable-jkbms.js). Paste it into a new Scriptable
script, add a Scriptable widget (small, medium or lock screen) and set its *Parameter* to your dashboard URL.
On macOS Sonoma or later, the same widget can be placed on the Mac desktop through *Edit Widgets → iPhone*.

**Mac menu bar (SwiftBar):** [widgets/swiftbar/jkbms.10s.sh](widgets/swiftbar/jkbms.10s.sh) shows SOC and power in the
menu bar, with cells, temperatures and link status in the dropdown. It needs only `curl` and `jq` (built into macOS 15+).

```bash
brew install --cask swiftbar          # pick a plugin folder on first launch
cp widgets/swiftbar/jkbms.10s.sh "<plugin folder>/"
```

Edit `JKBMS_URL` at the top of the copied file. The `10s` in the filename is the refresh interval.
When the Pi is unreachable, both widgets show the last values they fetched, marked as offline.

## API

| Endpoint | |
|---|---|
| `GET /api/health` | `status` is `degraded` when the last cell frame is older than `stale_s` or the DB is not writable |
| `GET /api/current` | Latest decoded values; all `null` before the first frame |
| `GET /api/settings`, `/api/device` | `{ts, settings}` / `{ts, device}`; passcodes are never decoded |
| `GET /api/history?from&to&fields&max_points` | Downsampled series; `from`/`to` in epoch ms or ISO-8601 |
| `GET /api/history/cells?from&to&max_points` | Per-cell averages per bucket |
| `GET /api/events?limit&from&to` | Newest first |
| `GET /api/system` | Pi health (CPU temp, under-voltage/throttling, CPU, memory, storage, WiFi) and BLE link (RSSI, recovery state) |
| `GET /api/export.csv?from&to` | Raw samples, max 31 days |

Bad parameters return `400 {"error": "..."}`. Interactive docs: `/docs`.

## Link recovery

Everything is driven by the time since the last **cell-data frame**:

| Stalled for | Action | Event |
|---|---|---|
| `cell_rerequest_s` (5 s) | Re-send cell-info request | `cell_info_rerequest` |
| `link_timeout_s` (20 s) | Disconnect, rescan, reconnect | `ble_timeout`, `ble_disconnected` |
| `adapter_reset_after_s` (2 min), then 4, 8, … min (cap 30) | Power-cycle the Bluetooth adapter | `adapter_reset` |
| after `restart_after_resets` (3) resets | Exit with code 75; systemd restarts `bluetooth.service`, then this service | `service_restart` |

Set `restart_after_resets = 0` to never exit. The dashboard's *Raspberry Pi & link* section shows the current
stage, the number of resets, and the BMS signal strength. Live RSSI is read with `btmgmt` (the unit grants
`CAP_NET_ADMIN`); if that isn't available, the RSSI seen at the last scan is shown instead.

After pulling these changes onto an existing install, re-run `./deploy/deploy.sh` so the updated systemd unit
(capabilities, `video` group, BlueZ restart hook) is installed.

## Notes and deviations from the spec

- **Dashboard** is a Next.js app (static export) instead of vanilla JS. It is still served by FastAPI from `/`,
  bundles Chart.js, and loads nothing from a CDN.
- `samples_1m` has two extra columns (`balance_current_avg`, `capacity_remain_last`) so every `/api/history` field
  works on ranges over 6 h. A `meta` table tracks the monthly `VACUUM`.
- History buckets are aligned to epoch multiples, so they stay stable across refreshes. Ranges are half-open
  `[from, to)`. When alignment would add one bucket over `max_points`, the bucket is widened by a second. Ranges over
  6 h use a minimum bucket of 60 s, since they read the 1-minute rollups.
- `min_cell_index` / `max_cell_index` are **1-based**, matching the JK app's cell numbering.
- Link liveness counts only cell-data frames, not "any notification" (§5.6). The BMS acknowledges every command
  with a short notification, so re-requesting cell info used to keep a dead data stream looking alive forever.
- `crc_error` events are rate-limited to one per minute (with a count), so a noisy link can't flood the table.
- Retention never deletes raw samples that haven't been rolled up yet.
- `/api/health` also returns frame counters (`frames_ok`, `crc_errors`, `reconnects`, `unknown_frames`).
