import asyncio

import asyncssh
from sqlalchemy import select

from app.db.models import ActionLog, Device, Room, User
from app.security import hash_password
from app.services.shutdown import (
    Shutdowner,
    ShutdownResult,
    describe,
    key_fingerprint,
    load_or_create_key,
)
from tests.test_web import post


class FakeHost:
    """Local SSH server: accepts only the app's key and records the commands it gets."""

    def __init__(self, client_public_key: str, exit_status: int = 0, stderr: str = "") -> None:
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.authorized = asyncssh.import_authorized_keys(client_public_key)
        self.exit_status = exit_status
        self.stderr = stderr
        self.commands: list[tuple[str, str]] = []
        self.port = 0

    async def __aenter__(self):
        def handle(process: asyncssh.SSHServerProcess) -> None:
            self.commands.append((process.get_extra_info("username"), process.command))
            if self.stderr:
                process.stderr.write(self.stderr + "\n")
            process.exit(self.exit_status)

        self.server = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_host_keys=[self.host_key],
            authorized_client_keys=self.authorized,
            process_factory=handle,
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


def add_device(session_factory, **kw) -> int:
    values = {
        "mac": "aa:bb:cc:dd:ee:01",
        "name": "PC birou",
        "last_ip": "127.0.0.1",
        "status": "online",
        "shutdown_enabled": True,
        "ssh_user": "dan",
        "os": "windows",
    }
    values.update(kw)
    with session_factory.begin() as s:
        device = Device(**values)
        s.add(device)
        s.flush()
        return device.id


def test_key_is_generated_once(tmp_path):
    first = load_or_create_key(tmp_path / "ssh_key")
    again = load_or_create_key(tmp_path / "ssh_key")
    assert key_fingerprint(first) == key_fingerprint(again)
    assert key_fingerprint(first).startswith("ssh-ed25519 ")


def test_shutdown_runs_os_command_and_remembers_host_key(session_factory, tmp_path):
    device_id = add_device(session_factory)
    shutdowner = Shutdowner(session_factory, tmp_path / "ssh_key")

    async def scenario():
        async with FakeHost(shutdowner.public_key()) as host:
            shutdowner.port = host.port
            first = await shutdowner.shutdown(device_id)
            second = await shutdowner.shutdown(device_id, source="api")
            return host, first, second

    host, first, second = asyncio.run(scenario())
    assert first.ok and second.ok, (first, second)
    assert host.commands == [("dan", "shutdown /s /t 0")] * 2
    with session_factory() as s:
        assert s.get(Device, device_id).ssh_host_key == key_fingerprint(host.host_key)
        log = s.scalars(select(ActionLog).order_by(ActionLog.id)).all()
    assert [(e.action, e.source, e.result) for e in log] == [
        ("shutdown", "user", "ok"),
        ("shutdown", "api", "ok"),
    ]


def test_changed_host_key_is_refused(session_factory, tmp_path):
    other = asyncssh.generate_private_key("ssh-ed25519")
    device_id = add_device(session_factory, os="linux", ssh_host_key=key_fingerprint(other))
    shutdowner = Shutdowner(session_factory, tmp_path / "ssh_key")

    async def scenario():
        async with FakeHost(shutdowner.public_key()) as host:
            shutdowner.port = host.port
            return host, await shutdowner.shutdown(device_id)

    host, result = asyncio.run(scenario())
    assert not result.ok and "host key changed" in result.detail
    assert "Amprenta SSH s-a schimbat" in describe("ro", result)
    assert host.commands == []


