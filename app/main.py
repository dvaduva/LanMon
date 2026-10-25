import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.db.migrate import upgrade_to_head
from app.db.session import make_engine, make_session_factory
from app.events import EventBus
from app.security import LoginLimiter
from app.services.netinfo import resolve_network
from app.services.pinger import Pinger
from app.services.scanner import Scanner
from app.web import auth, catalog, dashboard, devices, unassigned
from app.web.deps import CsrfError, Redirect, redirect, templates, verify_csrf
from app.web.health import router as health_router

log = logging.getLogger("lanmon")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not settings.secret_key:
            log.warning(
                "LANMON_SECRET_KEY nu e setat; folosesc cheia generată în /data/secret.key."
            )
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        tasks: list[asyncio.Task] = []
        if settings.background_tasks:
            tasks = [
                asyncio.create_task(app.state.scanner.run(), name="scanner"),
                asyncio.create_task(app.state.pinger.run(), name="pinger"),
            ]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        app.state.engine.dispose()

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="LanMon",
        version=settings.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(verify_csrf)],
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.resolved_secret_key(),
        session_cookie="lanmon_session",
        max_age=30 * 24 * 3600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.events = EventBus()
    app.state.scanner = Scanner(
        app.state.session_factory,
        app.state.events,
        network=lambda: resolve_network(settings.interface, settings.subnet),
    )
    app.state.pinger = Pinger(
        app.state.session_factory,
        app.state.events,
        extra_arp=lambda: {mac: seen.ip for mac, seen in app.state.scanner.seen.items()},
    )
    app.state.login_limiter = LoginLimiter()
    app.include_router(health_router)
    for router in (auth.router, dashboard.router, unassigned.router, devices.router):
        app.include_router(router)
    for router in catalog.routers:
        app.include_router(router)

    @app.exception_handler(Redirect)
    async def on_redirect(request: Request, exc: Redirect) -> Response:
        return redirect(request, exc.url)

    @app.exception_handler(CsrfError)
    async def on_csrf_error(request: Request, exc: CsrfError) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"message": "Formularul a expirat. Reîncarcă pagina."}, 403
        )

    return app
