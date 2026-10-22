"""CLI entry point.

python -m ray_vault [--config PATH] [--fake [--seed-days N]] [--dump-frames [DIR]]
python -m ray_vault probe-inverter [--config PATH] [--port PORT]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

import uvicorn

from .api.app import create_app
from .api.common import AppContext
from .core.clock import now_ms
from .core.config import Config, ConfigError, load_config
from .core.service import BackgroundWriter, FatalDriverError, Service
from .core.store import DAY_MS, Store
from .core.watchdog import Watchdog

log = logging.getLogger("ray_vault")

FAKE_DB_PATH = "./ray-vault-fake.db"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="ray_vault", description="Ray Vault: JK BMS + SRNE inverter monitor")
    ap.add_argument("command", nargs="?", choices=["run", "probe-inverter"], default="run")
    ap.add_argument("--config", help="path to config.toml (default: /etc/ray-vault/config.toml if present)")
    ap.add_argument(
        "--dump-frames",
        nargs="?",
        const="",
        metavar="DIR",
        help="log every complete BMS frame as hex and save it to DIR (default: frames/ next to the database)",
    )
    ap.add_argument("--fake", action="store_true", help="simulate the BMS and the inverter (no hardware needed)")
    ap.add_argument("--seed-days", type=float, metavar="N", help="with --fake: prefill N days of synthetic history")
    ap.add_argument("--port", help="probe-inverter: serial port (default: inverter.port from the config)")
    return ap.parse_args(argv)


def seed_history(ctx: AppContext, days: float, interval_s: float) -> None:
    from .bms.fake import seed_rows as bms_rows
    from .inverter.fake import seed_rows as inverter_rows

    end = now_ms()
    start = end - int(days * DAY_MS)
    chunk = 6 * 3_600_000
    log.info("seeding %.1f day(s) of synthetic history…", days)
    for t in range(start, end, chunk):
        stop = min(t + chunk, end)
        if ctx.bms is not None:
            ctx.bms.series.insert(bms_rows(t, stop, interval_s, seed=t))
        if ctx.inverter is not None:
            ctx.inverter.series.insert(inverter_rows(t, stop, interval_s, seed=t))
    for d in (ctx.bms, ctx.inverter):
        if d is not None:
            d.series.rollup(end)
    log.info("seeding done")


def build(cfg: Config, args: argparse.Namespace, store: Store, writer: BackgroundWriter) -> AppContext:
    from .bms.state import State as BmsState
    from .inverter.controls import BmsLimits, Controller, PinGuard

    ctx = AppContext(cfg=cfg, store=store)

    if cfg.bms.enabled:
        from .bms.driver import BmsDriver

        bms_state = BmsState()
        if args.fake:
            from .bms.fake import FakeSource

            link = FakeSource(stats=bms_state.ble)
        else:
            from .bms.ble_client import BleSource

            b = cfg.bms
            link = BleSource(
                b.mac,
                b.adapter,
                cell_rerequest_s=b.cell_rerequest_s,
                link_timeout_s=b.link_timeout_s,
                adapter_reset_after_s=b.adapter_reset_after_s,
                adapter_reset_max_interval_s=b.adapter_reset_max_interval_s,
                restart_after_resets=b.restart_after_resets,
                rssi_poll_s=b.rssi_poll_s,
                stats=bms_state.ble,
            )
        dump_dir = None
        if args.dump_frames is not None:
            dump_dir = (
                Path(args.dump_frames) if args.dump_frames else Path(cfg.storage.db_path).resolve().parent / "frames"
            )
            log.info("dumping BMS frames to %s", dump_dir)
        ctx.bms = BmsDriver(store, writer, bms_state, link, cfg.storage.stale_s, dump_dir)

    if cfg.inverter.enabled:
        from .inverter.driver import InverterDriver
        from .inverter.profiles import get_profile

        inv = cfg.inverter
        if args.fake:
            from .inverter.fake import FakeBus

            bus = FakeBus()
        else:
            from .inverter.bus import ModbusBus

            bus = ModbusBus(inv.port, inv.slave, inv.baudrate, inv.timeout_s)
        ctx.inverter = InverterDriver(
            store,
            writer,
            bus,
            get_profile(inv.profile),
            poll_interval_s=inv.poll_interval_s,
            settings_poll_s=inv.settings_poll_s,
            stale_s=cfg.storage.stale_s,
        )

        def bms_limits() -> BmsLimits | None:
            s = ctx.bms.state.latest_settings if ctx.bms is not None else None
            if not s or not s.get("cell_count") or not s.get("cell_ovp") or not s.get("cell_uvp"):
                return None
            return BmsLimits(int(s["cell_count"]), float(s["cell_ovp"]), float(s["cell_uvp"]))

        guard = PinGuard(cfg.control.pin, cfg.control.max_failures, cfg.control.lockout_s)
        ctx.controller = Controller(ctx.inverter, guard, bms_limits)
    return ctx


async def amain(args: argparse.Namespace) -> int:
    try:
        cfg, explicit = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("bleak").setLevel(max(logging.INFO, logging.getLevelName(cfg.log_level)))

    if args.command == "probe-inverter":
        return await probe_inverter(cfg, args)

    if args.fake:
        cfg.bms.enabled = True
        cfg.inverter.enabled = True
        if not cfg.control.pin:
            cfg.control.pin = "1234"
            log.info("fake mode: control PIN is 1234")
        if "storage.db_path" not in explicit:
            cfg.storage.db_path = FAKE_DB_PATH
    else:
        if cfg.bms.enabled and not cfg.bms.mac:
            print(
                "config error: [bms] mac is required (or set [bms] enabled = false, or run with --fake)",
                file=sys.stderr,
            )
            return 2
        if not cfg.bms.enabled and not cfg.inverter.enabled:
            print("config error: neither [bms] nor [inverter] is enabled", file=sys.stderr)
            return 2
    if args.seed_days and not args.fake:
        print("--seed-days requires --fake", file=sys.stderr)
        return 2

    store = Store(cfg.storage.db_path)
    writer = BackgroundWriter(store)
    log.info("database: %s", Path(cfg.storage.db_path).resolve())
    ctx = build(cfg, args, store, writer)
    if args.seed_days:
        await asyncio.to_thread(seed_history, ctx, args.seed_days, cfg.storage.sample_interval_s)
    if ctx.controller is not None and not ctx.controller.writable:
        log.info("inverter settings are read-only (no [control] pin configured)")

    devices = [d for d in (ctx.bms, ctx.inverter) if d is not None]
    service = Service(cfg, store, writer, devices)
    app = create_app(ctx)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.http.host, port=cfg.http.port, log_level=cfg.log_level.lower(), access_log=False)
    )
    watchdog = Watchdog(store)

    service_task = asyncio.create_task(service.run(), name="service")
    server_task = asyncio.create_task(server.serve(), name="http")
    watchdog_task = asyncio.create_task(watchdog.run(lambda: server.started), name="watchdog")
    log.info("serving on http://%s:%d", cfg.http.host, cfg.http.port)
    try:
        done, _ = await asyncio.wait({service_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is service_task and t.exception():
                exc = t.exception()
                if isinstance(exc, FatalDriverError):
                    log.error("%s; exiting so systemd restarts the service", exc)
                    return exc.exit_code
                log.error("service stopped", exc_info=exc)
                return 1
        return 0
    finally:
        watchdog.stopping()
        watchdog_task.cancel()
        server.should_exit = True
        service_task.cancel()
        for t in (service_task, server_task, watchdog_task):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if ctx.inverter is not None:
            with contextlib.suppress(Exception):
                await ctx.inverter.bus.close()
        store.close()


async def probe_inverter(cfg: Config, args: argparse.Namespace) -> int:
    from .inverter.bus import ModbusBus
    from .inverter.probe import probe
    from .inverter.profiles import get_profile

    port = args.port or cfg.inverter.port
    if args.fake:
        from .inverter.fake import FakeBus

        bus = FakeBus()
    elif not port:
        print("probe-inverter: no port (set [inverter] port or pass --port)", file=sys.stderr)
        return 2
    else:
        bus = ModbusBus(port, cfg.inverter.slave, cfg.inverter.baudrate, cfg.inverter.timeout_s)
    try:
        return await probe(bus, get_profile(cfg.inverter.profile))
    finally:
        await bus.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