def test_failed_command_and_unknown_key(session_factory, tmp_path):
    device_id = add_device(session_factory, os="linux")
    shutdowner = Shutdowner(session_factory, tmp_path / "ssh_key")
    stranger = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()

    async def scenario():
        async with FakeHost(
            shutdowner.public_key(), exit_status=1, stderr="sudo: a password is required"
        ) as host:
            shutdowner.port = host.port
            failed = await shutdowner.shutdown(device_id)
        async with FakeHost(stranger) as host:
            shutdowner.port = host.port
            with session_factory.begin() as s:
                s.get(Device, device_id).ssh_host_key = None
            denied = await shutdowner.shutdown(device_id)
        return failed, denied

    failed, denied = asyncio.run(scenario())
    assert not failed.ok and failed.detail == "The command failed: sudo: a password is required"
    assert describe("ro", failed) == (
        "Nu am putut opri „PC birou”: Comanda a eșuat: sudo: a password is required"
    )
    assert not denied.ok and "key isn't accepted" in denied.detail


def test_disabled_device_is_not_contacted(session_factory, tmp_path):
    device_id = add_device(session_factory, shutdown_enabled=False)
    result = asyncio.run(Shutdowner(session_factory, tmp_path / "k").shutdown(device_id))
    assert result.status == "disabled"
    assert describe("en", result) == "The device doesn't have remote shutdown enabled."


class FakeShutdowner:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def public_key(self) -> str:
        return "ssh-ed25519 AAAATEST lanmon"

    async def shutdown(self, device_id: int, source: str = "user"):
        self.calls.append((device_id, source))
        return ShutdownResult("ok", "PC birou")


def test_shutdown_button_and_permissions(admin):
    fake = FakeShutdowner()
    admin.app.state.shutdowner = fake
    sf = admin.app.state.session_factory
    with sf.begin() as s:
        room = Room(name="Birou", icon="briefcase")
        s.add(
            User(
                username="ana",
                password_hash=hash_password("parola-ana"),
                role="member",
                rooms=[room],
            )
        )
    device_id = add_device(sf, room_id=1)
    other_id = add_device(sf, mac="aa:bb:cc:dd:ee:02", name="Server")

    assert "ssh-ed25519 AAAATEST lanmon" in admin.get("/settings").text
    page = admin.get(f"/devices/{device_id}").text
    assert f"/devices/{device_id}/shutdown" in page

    response = post(admin, f"/devices/{device_id}/shutdown", page=f"/devices/{device_id}")
    assert response.status_code == 303 and fake.calls == [(device_id, "user")]

    # The form saves the shutdown settings and can forget the remembered host key.
    with sf.begin() as s:
        s.get(Device, device_id).ssh_host_key = "ssh-ed25519 OLD"
    response = post(
        admin,
        f"/devices/{device_id}",
        {
            "name": "PC birou",
            "room_id": "1",
            "shutdown_enabled": "on",
            "ssh_user": "administrator",
            "os": "linux",
            "forget_host_key": "on",
        },
        page=f"/devices/{device_id}",
    )
    assert response.status_code == 303
    with sf() as s:
        device = s.get(Device, device_id)
        assert (device.ssh_user, device.os, device.ssh_host_key) == ("administrator", "linux", None)

    response = post(
        admin,
        f"/devices/{device_id}",
        {"name": "PC birou", "shutdown_enabled": "on", "ssh_user": ""},
        page=f"/devices/{device_id}",
    )
    assert response.status_code == 400 and "the SSH user" in response.text

    post(admin, "/logout", page="/settings")
    post(admin, "/login", {"username": "ana", "password": "parola-ana"})
    assert "AAAATEST" not in admin.get("/settings").text
    post(admin, f"/devices/{device_id}/shutdown", page=f"/devices/{device_id}")
    assert fake.calls[-1] == (device_id, "user") and len(fake.calls) == 2
    response = post(admin, f"/devices/{other_id}/shutdown", page="/settings")
    assert response.status_code == 403 and len(fake.calls) == 2


def test_unreachable_host(session_factory, tmp_path):
    device_id = add_device(session_factory)
    shutdowner = Shutdowner(session_factory, tmp_path / "ssh_key", port=1)
    result = asyncio.run(shutdowner.shutdown(device_id))
    assert not result.ok and result.detail.startswith("The SSH connection failed: ")
