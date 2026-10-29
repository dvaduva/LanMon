import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionLog, StatusEvent

log = logging.getLogger(__name__)

RETENTION = timedelta(days=90)
RUN_AT = (3, 30)  # local time (TZ from .env)


def seconds_until(now: datetime, hour: int, minute: int) -> float:
    """Seconds until the next hour:minute (in the time zone of `now`)."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def purge(session_factory: sessionmaker[Session], now: datetime | None = None) -> tuple[int, int]:
    """Deletes status events and log entries older than 90 days."""
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
            log.info("Retention: deleted %d status events and %d log entries", events, actions)
        except Exception:
            log.exception("Retention job failed")
