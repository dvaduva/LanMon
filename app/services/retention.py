import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionLog, StatusEvent

log = logging.getLogger(__name__)

RETENTION = timedelta(days=90)
RUN_AT = (3, 30)  # ora locală (TZ din .env)


def seconds_until(now: datetime, hour: int, minute: int) -> float:
    """Secunde până la următoarea apariție a orei hour:minute (în fusul lui `now`)."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def purge(session_factory: sessionmaker[Session], now: datetime | None = None) -> tuple[int, int]:
    """Șterge evenimentele de stare și jurnalul mai vechi de 90 de zile."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    with session_factory.begin() as session:
        events = session.execute(delete(StatusEvent).where(StatusEvent.at < cutoff)).rowcount
        actions = session.execute(delete(ActionLog).where(ActionLog.created_at < cutoff)).rowcount
    return events, actions


async def run_nightly(session_factory: sessionmaker[Session]) -> None:
    while True:
        await asyncio.sleep(seconds_until(datetime.now().astimezone(), *RUN_AT))
        try:
            events, actions = await asyncio.to_thread(purge, session_factory)
            log.info("Retenție: șterse %d evenimente și %d intrări din jurnal", events, actions)
        except Exception:
            log.exception("Jobul de retenție a eșuat")
