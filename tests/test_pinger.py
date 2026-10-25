import asyncio

from sqlalchemy import select

from app.db.models import Device, StatusEvent
from app.events import EventBus
from app.services.pinger import OFFLINE, ONLINE, UNKNOWN, Pinger, StatusTracker


def test_tracker_anti_flapping():
    t = StatusTracker(offline_after=2)
    assert t.observe(1, UNKNOWN, True) == ONLINE
    assert t.observe(1, ONLINE, False) is None  # primul eșec nu contează
    assert t.observe(1, ONLINE, True) is None  # răspunsul resetează contorul
    assert t.observe(1, ONLINE, False) is None
    assert t.observe(1, ONLINE, False) == OFFLINE
    assert t.observe(1, OFFLINE, False) is None
    assert t.observe(1, OFFLINE, True) == ONLINE


def test_tracker_unknown_goes_offline_after_first_cycle():
    assert StatusTracker().observe(1, UNKNOWN, False) == OFFLINE


class FakeNet:
    def __init__(self, alive=(), arp=None):
        self.alive = set(alive)
        self.arp = dict(arp or {})
        self.pinged: list[list[str]] = []

    async def ping_many(self, ips):
        self.pinged.append(ips)
        return {ip for ip in ips if ip in self.alive}


def add_device(session_factory, **kw):
    with session_factory.begin() as s:
        d = Device(name="PC", **kw)
        s.add(d)
        s.flush()
        return d.id


def make_pinger(session_factory, net):
    bus = EventBus()
    pinger = Pinger(session_factory, bus, ping_many=net.ping_many, arp_lookup=lambda: net.arp)
    return pinger, bus


def test_status_transitions_write_events(session_factory):
    device_id = add_device(session_factory, mac="aa:bb:cc:dd:ee:01", last_ip="192.168.1.10")
    net = FakeNet(alive={"192.168.1.10"})
    pinger, bus = make_pinger(session_factory, net)
    events = bus.subscribe()

    asyncio.run(pinger.cycle())  # unknown → online
    net.alive.clear()
    asyncio.run(pinger.cycle())  # un eșec: rămâne online
    asyncio.run(pinger.cycle())  # două eșecuri: offline

    with session_factory() as s:
        device = s.get(Device, device_id)
        history = [e.status for e in s.scalars(select(StatusEvent).order_by(StatusEvent.id))]
    assert device.status == OFFLINE
    assert device.last_seen_at is not None and device.status_changed_at is not None
    assert history == [ONLINE, OFFLINE]
    published = [events.get_nowait().data["status"] for _ in range(events.qsize())]
    assert published == [ONLINE, OFFLINE]


def test_ip_changed_by_dhcp_is_picked_up_from_arp(session_factory):
    device_id = add_device(
        session_factory, mac="aa:bb:cc:dd:ee:02", last_ip="192.168.1.20", status=ONLINE
    )
    net = FakeNet(alive={"192.168.1.21"}, arp={"aa:bb:cc:dd:ee:02": "192.168.1.21"})
    pinger, _ = make_pinger(session_factory, net)

    outcomes = asyncio.run(pinger.cycle())

    assert outcomes[0].alive and outcomes[0].new_status is None
    assert net.pinged == [["192.168.1.20"], ["192.168.1.21"]]
    with session_factory() as s:
        assert s.get(Device, device_id).last_ip == "192.168.1.21"


def test_no_devices_no_ping(session_factory):
    net = FakeNet()
    pinger, _ = make_pinger(session_factory, net)
    assert asyncio.run(pinger.cycle()) == []
    assert net.pinged == []
