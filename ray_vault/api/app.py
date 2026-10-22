"""FastAPI app: REST API and the static dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core.store import QueryError
from ..inverter.controls import ControlError
from . import routes_bms, routes_inverter, routes_system
from .common import AppContext

STATIC_DIR = Path(__file__).parent.parent / "static"


def create_app(ctx: AppContext, static_dir: Path | None = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="Ray Vault", version=__version__)
    app.state.ctx = ctx
    # LAN-only service. Writes are protected by the control PIN, not by CORS.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(QueryError)
    async def _query_error(_req: Request, exc: QueryError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(ControlError)
    async def _control_error(_req: Request, exc: ControlError):
        return JSONResponse(status_code=exc.status, content={"error": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=400, content={"error": "; ".join(msgs) or "invalid request"})

    app.include_router(routes_bms.make_router(ctx), prefix="/api/bms")
    # Paths from the single-device version (/api/current, /api/history, …), still used by the widgets.
    app.include_router(routes_bms.make_router(ctx), prefix="/api", include_in_schema=False)
    app.include_router(routes_inverter.make_router(ctx))
    app.include_router(routes_system.make_router(ctx))

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        def no_dashboard():
            return HTMLResponse(
                "<h1>Ray Vault</h1><p>The dashboard has not been built. "
                "Run <code>bun run build</code> in <code>web/</code>. The API is at <a href='/docs'>/docs</a>.</p>"
            )

    return app
