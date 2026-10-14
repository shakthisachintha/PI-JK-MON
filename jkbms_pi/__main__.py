"""CLI entry point: python -m jkbms_pi [--config PATH] [--dump-frames] [--fake]."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .config import ConfigError, load_config
from .service import Service
from .state import State, now_ms
from .store import DAY_MS, Store

log = logging.getLogger("jkbms_pi")

FAKE_DB_PATH = "./jkbms-fake.db"
EXIT_RECOVERY_EXHAUSTED = 75  # EX_TEMPFAIL; the systemd unit restarts bluetooth.service on this code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="jkbms_pi", description="JK BMS monitor for Raspberry Pi")
    ap.add_argument("--config", help="path to config.toml (default: /etc/jkbms-pi/config.toml if present)")
    ap.add_argument(
        "--dump-frames",
        nargs="?",
        const="",
        metavar="DIR",
        help="log every complete frame as hex and save it to DIR (default: frames/ next to the database)",
    )
    ap.add_argument("--fake", action="store_true", help="generate synthetic BMS data instead of using BLE")
    ap.add_argument(
        "--seed-days",
        type=float,
        metavar="N",
        help="with --fake: fill the database with N days of synthetic history before starting",
    )
    return ap.parse_args(argv)


def seed_history(store: Store, days: float, interval_s: float) -> None:
    from .fake_source import seed_rows

    end = now_ms()
    start = end - int(days * DAY_MS)
    chunk = 6 * 3_600_000
    log.info("seeding %.1f day(s) of synthetic history…", days)
    for t in range(start, end, chunk):
        store.insert_samples_bulk(seed_rows(t, min(t + chunk, end), interval_s, seed=t))
    store.rollup(end)
    log.info("seeding done")


async def amain(args: argparse.Namespace) -> int:
    try:
        cfg, explicit = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("bleak").setLevel(max(logging.INFO, logging.getLevelName(cfg.log_level)))

    if args.fake and "db_path" not in explicit:
        cfg.db_path = FAKE_DB_PATH
    if not args.fake and not cfg.bms_mac:
        print("config error: 'bms_mac' is required (or run with --fake)", file=sys.stderr)
        return 2
    if args.seed_days and not args.fake:
        print("--seed-days requires --fake", file=sys.stderr)
        return 2

    store = Store(cfg.db_path)
    state = State()
    log.info("database: %s", Path(cfg.db_path).resolve())

    if args.fake:
        from .fake_source import FakeSource

        source = FakeSource(stats=state.ble)
        if args.seed_days:
            await asyncio.to_thread(seed_history, store, args.seed_days, cfg.sample_interval_s)
    else:
        from .ble_client import BleSource

        source = BleSource(
            cfg.bms_mac,
            cfg.adapter,
            cell_rerequest_s=cfg.cell_rerequest_s,
            link_timeout_s=cfg.link_timeout_s,
            adapter_reset_after_s=cfg.adapter_reset_after_s,
            adapter_reset_max_interval_s=cfg.adapter_reset_max_interval_s,
            restart_after_resets=cfg.restart_after_resets,
            rssi_poll_s=cfg.rssi_poll_s,
            stats=state.ble,
        )

    dump_dir = None
    if args.dump_frames is not None:
        dump_dir = Path(args.dump_frames) if args.dump_frames else Path(cfg.db_path).resolve().parent / "frames"
        log.info("dumping frames to %s", dump_dir)

    service = Service(cfg, store, state, source, dump_dir)
    app = create_app(cfg, state, store)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.http_host, port=cfg.http_port, log_level=cfg.log_level.lower(), access_log=False)
    )

    service_task = asyncio.create_task(service.run(), name="service")
    server_task = asyncio.create_task(server.serve(), name="http")
    log.info("serving on http://%s:%d", cfg.http_host, cfg.http_port)
    try:
        done, _ = await asyncio.wait({service_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is service_task and t.exception():
                exc = t.exception()
                if type(exc).__name__ == "LinkRecoveryExhausted":
                    log.error("BLE recovery exhausted; exiting so systemd restarts bluetoothd and this service")
                    return EXIT_RECOVERY_EXHAUSTED
                log.error("service stopped", exc_info=exc)
                return 1
        return 0
    finally:
        server.should_exit = True
        service_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await service_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await server_task
        store.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        sys.exit(asyncio.run(amain(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
