# Ray Vault

Monitors and controls the Ray Vault power system from a Raspberry Pi: a **JK BMS** over Bluetooth LE and an
**SRNE HF2430 inverter** over Modbus RS485. Every reading goes into SQLite; a REST API and a web dashboard are
served on the LAN, and inverter settings can be changed from the dashboard behind a PIN.
The original single-device design is in [raspberry-pi-spec.md](raspberry-pi-spec.md).

```
JK BMS ──BLE──────▶ ┌─────────────── one Python process (asyncio) ───────────────┐
                    │ bms driver ─┐                                               │
SRNE ──RS485/USB──▶ │ inverter ───┼─▶ sampler ─▶ SQLite (WAL) ◀─ rollup/retention │
inverter            │ driver    ◀─┼── controls (PIN, validation, BMS guardrails)  │
                    │ watchdog    └─▶ FastAPI ─▶ /api/* + Next.js dashboard :8080 │
                    └─────────────────────────────────────────────────────────────┘
```

**Reliability:** each device driver runs in its own supervised task, so a crash or a dead link on one device
never stops the other, the database or the API. Both links recover by themselves (see *Link recovery*), and a
systemd watchdog restarts the service if the event loop or the database ever hangs.

## Layout

| Path | What |
|---|---|
| `ray_vault/core/` | Config, SQLite store and migrations, generic time series (`timeseries.py`), service supervisor, Pi stats, systemd watchdog |
| `ray_vault/bms/` | JK BMS: `protocol.py` (pure frame codec, **all offsets live here**), BLE client + recovery, RSSI, fake source, driver |
| `ray_vault/inverter/` | SRNE: `registers.py` (register/setting model), `profiles/hf.py` (HF2430 map), `bus.py` (Modbus), driver, `controls.py`, fake, probe |
| `ray_vault/api/` | FastAPI routers: `routes_bms.py`, `routes_inverter.py`, `routes_system.py` |
| `ray_vault/static/` | Built dashboard (generated, git-ignored) |
| `web/` | Next.js dashboard (TypeScript, Tailwind, React Query, Chart.js): Overview, Battery, Inverter, System |
| `deploy/` | systemd unit, polkit rule, `install.sh`, `deploy.sh` |
| `docs/inverter/` | SRNE Modbus protocol PDFs |
| `widgets/` | iPhone (Scriptable) and Mac menu bar (SwiftBar) widgets |

## Develop on a laptop (no hardware needed)

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python -m ray_vault --fake                  # fake BMS + fake inverter, http://localhost:8080 (PIN 1234)
.venv/bin/python -m ray_vault --fake --seed-days 30   # prefill 30 days of synthetic history first

