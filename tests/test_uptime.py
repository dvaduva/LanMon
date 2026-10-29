from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db.models import Device, Room, StatusEvent, User
from app.security import hash_password
from app.services.uptime import compute, device_uptime
from tests.test_web import post

TZ = ZoneInfo("Europe/Bucharest")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ).astimezone(UTC)  # Wednesday, noon


def at(day, hour):
    return datetime(2026, 11, day, hour, 0, tzinfo=TZ).astimezone(UTC)


def test_compute_splits_by_local_day():
    changes = [(at(3, 8), "online"), (at(3, 20), "offline"), (at(4, 6), "online")]
    uptime = compute(changes, "offline", NOW, 3, TZ)
    d2, d3, d4 = uptime.days
    assert d2.percent == 0  # offline all day
    assert d3.percent == pytest.approx(50)  # 12 h out of 24
    assert d4.known_s == 12 * 3600 and d4.percent == pytest.approx(50)  # 6 h out of 12 so far
    assert uptime.percent(2) == pytest.approx(100 * 18 / 36)


def test_unknown_time_is_ignored():
    uptime = compute([(at(4, 10), "online")], None, NOW, 2, TZ)
    assert uptime.days[0].percent is None
    assert uptime.days[1].percent == 100 and uptime.days[1].known_s == 2 * 3600
    assert compute([], None, NOW, 7, TZ).percent(7) is None


def test_device_uptime_uses_state_before_window(session_factory):
    with session_factory.begin() as s:
        d = Device(mac="aa:bb:cc:dd:ee:01", name="PC")
        s.add(d)
        s.flush()
        s.add_all(
            [
                StatusEvent(device_id=d.id, status="online", at=NOW - timedelta(days=40)),
                StatusEvent(device_id=d.id, status="offline", at=at(4, 0)),
            ]
        )
    with session_factory() as s:
        uptime = device_uptime(s, 1, NOW, 7, TZ)
    assert [round(day.percent) for day in uptime.days] == [100] * 6 + [0]


def test_pages_show_uptime(admin):
    sf = admin.app.state.session_factory
    with sf.begin() as s:
        room = Room(name="Birou", icon="briefcase")
        d = Device(mac="aa:bb:cc:dd:ee:01", name="PC birou", room=room, status="online")
        s.add_all([d, Device(mac="aa:bb:cc:dd:ee:02", name="TV", status="offline")])
        s.flush()
        s.add(
            StatusEvent(device_id=d.id, status="online", at=datetime.now(UTC) - timedelta(days=9))
        )
        s.add(
            User(
                username="ana",
                password_hash=hash_password("parola-ana"),
                role="member",
                rooms=[room],
            )
        )
    page = admin.get("/uptime").text
    assert "PC birou" in page and "100%" in page and "TV" in page
    assert "Uptime" in admin.get("/devices/1").text

    post(admin, "/logout", page="/settings")
    post(admin, "/login", {"username": "ana", "password": "parola-ana"})
    page = admin.get("/uptime").text
    assert "PC birou" in page and ">TV<" not in page
