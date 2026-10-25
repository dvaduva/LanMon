import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.db.backup import backup_before_migration
from app.db.migrate import upgrade_to_head
from app.db.session import make_engine, make_session_factory
from app.db.settings_store import get_str
from app.events import EventBus
from app.security import LoginLimiter
from app.services.netinfo import resolve_network
from app.services.pinger import Pinger
from app.services.retention import run_nightly
from app.services.scanner import Scanner
from app.services.waker import Waker
from app.web import (
    auth,
    catalog,
    dashboard,
    devices,
    live,
    unassigned,
    wol,
)
from app.web import (
    settings as settings_page,
)
from app.web.deps import CsrfError, Redirect, redirect, templates, verify_csrf
from app.web.health import router as health_router

log = logging.getLogger("lanmon")

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not settings.secret_key:
            log.warning(
                "LANMON_SECRET_KEY nu e setat; folosesc cheia generată în /data/secret.key."
            )
        await asyncio.to_thread(
            backup_before_migration,
            settings.db_path,
            settings.database_url,
            settings.data_dir / "backups",
        )
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        await asyncio.to_thread(app.state.waker.close_interrupted)
        tasks: list[asyncio.Task] = []
        if settings.background_tasks:
            tasks = [
                asyncio.create_task(app.state.scanner.run(), name="scanner"),
                asyncio.create_task(app.state.pinger.run(), name="pinger"),
                asyncio.create_task(run_nightly(app.state.session_factory), name="retention"),
            ]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.waker.shutdown()
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

    def network():
        # Subnetul din Setări are întâietate față de LANMON_SUBNET; altfel autodetecție.
        with app.state.session_factory() as session:
            subnet = get_str(session, "subnet") or settings.subnet
        return resolve_network(settings.interface, subnet)

    app.state.network = network

    app.state.scanner = Scanner(app.state.session_factory, app.state.events, network=network)
    app.state.waker = Waker(app.state.session_factory, app.state.events, network=network)
    app.state.pinger = Pinger(
        app.state.session_factory,
        app.state.events,
        extra_arp=lambda: {mac: seen.ip for mac, seen in app.state.scanner.seen.items()},
    )
    app.state.login_limiter = LoginLimiter()
    app.include_router(health_router)
    for module in (auth, dashboard, unassigned, devices, wol, live, settings_page):
        app.include_router(module.router)
    for router in catalog.routers:
        app.include_router(router)

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        # Servit din rădăcină ca să controleze tot site-ul.
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")

    @app.exception_handler(Redirect)
    async def on_redirect(request: Request, exc: Redirect) -> Response:
        return redirect(request, exc.url)

    @app.exception_handler(CsrfError)
    async def on_csrf_error(request: Request, exc: CsrfError) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"message": "Formularul a expirat. Reîncarcă pagina."}, 403
        )

    return app
