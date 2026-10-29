import asyncio
import ipaddress

from sqlalchemy import select

from app.db.models import ActionLog, Device, Setting
from app.events import EventBus
from app.services.netinfo import NetInfo
from app.services.waker import Waker

NET = NetInfo("eth0", ipaddress.IPv4Interface("192.168.1.2/24"))


class FakeLan:
    def __init__(self, wake_after_pings=None):
        self.sent = []
        self.pings = 0
        self.wake_after_pings = wake_after_pings

    def send(self, mac, broadcasts, ports, source_ip=None):
        self.sent.append((mac, list(broadcasts), tuple(ports), source_ip))
        return len(broadcasts) * len(ports)

    async def ping_many(self, ips):
        self.pings += 1
        if self.wake_after_pings is not None and self.pings >= self.wake_after_pings:
            return set(ips)
        return set()


def add_device(session_factory, wol=True, status="offline", mac="aa:bb:cc:dd:ee:01"):
    with session_factory.begin() as s:
        d = Device(mac=mac, name="PC", last_ip="192.168.1.10", wol_enabled=wol, status=status)
        s.add(d)
        s.flush()
        return d.id


def set_timeout(session_factory, seconds):
    with session_factory.begin() as s:
        s.get(Setting, "wol_timeout_s").value = str(seconds)


def make_waker(session_factory, lan):
    bus = EventBus()
    waker = Waker(
        session_factory,
        bus,
        network=lambda: NET,
        send=lan.send,
        ping_many=lan.ping_many,
        poll_interval=0.01,
    )
    return waker, bus


async def wake_and_wait(waker, ids, source="user"):
    sent = await waker.wake(ids, source)
    while waker.pending:
        await asyncio.sleep(0.01)
    return sent


def last_action(session_factory):
    with session_factory() as s:
        return s.scalar(select(ActionLog).order_by(ActionLog.id.desc()))


def test_wake_sends_magic_packet_and_detects_boot(session_factory):
    device_id = add_device(session_factory)
    lan = FakeLan(wake_after_pings=3)
    waker, bus = make_waker(session_factory, lan)
    events = bus.subscribe()

    sent = asyncio.run(wake_and_wait(waker, [device_id]))

    assert sent == [device_id]
    assert lan.sent == [
        ("aa:bb:cc:dd:ee:01", ["192.168.1.255", "255.255.255.255"], (9, 7), "192.168.1.2")
    ]
    entry = last_action(session_factory)
    assert (entry.action, entry.source, entry.result) == ("wol", "user", "woke")
    assert entry.finished_at is not None
    results = [events.get_nowait().data["result"] for _ in range(events.qsize())]
    assert results == ["sent", "woke"]


def test_wake_times_out(session_factory):
    device_id = add_device(session_factory)
    set_timeout(session_factory, 0.05)
    lan = FakeLan()
    waker, _ = make_waker(session_factory, lan)

    asyncio.run(wake_and_wait(waker, [device_id], source="room"))

    entry = last_action(session_factory)
    assert (entry.source, entry.result) == ("room", "timeout")


def test_device_marked_online_by_pinger_counts_as_woke(session_factory):
    device_id = add_device(session_factory)
    lan = FakeLan()
    waker, _ = make_waker(session_factory, lan)

    async def scenario():
        await waker.wake([device_id])
        with session_factory.begin() as s:
            s.get(Device, device_id).status = "online"
        while waker.pending:
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert last_action(session_factory).result == "woke"


def test_wol_disabled_devices_are_skipped(session_factory):
    device_id = add_device(session_factory, wol=False)
    lan = FakeLan()
    waker, _ = make_waker(session_factory, lan)
    assert asyncio.run(waker.wake([device_id])) == []
    assert lan.sent == []


def test_send_error_is_logged(session_factory):
    device_id = add_device(session_factory)
    lan = FakeLan()

    def broken_send(*args, **kwargs):
        raise OSError("Network is unreachable")

    lan.send = broken_send
    waker, _ = make_waker(session_factory, lan)
    assert asyncio.run(waker.wake([device_id])) == []
    entry = last_action(session_factory)
    assert (entry.result, entry.detail) == ("error", "Sending failed: Network is unreachable")


def test_interrupted_actions_closed_on_startup(session_factory):
    device_id = add_device(session_factory)
    with session_factory.begin() as s:
        s.add(ActionLog(device_id=device_id, action="wol", source="user", result="sent"))
    waker, _ = make_waker(session_factory, FakeLan())
    waker.close_interrupted()
    entry = last_action(session_factory)
    assert entry.result == "error" and entry.finished_at is not None