cd web && bun install && bun run dev    # dashboard with hot reload on :3000, talking to the API on :8080
cd web && bun run build                 # static export copied into ray_vault/static
```

`--fake` uses `./ray-vault-fake.db` unless `[storage] db_path` is set. The fake inverter keeps its settings in
memory, so every control works end to end.

## Deploy to the Pi

```bash
./deploy/deploy.sh                           # on the Pi: build and install in place
./deploy/deploy.sh user@raspberrypi.local    # from another computer: build, copy to ~/ray-vault on the Pi, install
```

Re-runs are quick: apt is skipped when the packages are present, and Python dependencies are only resolved again
when `pyproject.toml` changes. Add `--full` to force everything (e.g. after an OS upgrade).

`install.sh` creates the `rayvault` user (groups `bluetooth`, `video`, `dialout`), a venv in `/opt/ray-vault`,
`/etc/ray-vault/config.toml` (mode 640, it holds the PIN), the polkit rule and the `ray-vault` service.

**Upgrading from jkbms-pi** happens automatically on the first run: the old service is stopped and removed, the
database is copied to `/var/lib/ray-vault/ray-vault.db`, and the config to `/etc/ray-vault/config.toml` (old flat
keys keep working; commented `[inverter]`/`[control]` sections are appended). The old directories are left for you
to delete once everything works.

### Connecting the inverter

1. Plug the USB-RS485 adapter into the Pi and find its stable path: `ls /dev/serial/by-id/`.
2. In `/etc/ray-vault/config.toml` set `[inverter] enabled = true` and `port = "/dev/serial/by-id/…"`.
3. **Check the register map before trusting writes.** With the service stopped (it owns the port):

   ```bash
   sudo systemctl stop ray-vault
   sudo -u rayvault /opt/ray-vault/venv/bin/python -m ray_vault probe-inverter --config /etc/ray-vault/config.toml
   sudo systemctl start ray-vault
   ```

   The probe is read-only. Compare its output with the inverter's display (priorities, charge currents, battery
   voltages). Registers the inverter rejects are shown as `-`.
4. Set `[control] pin = "…"` to enable changes, restart, change **output priority** once from the Inverter page
   and confirm it on the inverter's display before using the other controls.

### First run against a real BMS

Set `[bms] mac`, power off any other device connected to the BMS (it accepts one BLE connection), and
`sudo systemctl restart ray-vault`. If values look wrong, capture frames with `--dump-frames` (saved next to the
database) and adjust `ray_vault/bms/protocol.py`.

Logs: `journalctl -u ray-vault -f`. Backup while running: `sqlite3 /var/lib/ray-vault/ray-vault.db ".backup /path/b.db"`.

## Inverter control and safety

Settings are defined once in the profile (`ray_vault/inverter/profiles/hf.py`) and the dashboard builds its
forms from them. HF2430 controls: output and charger priority; total, mains and PV charge current limits;
stop-charging current and boost time; charge/discharge cut-off SOC; and the battery protection voltages
(over-voltage, charge limit, boost, float, over-discharge and its return, discharge limit, switch-to-mains voltage).

The HF series has **no time-of-use charge/discharge slots** and switches to mains by battery **voltage**
(`Switch to mains below`) rather than by SOC; those registers exist only on SRNE hybrid models. A profile for
another model can add them.

A write is refused, and nothing is sent to the inverter, unless all of these pass:

1. a PIN is configured (otherwise everything is read-only) and the PIN is correct; 5 wrong PINs in 10 minutes lock
   that client out for 10 minutes;
2. the value is valid for the setting (option, range, step);
3. battery voltages: an explicit confirmation (typed in the UI), the order float ≤ boost ≤ charge limit ≤
   over-voltage and discharge limit ≤ over-discharge < its return, and the **BMS guardrails**: per cell, charging
   voltages must stay 50 mV below the BMS over-voltage limit and discharge voltages 50 mV above its under-voltage
   limit (cell count and limits are read live from the JK BMS; if the BMS is offline these writes are refused).

Then the value is written, read back, and must match. Every change is logged as an `inverter_setting` event with
old → new and the client address; changes made on the inverter's own keypad show up as `settings_changed`.

## Link recovery

**BMS (BLE)**, driven by the time since the last cell-data frame:

| Stalled for | Action | Event |
|---|---|---|
| 5 s | Re-send the cell-info request | `cell_info_rerequest` |
| 20 s | Disconnect, rescan, reconnect | `ble_timeout`, `ble_disconnected` |
| 2 min, then 4, 8 … (cap 30 min) | Power-cycle the Bluetooth adapter | `adapter_reset` |
| after 3 resets | Restart `bluetooth.service` (in-process, via polkit; the inverter keeps running) | `bluetoothd_restart` |

Only if restarting bluetoothd fails does the service exit (code 75); systemd then restarts bluetoothd and the service.

**Inverter (RS485):** a failed poll is retried on the next tick; 3 failures in a row reopen the serial port
(`inverter_no_response`); a missing adapter (`inverter_port_missing`) is retried with backoff. Using the
`/dev/serial/by-id/` path means re-plugging the adapter just works.

## API

Interactive docs at `/docs`. Bad parameters return `400 {"error": "..."}`.

| Endpoint | |
|---|---|
| `GET /api/overview` | SOC, solar/load/grid/battery power, both link states (used by the widgets) |
| `GET /api/health` | `status` is `degraded` if any enabled device is stale or the DB isn't writable |
| `GET /api/system` | Pi health (CPU temp, under-voltage, CPU, memory, storage, WiFi) and both links |
| `GET /api/events?limit&from&to&source` | Newest first; `source` = `bms`, `inverter` or `system` |
| `GET /api/bms/{current,settings,device,link}` | Latest BMS state (old `/api/current` etc. still work) |
| `GET /api/bms/history`, `/history/cells`, `/export.csv` | Downsampled series / per-cell series / raw CSV (≤ 31 days) |
| `GET /api/inverter/{current,device,link}` | Latest inverter state |
| `GET /api/inverter/settings` | All settings with values and schema (range, step, options, danger) |
| `POST /api/inverter/settings/{key}` | `{"value": …, "confirm": bool}` with header `X-Control-Pin` → `{key, old, new}` |
| `GET /api/inverter/history`, `/export.csv` | Same semantics as the BMS endpoints |

## Widgets

**iPhone (Scriptable):** [widgets/scriptable-jkbms.js](widgets/scriptable-jkbms.js). Paste it into a new Scriptable
script, add a Scriptable widget and set its *Parameter* to your dashboard URL. The medium size also shows solar and load.

**Mac menu bar (SwiftBar):** [widgets/swiftbar/jkbms.10s.sh](widgets/swiftbar/jkbms.10s.sh) shows SOC and battery
power in the menu bar; the dropdown has cells, temperatures, solar/load/grid and the inverter state. Needs only
`curl` and `jq` (built into macOS 15+). Edit `JKBMS_URL` at the top; the `10s` in the filename is the refresh interval.

## Notes

- History buckets are aligned to epoch multiples (stable across refreshes); ranges are half-open `[from, to)`;
  ranges over 6 h read the 1-minute rollups. Retention never deletes raw rows that haven't been rolled up.
- `min_cell_index` / `max_cell_index` are 1-based, matching the JK app.
- BMS link liveness counts only cell-data frames: the BMS acknowledges every command, so counting any
  notification kept a dead data stream looking alive.
- The inverter reports battery current as + while discharging; Ray Vault flips it so + always means charging,
  matching the BMS.
- Database migrations are append-only (`ray_vault/core/store.py`); migration 2 added per-source settings, device
  info and events, and the inverter tables, keeping all existing BMS data.
