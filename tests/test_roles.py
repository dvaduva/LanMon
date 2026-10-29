from app.db.models import Device, Room, User
from app.security import hash_password
from tests.test_web import PASSWORD, csrf, post


def seed(sf):
    with sf.begin() as s:
        birou = Room(name="Birou", icon="briefcase")
        living = Room(name="Living", icon="sofa")
        s.add_all(
            [
                Device(
                    mac="aa:bb:cc:dd:ee:01",
                    name="PC birou",
                    room=birou,
                    wol_enabled=True,
                    status="offline",
                ),
                Device(
                    mac="aa:bb:cc:dd:ee:02",
                    name="TV living",
                    room=living,
                    wol_enabled=True,
                    status="offline",
                ),
                Device(mac="aa:bb:cc:dd:ee:03", name="Fără cameră"),
            ]
        )


def login_as(client, username, password=PASSWORD):
    post(client, "/logout", page="/settings")
    response = post(client, "/login", {"username": username, "password": password})
    assert response.status_code == 303


class FakeWaker:
    pending: dict = {}

    def __init__(self):
        self.calls = []

    async def wake(self, ids, source="user"):
        self.calls.append(list(ids))
        return list(ids)

    async def shutdown(self):
        pass


def test_admin_creates_member_with_rooms(admin):
    seed(admin.app.state.session_factory)
    response = post(
        admin,
        "/users/new",
        {"username": "ana", "password": "parola-ana", "role": "member", "rooms": ["1"]},
        page="/users/new",
    )
    assert response.status_code == 303
    page = admin.get("/users").text
    assert "ana" in page and "Membru" in page and "Birou" in page

    short = post(
        admin,
        "/users/new",
        {"username": "x", "password": "123", "role": "member"},
        page="/users/new",
    )
    assert short.status_code == 400
    dup = post(
        admin,
        "/users/new",
        {"username": "ANA", "password": "parola-ana", "role": "member"},
        page="/users/new",
    )
    assert dup.status_code == 400 and "Există deja" in dup.text


def make_member(sf, rooms=("Birou",)):
    from sqlalchemy import select

    with sf.begin() as s:
        allowed = list(s.scalars(select(Room).where(Room.name.in_(rooms))))
        s.add(
            User(
                username="ana",
                password_hash=hash_password("parola-ana"),
                role="member",
                rooms=allowed,
            )
        )


def test_member_sees_only_allowed_rooms(admin):
    sf = admin.app.state.session_factory
    seed(sf)
    make_member(sf)
    login_as(admin, "ana", "parola-ana")

    page = admin.get("/").text
    assert "PC birou" in page and "TV living" not in page and "Fără cameră" not in page
    assert 'action="/scan"' not in page and 'href="/unassigned"' not in page

    assert admin.get("/devices/1").status_code == 200
    assert "Editare" not in admin.get("/devices/1").text
    assert admin.get("/devices/2").status_code == 403


def test_member_cannot_use_admin_pages(admin):
    sf = admin.app.state.session_factory
    seed(sf)
    make_member(sf)
    login_as(admin, "ana", "parola-ana")
    token = csrf(admin, "/settings")
    for method, url in [
        ("GET", "/unassigned"),
        ("GET", "/rooms"),
        ("GET", "/types"),
        ("GET", "/log"),
        ("GET", "/schedules"),
        ("GET", "/users"),
        ("GET", "/settings/backup"),
        ("POST", "/settings"),
        ("POST", "/settings/notifications"),
        ("POST", "/scan"),
        ("POST", "/devices/1"),
        ("POST", "/devices/1/delete"),
        ("POST", "/users/new"),
        ("POST", "/settings/tokens"),
        ("POST", "/settings/tokens/1/delete"),
    ]:
        data = {"csrf_token": token} if method == "POST" else None
        response = admin.request(method, url, data=data, follow_redirects=False)
        assert response.status_code == 403, (method, url)

    settings = admin.get("/settings").text
    assert (
        "Schimbă parola" in settings and "Notificări" not in settings and "Backup" not in settings
    )


def test_member_wakes_only_allowed_devices(admin):
    sf = admin.app.state.session_factory
    seed(sf)
    make_member(sf)
    waker = FakeWaker()
    admin.app.state.waker = waker
    login_as(admin, "ana", "parola-ana")
    assert post(admin, "/devices/1/wake", page="/").status_code == 303
    assert post(admin, "/devices/2/wake", page="/").status_code == 403
    assert post(admin, "/rooms/2/wake", page="/").status_code == 403
    assert waker.calls == [[1]]


def test_last_admin_is_protected(admin):
    response = post(admin, "/users/1", {"username": "admin", "role": "member"}, page="/users/1")
    assert response.status_code == 400 and "cel puțin un administrator" in response.text
    post(admin, "/users/1/delete", page="/settings")
    with admin.app.state.session_factory() as s:
        assert s.get(User, 1).role == "admin"


def test_existing_admin_keeps_full_access(admin):
    with admin.app.state.session_factory() as s:
        assert s.get(User, 1).is_admin
    assert admin.get("/users").status_code == 200
