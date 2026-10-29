import asyncio

import pytest

from app.db.models import Device, DeviceType
from app.events import EventBus
from app.services.notifier import Notifier
from app.services.pinger import Pinger
from app.services.portcheck import closed_ports, parse_ports, tcp_open
from tests.test_notifier import Outbox, configure
from tests.test_web import post


def test_parse_ports():
    assert parse_ports("3389, 22;443 22") == [22, 443, 3389]
    assert parse_ports("") == []
    with pytest.raises(ValueError):
        parse_ports("80, rdp")
    with pytest.raises(ValueError):
        parse_ports("70000")


def test_tcp_open_against_local_server():
    async def scenario():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            assert await tcp_open("127.0.0.1", port)
            return await closed_ports("127.0.0.1", [port, 1])
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(scenario()) == [1]


class FakeLan:
    def __init__(self, closed=()):
        self.closed = list(closed)
        self.probed = []

    async def ping_many(self, ips):
        return set(ips)

    async def probe(self, ip, ports):
        self.probed.append((ip, ports))
        return [p for p in ports if p in self.closed]


def make(session_factory, lan, bus=None):
    return Pinger(
        session_factory,
        bus or EventBus(),
        ping_many=lan.ping_many,
        arp_lookup=dict,
        port_probe=lan.probe,
    )


def seed(session_factory, device_ports=None, type_ports="3389"):
    with session_factory.begin() as s:
        t = DeviceType(name="Desktop", check_ports=type_ports)
        s.add(
            Device(
                mac="aa:bb:cc:dd:ee:01",
                name="PC",
                last_ip="192.168.1.10",
                type=t,
                status="online",
                check_ports=device_ports,
                notify=True,
            )
        )


def device(session_factory):
    with session_factory() as s:
        d = s.get(Device, 1)
        return d.service_status, d.closed_ports


def test_service_down_after_two_cycles_and_recovery(session_factory):
    seed(session_factory)
    lan = FakeLan(closed=[3389])
    bus = EventBus()
    events = bus.subscribe()
    pinger = make(session_factory, lan, bus)

    asyncio.run(pinger.cycle())
    assert device(session_factory) == (None, None)  # a single failure isn't enough
    asyncio.run(pinger.cycle())
    assert device(session_factory) == ("down", "3389")
    assert lan.probed[0] == ("192.168.1.10", [3389])

    lan.closed = []
    asyncio.run(pinger.cycle())
    assert device(session_factory) == ("ok", None)
    kinds = [events.get_nowait() for _ in range(events.qsize())]
    assert [(e.data["service"], e.data["closed"]) for e in kinds if e.kind == "service_status"] == [
        ("down", "3389"),
        ("ok", None),
    ]


def test_device_ports_override_type_and_dash_disables(session_factory):
    seed(session_factory, device_ports="22, 80")
    lan = FakeLan()
    asyncio.run(make(session_factory, lan).cycle())
    assert lan.probed == [("192.168.1.10", [22, 80])]

    with session_factory.begin() as s:
        s.get(Device, 1).check_ports = "-"
    lan.probed.clear()
    asyncio.run(make(session_factory, lan).cycle())
    assert lan.probed == []


def test_offline_devices_are_not_probed(session_factory):
    seed(session_factory)
    lan = FakeLan()

    async def nobody(ips):
        return set()

    lan.ping_many = nobody
    asyncio.run(make(session_factory, lan).cycle())
    assert lan.probed == []


def test_service_notifications(session_factory):
    from app.events import Event

    seed(session_factory)
    configure(session_factory)
    outbox = Outbox()
    notifier = Notifier(session_factory, EventBus(), send=outbox)
    down = {"device_id": 1, "service": "down", "closed": "3389"}
    asyncio.run(notifier.handle(Event("service_status", down)))
    asyncio.run(notifier.handle(Event("service_status", {"device_id": 1, "service": "ok"})))
    asyncio.run(notifier.handle(Event("service_status", {"device_id": 1, "service": "ok"})))
    assert outbox.sent == [
        "PC: service down (closed ports: 3389).",
        "PC: services are answering again.",
    ]


def test_ui_forms_and_badges(admin):
    sf = admin.app.state.session_factory
    with sf.begin() as s:
        s.add(
            Device(
                mac="aa:bb:cc:dd:ee:01",
                name="PC",
                status="online",
                service_status="down",
                closed_ports="3389",
                check_ports="3389",
            )
        )
    assert "Service down" in admin.get("/").text
    assert "closed: 3389" in admin.get("/devices/1").text

    bad = post(admin, "/devices/1", {"name": "PC", "check_ports": "rdp"}, page="/devices/1")
    assert bad.status_code == 400 and "Invalid port" in bad.text
    post(admin, "/devices/1", {"name": "PC", "check_ports": "443 22"}, page="/devices/1")
    with sf() as s:
        assert s.get(Device, 1).check_ports == "22, 443"

    post(
        admin, "/types/1", {"name": "PC", "icon": "monitor", "check_ports": "3389"}, page="/types/1"
    )
    with sf() as s:
        assert s.get(DeviceType, 1).check_ports == "3389"
    assert 'value="3389"' in admin.get("/types/1").text
    assert "Porturi TCP" not in admin.get("/rooms/new").text
