import asyncio
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from ray_vault.api.app import create_app
from ray_vault.api.common import AppContext
from ray_vault.bms.driver import BmsDriver
from ray_vault.bms.fake import FakeSource
from ray_vault.bms.state import State
from ray_vault.core.config import Config
from ray_vault.core.service import BackgroundWriter
from ray_vault.core.store import Store
from ray_vault.inverter.controls import BmsLimits, Controller, PinGuard
from ray_vault.inverter.driver import InverterDriver
from ray_vault.inverter.fake import FakeBus
from ray_vault.inverter.profiles import hf

PIN = "2468"


@dataclass
class Env:
    cfg: Config
    store: Store
    writer: BackgroundWriter
    ctx: AppContext
    client: TestClient

    @property
    def bms(self) -> BmsDriver:
        return self.ctx.bms

    @property
    def inverter(self) -> InverterDriver:
        return self.ctx.inverter


def make_env(tmp_path, pin: str = PIN) -> Env:
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "api.db")
    cfg.control.pin = pin
    store = Store(cfg.storage.db_path)
    writer = BackgroundWriter(store)
    state = State()
    ctx = AppContext(cfg=cfg, store=store)
    ctx.bms = BmsDriver(store, writer, state, FakeSource(stats=state.ble), cfg.storage.stale_s)
    ctx.inverter = InverterDriver(store, writer, FakeBus(seed=1), hf, poll_interval_s=0.01, stale_s=cfg.storage.stale_s)

    def limits():
        s = state.latest_settings
        return BmsLimits(s["cell_count"], s["cell_ovp"], s["cell_uvp"]) if s else None

    ctx.controller = Controller(ctx.inverter, PinGuard(pin, cfg.control.max_failures, cfg.control.lockout_s), limits)
    return Env(cfg, store, writer, ctx, TestClient(create_app(ctx, static_dir=None)))


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    yield e
    e.store.close()


def run(coro):
    return asyncio.run(coro)


def feed_bms(env: Env, *frames) -> None:
    async def go():
        for f in frames:
            env.bms.on_frame(f)
        await env.writer.flush()

    run(go())
