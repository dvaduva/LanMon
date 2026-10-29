import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Device, Schedule

log = logging.getLogger(__name__)

WEEKDAYS = ["Lu", "Ma", "Mi", "Jo", "Vi", "Sâ", "Du"]
# O programare ratată (aplicația era oprită) mai pornește doar dacă au trecut cel mult 10 minute.
GRACE = timedelta(minutes=10)
CHECK_EVERY_S = 20


@dataclass(frozen=True)
class Due:
    schedule_id: int
    device_ids: list[int]


def due_at(schedule: Schedule, now_local: datetime) -> datetime | None:
    """Momentul (local) de azi la care trebuia să ruleze programarea, dacă azi e o zi a ei."""
    if str(now_local.weekday()) not in schedule.weekdays:
        return None
    hour, minute = (int(x) for x in schedule.time.split(":"))
    return now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)


class Scheduler:
    """Verifică periodic programările și trimite pachetele WOL prin Waker."""

    def __init__(self, session_factory: sessionmaker[Session], waker, tz: str) -> None:
        self._session_factory = session_factory
        self._waker = waker
        self.tz = ZoneInfo(tz)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Programările WOL au eșuat")
            await asyncio.sleep(CHECK_EVERY_S)

    async def tick(self, now: datetime | None = None) -> list[Due]:
        now = now or datetime.now(UTC)
        due = await asyncio.to_thread(self._collect, now)
        for item in due:
            if item.device_ids:
                await self._waker.wake(item.device_ids, source="schedule")
        return due

    def _collect(self, now: datetime) -> list[Due]:
        now_local = now.astimezone(self.tz)
        due = []
        with self._session_factory.begin() as session:
            for schedule in session.scalars(select(Schedule).where(Schedule.enabled)):
                at = due_at(schedule, now_local)
                if at is None or not at <= now_local < at + GRACE:
                    continue
                if schedule.last_run_at and schedule.last_run_at >= at:
                    continue  # a rulat deja azi
                schedule.last_run_at = now
                due.append(Due(schedule.id, self._targets(session, schedule)))
        return due

    @staticmethod
    def _targets(session: Session, schedule: Schedule) -> list[int]:
        query = select(Device.id).where(Device.wol_enabled, Device.status != "online")
        if schedule.device_id is not None:
            query = query.where(Device.id == schedule.device_id)
        else:
            query = query.where(Device.room_id == schedule.room_id)
        return list(session.scalars(query))
