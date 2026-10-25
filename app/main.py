import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db.migrate import upgrade_to_head
from app.db.session import make_engine, make_session_factory
from app.events import EventBus
from app.web.health import router as health_router

log = logging.getLogger("lanmon")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not settings.secret_key:
            log.warning("LANMON_SECRET_KEY nu e setat; va fi obligatoriu odată cu autentificarea.")
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        # Aici vor porni task-urile de fundal (scanner, pinger, retenție) din fazele următoare.
        yield
        app.state.engine.dispose()

    app = FastAPI(title="LanMon", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.events = EventBus()
    app.include_router(health_router)
    return app


app = create_app()
