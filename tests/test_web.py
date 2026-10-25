import re

import pytest
from sqlalchemy import select

from app.db.models import Device, DeviceType, Discovered, Room, User

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
PASSWORD = "parola-sigura"


def csrf(client, path="/login") -> str:
    return CSRF_RE.search(client.get(path).text).group(1)


def post(client, path, data=None, page=None, **kw):
    token = csrf(client, page or path)
    return client.post(
        path, data={"csrf_token": token, **(data or {})}, follow_redirects=False, **kw
    )


def setup_admin(client):
    return post(client, "/setup", {"username": "admin", "password": PASSWORD, "confirm": PASSWORD})


@pytest.fixture
def admin(client):
    assert setup_admin(client).status_code == 303
    return client


def session(client):
    return client.app.state.session_factory()


def test_first_run_redirects_to_setup(client):
    for path in ("/", "/login", "/unassigned", "/rooms", "/types", "/account", "/devices/1"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/setup", path


def test_setup_creates_admin_and_logs_in(admin):
    assert admin.get("/", follow_redirects=False).status_code == 200
    with session(admin) as s:
        user = s.scalar(select(User))
    assert user.username == "admin" and user.password_hash.startswith("$argon2")
    # Ecranul de configurare nu mai e disponibil după primul cont.
    assert admin.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_setup_validates_password(client):
    response = post(
        client, "/setup", {"username": "admin", "password": "scurt", "confirm": "scurt"}
    )
    assert response.status_code == 400
    assert "cel puțin 8" in response.text


PROTECTED = [
    ("GET", "/"),
    ("POST", "/scan"),
    ("GET", "/account"),
    ("POST", "/account/password"),
    ("GET", "/unassigned"),
    ("GET", "/unassigned/aa:bb:cc:dd:ee:ff/add"),
    ("POST", "/unassigned/aa:bb:cc:dd:ee:ff/add"),
    ("POST", "/unassigned/aa:bb:cc:dd:ee:ff/ignore"),
    ("POST", "/unassigned/aa:bb:cc:dd:ee:ff/restore"),
    ("GET", "/devices/1"),
    ("POST", "/devices/1"),
    ("POST", "/devices/1/delete"),
    ("POST", "/devices/1/wake"),
    ("POST", "/rooms/1/wake"),
    ("GET", "/log"),
    *[
        (method, f"{prefix}{suffix}")
        for prefix in ("/rooms", "/types")
        for method, suffix in [
            ("GET", ""),
            ("GET", "/new"),
            ("POST", "/new"),
            ("GET", "/1"),
            ("POST", "/1"),
            ("POST", "/1/delete"),
        ]
    ],
]


def test_every_page_and_action_requires_login(admin):
    admin.cookies.clear()
    token = csrf(admin)  # sesiune nouă, neautentificată
    for method, url in PROTECTED:
        data = {"csrf_token": token} if method == "POST" else None
        response = admin.request(method, url, data=data, follow_redirects=False)
        assert response.status_code == 303, (method, url)
        assert response.headers["location"] == "/login", (method, url)


def test_login_logout(admin):
    assert post(admin, "/logout", page="/account").headers["location"] == "/login"
    assert admin.get("/", follow_redirects=False).headers["location"] == "/login"

    bad = post(admin, "/login", {"username": "admin", "password": "gresit"})
    assert bad.status_code == 401
    good = post(admin, "/login", {"username": "admin", "password": PASSWORD})
    assert good.headers["location"] == "/"
    assert admin.get("/").status_code == 200


def test_login_rate_limited_after_five_failures(admin):
    post(admin, "/logout", page="/account")
    for _ in range(5):
        assert post(admin, "/login", {"username": "admin", "password": "x"}).status_code == 401
    blocked = post(admin, "/login", {"username": "admin", "password": PASSWORD})
    assert blocked.status_code == 429
    assert "15 min" in blocked.text


def test_post_without_csrf_is_rejected(admin):
    response = admin.post("/scan", follow_redirects=False)
    assert response.status_code == 403
    response = admin.post("/scan", data={"csrf_token": "fals"}, follow_redirects=False)
    assert response.status_code == 403


def test_htmx_header_token_accepted(admin):
    token = csrf(admin, "/")
    response = admin.post("/scan", headers={"X-CSRF-Token": token, "HX-Request": "true"})
    assert response.status_code == 204 and response.headers["HX-Redirect"] == "/"


def test_password_change_invalidates_other_sessions(admin, settings):
    from fastapi.testclient import TestClient

    other = TestClient(admin.app)
    post(other, "/login", {"username": "admin", "password": PASSWORD})
    assert other.get("/", follow_redirects=False).status_code == 200

    response = post(
        admin,
        "/account/password",
        {"current": PASSWORD, "password": "parola-noua-1", "confirm": "parola-noua-1"},
        page="/account",
    )
    assert response.headers["location"] == "/account"
    assert admin.get("/", follow_redirects=False).status_code == 200
    assert other.get("/", follow_redirects=False).headers["location"] == "/login"


def test_add_device_from_unassigned(admin):
    with session(admin) as s:
        s.add(
            Discovered(
                mac="00:11:32:aa:bb:cc",
                ip="192.168.1.20",
                hostname="nas.lan",
                vendor="Synology Incorporated",
            )
        )
        s.add(Room(name="Birou", icon="briefcase"))
        s.commit()
        room_id = s.scalar(select(Room.id))
        nas_type = s.scalar(select(DeviceType.id).where(DeviceType.name == "NAS"))

    page = admin.get("/unassigned")
    assert "nas.lan" in page.text and "Synology" in page.text
    form = admin.get("/unassigned/00:11:32:aa:bb:cc/add")
    assert 'value="nas"' in form.text  # nume sugerat din hostname

    response = post(
        admin,
        "/unassigned/00:11:32:aa:bb:cc/add",
        {"name": "NAS", "room_id": str(room_id), "type_id": str(nas_type), "wol_enabled": "on"},
    )
    assert response.headers["location"] == "/unassigned"
    with session(admin) as s:
        device = s.scalar(select(Device))
        assert s.get(Discovered, "00:11:32:aa:bb:cc") is None
    assert (device.name, device.room_id, device.type_id, device.wol_enabled) == (
        "NAS",
        room_id,
        nas_type,
        True,
    )
    assert device.last_ip == "192.168.1.20" and device.status == "unknown"

    dashboard = admin.get("/")
    assert "Birou" in dashboard.text and "NAS" in dashboard.text and "0/1" in dashboard.text


def test_ignore_and_restore(admin):
    with session(admin) as s:
        s.add(Discovered(mac="aa:bb:cc:dd:ee:01", ip="192.168.1.30"))
        s.commit()
    post(admin, "/unassigned/aa:bb:cc:dd:ee:01/ignore", page="/unassigned")
    with session(admin) as s:
        assert s.get(Discovered, "aa:bb:cc:dd:ee:01").ignored
    assert "Ignorate (1)" in admin.get("/unassigned").text
    post(admin, "/unassigned/aa:bb:cc:dd:ee:01/restore", page="/unassigned")
    with session(admin) as s:
        assert not s.get(Discovered, "aa:bb:cc:dd:ee:01").ignored


def test_edit_device_and_history(admin):
    with session(admin) as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:02", name="PC", last_ip="192.168.1.40"))
        s.commit()
        device_id = s.scalar(select(Device.id))
    assert "Nicio schimbare" in admin.get(f"/devices/{device_id}").text

    bad = post(admin, f"/devices/{device_id}", {"name": " "})
    assert bad.status_code == 400
    post(admin, f"/devices/{device_id}", {"name": "PC birou", "notes": "lângă imprimantă"})
    with session(admin) as s:
        device = s.get(Device, device_id)
    assert (device.name, device.notes, device.room_id) == ("PC birou", "lângă imprimantă", None)

    post(admin, f"/devices/{device_id}/delete", page=f"/devices/{device_id}")
    with session(admin) as s:
        assert s.get(Device, device_id) is None


def test_room_crud_and_delete_guard(admin):
    post(admin, "/rooms/new", {"name": "Sufragerie", "icon": "sofa", "sort_order": "10"})
    with session(admin) as s:
        room = s.scalar(select(Room))
    assert (room.name, room.icon, room.sort_order) == ("Sufragerie", "sofa", 10)

    duplicate = post(admin, "/rooms/new", {"name": "sufragerie", "icon": "sofa", "sort_order": "0"})
    assert duplicate.status_code == 400

    with session(admin) as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:03", name="TV", room_id=room.id))
        s.commit()
    post(admin, f"/rooms/{room.id}/delete", page=f"/rooms/{room.id}")
    with session(admin) as s:
        assert s.get(Room, room.id) is not None  # folosită: nu se șterge
        s.delete(s.scalar(select(Device)))
        s.commit()
    post(admin, f"/rooms/{room.id}/delete", page=f"/rooms/{room.id}")
    with session(admin) as s:
        assert s.get(Room, room.id) is None


def test_types_are_seeded_and_editable(admin):
    page = admin.get("/types")
    for name in ("PC", "Laptop", "NAS", "Router/AP"):
        assert name in page.text
    with session(admin) as s:
        pc = s.scalar(select(DeviceType).where(DeviceType.name == "PC"))
    post(admin, f"/types/{pc.id}", {"name": "Desktop", "icon": "monitor", "sort_order": "0"})
    with session(admin) as s:
        assert s.get(DeviceType, pc.id).name == "Desktop"


def test_static_assets_served(client):
    assert client.get("/static/icons.svg").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200


class FakeWaker:
    def __init__(self):
        self.pending = {}
        self.calls = []

    async def wake(self, ids, source="user"):
        self.calls.append((list(ids), source))
        return list(ids)

    def close_interrupted(self):
        pass

    async def shutdown(self):
        pass


def test_wake_buttons(admin):
    from app.db.models import ActionLog

    fake = FakeWaker()
    admin.app.state.waker = fake
    with session(admin) as s:
        s.add(Room(name="Birou", icon="briefcase"))
        s.flush()
        room_id = s.scalar(select(Room.id))
        s.add_all(
            [
                Device(
                    mac="aa:bb:cc:00:00:01",
                    name="PC1",
                    room_id=room_id,
                    wol_enabled=True,
                    status="offline",
                ),
                Device(
                    mac="aa:bb:cc:00:00:02",
                    name="PC2",
                    room_id=room_id,
                    wol_enabled=True,
                    status="offline",
                ),
                Device(
                    mac="aa:bb:cc:00:00:03",
                    name="PC3",
                    room_id=room_id,
                    wol_enabled=True,
                    status="online",
                ),
                Device(
                    mac="aa:bb:cc:00:00:04",
                    name="TV",
                    room_id=room_id,
                    wol_enabled=False,
                    status="offline",
                ),
            ]
        )
        s.commit()
        ids = {d.name: d.id for d in s.scalars(select(Device))}

    page = admin.get("/").text
    assert "Pornește toată camera (2)" in page
    assert len(re.findall(r'action="/devices/\d+/wake"', page)) == 2  # PC1 și PC2

    post(admin, f"/rooms/{room_id}/wake", page="/")
    assert fake.calls[-1] == ([ids["PC1"], ids["PC2"]], "room")  # doar WOL bifat și offline

    response = post(
        admin, f"/devices/{ids['PC1']}/wake", page="/", headers={"referer": "http://testserver/"}
    )
    assert response.headers["location"] == "/"
    assert fake.calls[-1] == ([ids["PC1"]], "user")

    post(admin, f"/devices/{ids['TV']}/wake", page="/")
    assert len(fake.calls) == 2  # TV nu are WOL

    fake.pending = {ids["PC1"]: 1}
    assert "Se pornește…" in admin.get("/").text

    with session(admin) as s:
        s.add(ActionLog(device_id=ids["PC2"], action="wol", source="user", result="timeout"))
        s.commit()
    assert "Nu a răspuns" in admin.get("/").text
    device_page = admin.get(f"/devices/{ids['PC2']}").text
    assert "Fast Startup" in device_page and "BIOS" in device_page

    log_page = admin.get("/log").text
    assert "PC2" in log_page and "Nu a răspuns" in log_page
