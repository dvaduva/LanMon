import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import Settings
from app.db.backup import backup_before_migration
from app.db.models import ActionLog, Device, Setting, StatusEvent
from app.events import Event, EventBus
from app.main import create_app
from app.services.retention import purge, seconds_until
from app.web.live import event_stream
from tests.test_web import PASSWORD, post


def test_settings_save_and_validate(admin):
    page = admin.get("/settings").text
    assert "Rețea detectată" in page and 'value="30"' in page

    bad = post(
        admin,
        "/settings",
        {
            "ping_interval_s": "1",
            "scan_interval_s": "300",
            "wol_timeout_s": "180",
            "subnet": "nu-e-subnet",
        },
    )
    assert bad.status_code == 400 and "Subnet invalid" in bad.text and "Interval ping" in bad.text

    ok = post(
        admin,
        "/settings",
        {
            "ping_interval_s": "45",
            "scan_interval_s": "600",
            "wol_timeout_s": "120",
            "subnet": "192.168.1.7/24",
        },
    )
    assert ok.headers["location"] == "/settings"
    with admin.app.state.session_factory() as s:
        values = {r.key: r.value for r in s.scalars(select(Setting))}
    assert values["ping_interval_s"] == "45" and values["wol_timeout_s"] == "120"
    assert values["subnet"] == "192.168.1.0/24"

    post(
        admin,
        "/settings",
        {"ping_interval_s": "45", "scan_interval_s": "600", "wol_timeout_s": "120", "subnet": ""},
    )
    with admin.app.state.session_factory() as s:
        assert s.get(Setting, "subnet") is None


def test_backup_download_is_a_consistent_sqlite_copy(admin, tmp_path):
    with admin.app.state.session_factory() as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:01", name="PC"))
        s.commit()
    response = admin.get("/settings/backup")
    assert response.status_code == 200
    assert "lanmon-" in response.headers["content-disposition"]
    copy = tmp_path / "copy.db"
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as conn:
        assert conn.execute("select name from devices").fetchall() == [("PC",)]
    leftovers = list((admin.app.state.settings.data_dir / "backups").glob(".descarcare-*"))
    assert leftovers == []


def test_restore_backup_on_fresh_install(admin, tmp_path):
    with admin.app.state.session_factory() as s:
        room = s.execute(
            text("insert into rooms(name, icon, sort_order) values('Birou', 'home', 0)")
        )
        s.add(Device(mac="aa:bb:cc:dd:ee:01", name="PC birou", room_id=room.lastrowid))
        s.commit()
    backup = admin.get("/settings/backup").content

    # Instalare nouă: backup-ul pus ca /data/lanmon.db, altă cheie de sesiune.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "lanmon.db").write_bytes(backup)
    settings = Settings(data_dir=fresh, secret_key="alta", version="test", background_tasks=False)
    with TestClient(create_app(settings)) as c:
        assert post(c, "/login", {"username": "admin", "password": PASSWORD}).status_code == 303
        page = c.get("/").text
        assert "Birou" in page and "PC birou" in page


def test_backup_before_migration(session_factory, settings):
    backups = settings.data_dir / "backups"
    # Baza e la zi: nu se face backup.
    assert backup_before_migration(settings.db_path, settings.database_url, backups) is None
    # O bază la o revizie mai veche primește backup înainte de migrare.
    with session_factory.begin() as s:
        s.execute(text("update alembic_version set version_num = 'veche'"))
    dest = backup_before_migration(settings.db_path, settings.database_url, backups)
    assert dest is not None and dest.name.startswith("pre-migrare-veche-")
    with sqlite3.connect(dest) as conn:
        assert conn.execute("select count(*) from device_types").fetchone()[0] == 10


def test_retention_purges_old_rows(session_factory):
    now = datetime.now(UTC)
    with session_factory.begin() as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:01", name="PC"))
        s.flush()
        device_id = s.scalar(select(Device.id))
        s.add_all(
            [
                StatusEvent(device_id=device_id, status="online", at=now - timedelta(days=91)),
                StatusEvent(device_id=device_id, status="offline", at=now - timedelta(days=89)),
                ActionLog(
                    device_id=device_id,
                    action="wol",
                    source="user",
                    result="woke",
                    created_at=now - timedelta(days=100),
                ),
                ActionLog(
                    device_id=device_id, action="wol", source="user", result="woke", created_at=now
                ),
            ]
        )
    assert purge(session_factory, now) == (1, 1)
    with session_factory() as s:
        assert [e.status for e in s.scalars(select(StatusEvent))] == ["offline"]


def test_seconds_until_next_run():
    tz = datetime.now().astimezone().tzinfo
    assert seconds_until(datetime(2026, 10, 25, 3, 0, tzinfo=tz), 3, 30) == 30 * 60
    assert seconds_until(datetime(2026, 10, 25, 4, 0, tzinfo=tz), 3, 30) == 23.5 * 3600


def test_event_stream_forwards_live_events():
    bus = EventBus()

    class FakeRequest:
        app = SimpleNamespace(state=SimpleNamespace(events=bus))
        polls = 0

        async def is_disconnected(self):
            self.polls += 1
            return self.polls > 3

    async def collect():
        stream = event_stream(FakeRequest(), heartbeat_s=0.05)
        chunks = [await anext(stream)]
        bus.publish(Event("internal", {}))  # neinteresant pentru browser
        bus.publish(Event("device_status", {"device_id": 1, "status": "online"}))
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert chunks[0].startswith("retry:")
    assert 'event: device_status\ndata: {"device_id": 1, "status": "online"}\n\n' in chunks
    assert not any("internal" in c for c in chunks)
    assert not bus._subscribers  # abonamentul se închide la deconectare


def test_events_endpoint_requires_login(client):
    assert client.get("/events").status_code == 401


def test_pwa_assets(client):
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.json()["display"] == "standalone"
    assert "manifest" in manifest.headers["content-type"]
    for icon in manifest.json()["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert client.get("/sw.js").status_code == 200
