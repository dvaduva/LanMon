import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.models import ActionLog, Device, Room, Schedule
from app.services.scheduler import Scheduler
from tests.test_web import post

TZ = ZoneInfo("Europe/Bucharest")
# Monday, 2 November 2026, local time
MONDAY_0800 = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)


class FakeWaker:
    def __init__(self):
        self.calls = []

    async def wake(self, ids, source="user"):
        self.calls.append((sorted(ids), source))
        return list(ids)


def seed(session_factory):
    with session_factory.begin() as s:
        room = Room(name="Birou", icon="briefcase")
        s.add_all(
            [
                Device(
                    mac="aa:bb:cc:dd:ee:01",
                    name="PC",
                    room=room,
                    wol_enabled=True,
                    status="offline",
                ),
                Device(
                    mac="aa:bb:cc:dd:ee:02",
                    name="PC2",
                    room=room,
                    wol_enabled=True,
                    status="online",
                ),
                Device(
                    mac="aa:bb:cc:dd:ee:03",
                    name="TV",
                    room=room,
                    wol_enabled=False,
                    status="offline",
                ),
            ]
        )


def add_schedule(session_factory, **kw):
    with session_factory.begin() as s:
        values = {"time": "08:00", "weekdays": "01234", "enabled": True, **kw}
        s.add(Schedule(**values))


def tick(session_factory, waker, when):
    scheduler = Scheduler(session_factory, waker, "Europe/Bucharest")
    return asyncio.run(scheduler.tick(when.astimezone(UTC)))


def test_device_schedule_fires_once_on_weekday(session_factory):
    seed(session_factory)
    add_schedule(session_factory, device_id=1)
    waker = FakeWaker()

    tick(session_factory, waker, MONDAY_0800 - timedelta(minutes=1))
    assert waker.calls == []
    tick(session_factory, waker, MONDAY_0800 + timedelta(seconds=20))
    tick(session_factory, waker, MONDAY_0800 + timedelta(minutes=1))
    assert waker.calls == [([1], "schedule")]

    # The next day (Tuesday) it runs again.
    tick(session_factory, waker, MONDAY_0800 + timedelta(days=1, seconds=5))
    assert len(waker.calls) == 2


def test_weekend_skipped_and_missed_runs_not_replayed(session_factory):
    seed(session_factory)
    add_schedule(session_factory, device_id=1)
    waker = FakeWaker()
    tick(session_factory, waker, MONDAY_0800 - timedelta(days=2))  # Saturday
    tick(session_factory, waker, MONDAY_0800 + timedelta(minutes=30))  # too late
    assert waker.calls == []


def test_room_schedule_wakes_only_offline_wol_devices(session_factory):
    seed(session_factory)
    add_schedule(session_factory, room_id=1)
    add_schedule(session_factory, device_id=1, enabled=False)
    waker = FakeWaker()
    tick(session_factory, waker, MONDAY_0800)
    assert waker.calls == [([1], "schedule")]


def test_schedules_page_crud(admin):
    seed(admin.app.state.session_factory)
    response = post(
        admin,
        "/schedules",
        {"target": "device:1", "time": "07:30", "days": ["0", "2", "4"]},
        page="/schedules",
    )
    assert response.status_code == 303
    page = admin.get("/schedules").text
    assert "07:30" in page and "Mon, Wed, Fri" in page

    bad = post(
        admin, "/schedules", {"target": "room:1", "time": "25:00", "days": "0"}, page="/schedules"
    )
    assert bad.status_code == 400 and "HH:MM" in bad.text
    assert (
        post(
            admin, "/schedules", {"target": "room:1", "time": "08:00"}, page="/schedules"
        ).status_code
        == 400
    )

    post(admin, "/schedules/1/toggle", page="/schedules")
    with admin.app.state.session_factory() as s:
        assert s.get(Schedule, 1).enabled is False
    post(admin, "/schedules/1/delete", page="/schedules")
    with admin.app.state.session_factory() as s:
        assert s.get(Schedule, 1) is None


def test_scheduled_wake_shows_in_log(admin):
    with admin.app.state.session_factory.begin() as s:
        d = Device(mac="aa:bb:cc:dd:ee:01", name="PC")
        s.add(d)
        s.flush()
        s.add(ActionLog(device_id=d.id, action="wol", source="schedule", result="woke"))
    assert "scheduled" in admin.get("/log").text
