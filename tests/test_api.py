import re

from app.db.models import ActionLog, ApiToken, Device, Room
from tests.test_shutdown import FakeShutdowner
from tests.test_web import post

TOKEN_RE = re.compile(r"(lm_[A-Za-z0-9_-]{20,})")


class FakeWaker:
    def __init__(self) -> None:
        self.pending: dict[int, int] = {}
        self.calls: list[tuple[list[int], str]] = []

    async def wake(self, ids, source="user"):
        self.calls.append((list(ids), source))
        return list(ids)

    async def shutdown(self) -> None:
        pass

    def close_interrupted(self) -> None:
        pass


def new_token(admin, name="Home Assistant") -> str:
    response = post(admin, "/settings/tokens", {"name": name}, page="/settings")
    assert response.status_code == 200
    return TOKEN_RE.search(response.text).group(1)


def seed(admin) -> None:
    with admin.app.state.session_factory.begin() as s:
        room = Room(name="Birou", icon="briefcase")
        s.add_all(
            [
                Device(
                    mac="aa:bb:cc:dd:ee:01",
                    name="PC birou",
                    room=room,
                    status="online",
                    last_ip="192.168.1.10",
                    wol_enabled=True,
                    shutdown_enabled=True,
                    ssh_user="dan",
                    os="windows",
                ),
                Device(mac="aa:bb:cc:dd:ee:02", name="TV", status="offline"),
            ]
        )


def test_api_requires_valid_token(admin):
    seed(admin)
    assert admin.get("/api/v1/devices").status_code == 401
    bad = admin.get("/api/v1/devices", headers={"Authorization": "Bearer lm_gresit"})
    assert bad.status_code == 401 and bad.headers["www-authenticate"] == "Bearer"
    # Cookie-ul de sesiune nu e suficient pentru acțiuni prin API.
    assert admin.post("/api/v1/devices/1/wake").status_code == 401


def test_token_is_shown_once_and_stored_hashed(admin):
    token = new_token(admin)
    assert token not in admin.get("/settings").text
    with admin.app.state.session_factory() as s:
        row = s.query(ApiToken).one()
        assert row.token_hash != token and token.startswith(row.prefix)
        assert row.last_used_at is None
    admin.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    with admin.app.state.session_factory() as s:
        assert s.query(ApiToken).one().last_used_at is not None

    response = post(admin, "/settings/tokens/1/delete", page="/settings")
    assert response.status_code == 303
    revoked = admin.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401


def test_list_get_wake_shutdown(admin):
    seed(admin)
    waker, shutdowner = FakeWaker(), FakeShutdowner()
    admin.app.state.waker, admin.app.state.shutdowner = waker, shutdowner
    headers = {"Authorization": f"Bearer {new_token(admin)}"}
    admin.cookies.clear()  # Home Assistant nu are sesiune

    devices = admin.get("/api/v1/devices", headers=headers).json()
    assert [(d["name"], d["online"], d["room"]) for d in devices] == [
        ("PC birou", True, "Birou"),
        ("TV", False, None),
    ]
    pc = admin.get("/api/v1/devices/1", headers=headers).json()
    assert pc["ip"] == "192.168.1.10" and pc["can_wake"] and pc["can_shutdown"]
    assert admin.get("/api/v1/devices/99", headers=headers).status_code == 404

    woke = admin.post("/api/v1/devices/1/wake", headers=headers)
    assert woke.status_code == 200 and woke.json()["ok"] and waker.calls == [([1], "api")]
    refused = admin.post("/api/v1/devices/2/wake", headers=headers)
    assert refused.status_code == 409 and not refused.json()["ok"]

    off = admin.post("/api/v1/devices/1/shutdown", headers=headers)
    assert off.status_code == 200 and shutdowner.calls == [(1, "api")]
    assert admin.post("/api/v1/devices/2/shutdown", headers=headers).status_code == 409


def test_log_shows_api_source(admin):
    seed(admin)
    with admin.app.state.session_factory.begin() as s:
        s.add(ActionLog(device_id=1, action="shutdown", source="api", result="ok"))
    assert "Home Assistant" in admin.get("/log").text
