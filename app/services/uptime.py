"""Daily uptime, computed from status_events (the status changes)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import StatusEvent


@dataclass(frozen=True)
class Day:
    day: date
    online_s: float
    known_s: float  # time with a known status (online or offline)

    @property
    def percent(self) -> float | None:
        return 100 * self.online_s / self.known_s if self.known_s else None


@dataclass(frozen=True)
class Uptime:
    days: list[Day]

    def percent(self, last: int) -> float | None:
        chosen = self.days[-last:]
        known = sum(d.known_s for d in chosen)
        return 100 * sum(d.online_s for d in chosen) / known if known else None


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz).astimezone(UTC)


def compute(
    changes: list[tuple[datetime, str]],
    initial: str | None,
    now: datetime,
    days: int,
    tz: ZoneInfo,
) -> Uptime:
    """`changes` = (moment, status) in order; `initial` = the status before the first day."""
    today = now.astimezone(tz).date()
    first = today - timedelta(days=days - 1)
    start = _local_midnight(first, tz)

    # Intervals (start, end, status) between the start and now.
    intervals = []
    state, since = initial, start
    for at, status in changes:
        if at <= start:
            state = status
            continue
        if at > now:
            break
        intervals.append((since, at, state))
        state, since = status, at
    intervals.append((since, now, state))

    result = []
    for i in range(days):
        day = first + timedelta(days=i)
        lo = _local_midnight(day, tz)
        hi = min(_local_midnight(day + timedelta(days=1), tz), now)
        online = known = 0.0
        for a, b, s in intervals:
            overlap = (min(b, hi) - max(a, lo)).total_seconds()
            if overlap <= 0 or s not in ("online", "offline"):
                continue
            known += overlap
            if s == "online":
                online += overlap
        result.append(Day(day, online, known))
    return Uptime(result)


def device_uptime(
    session: Session, device_id: int, now: datetime, days: int, tz: ZoneInfo
) -> Uptime:
    start = _local_midnight(now.astimezone(tz).date() - timedelta(days=days - 1), tz)
    before = session.scalar(
        select(StatusEvent.status)
        .where(StatusEvent.device_id == device_id, StatusEvent.at <= start)
        .order_by(StatusEvent.at.desc(), StatusEvent.id.desc())
        .limit(1)
    )
    rows = session.execute(
        select(StatusEvent.at, StatusEvent.status)
        .where(StatusEvent.device_id == device_id, StatusEvent.at > start)
        .order_by(StatusEvent.at, StatusEvent.id)
    ).all()
    return compute([(at, status) for at, status in rows], before, now, days, tz)
