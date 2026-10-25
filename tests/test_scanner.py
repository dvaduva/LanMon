import asyncio
import ipaddress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import ActionLog, Device, Discovered
from app.events import EventBus
from app.services.arp import ArpReply
from app.services.netinfo import NetInfo
from app.services.scanner import Scanner

NET = NetInfo("eth0", ipaddress.IPv4Interface("192.168.1.2/24"))


def make_scanner(session_factory, replies, names=None):
    calls = {"resolve": []}

    def arp(network, iface, timeout):
        assert (network, iface) == ("192.168.1.0/24", "eth0")
        return list(replies)

    async def resolve(ip):
        calls["resolve"].append(ip)
        return (names or {}).get(ip)

    bus = EventBus()
    scanner = Scanner(session_factory, bus, network=lambda: NET, arp=arp, resolve=resolve)
    return scanner, bus, calls


def test_new_macs_land_in_discovered(session_factory):
    replies = [
        ArpReply("192.168.1.20", "00:11:32:aa:bb:cc"),
        ArpReply("192.168.1.30", "da:a1:19:00:00:01"),
    ]
    scanner, bus, _ = make_scanner(session_factory, replies, {"192.168.1.20": "nas.lan"})
    events = bus.subscribe()

    result = asyncio.run(scanner.scan_once())

    assert (result.found, result.new) == (2, 2)
    with session_factory() as s:
        rows = {d.mac: d for d in s.scalars(select(Discovered))}
    assert rows["00:11:32:aa:bb:cc"].vendor == "Synology Incorporated"
    assert rows["00:11:32:aa:bb:cc"].hostname == "nas.lan"
    assert rows["da:a1:19:00:00:01"].vendor is None
    kinds = [events.get_nowait().kind for _ in range(events.qsize())]
    assert kinds == ["device_discovered", "device_discovered", "scan_completed"]


def test_saved_device_gets_ip_update_and_stays_out_of_discovered(session_factory):
    with session_factory.begin() as s:
        s.add(Device(mac="aa:bb:cc:dd:ee:01", name="PC birou", last_ip="192.168.1.50"))
    scanner, _, _ = make_scanner(session_factory, [ArpReply("192.168.1.51", "aa:bb:cc:dd:ee:01")])

    asyncio.run(scanner.scan_once())

    with session_factory() as s:
        assert s.scalar(select(Device.last_ip)) == "192.168.1.51"
        assert s.scalar(select(Discovered.mac)) is None


def test_last_seen_written_at_most_every_15_minutes(session_factory):
    old = datetime.now(UTC) - timedelta(minutes=5)
    with session_factory.begin() as s:
        s.add(
            Discovered(
                mac="aa:bb:cc:dd:ee:02", ip="192.168.1.60", first_seen_at=old, last_seen_at=old
            )
        )
    scanner, _, calls = make_scanner(
        session_factory, [ArpReply("192.168.1.60", "aa:bb:cc:dd:ee:02")]
    )

    asyncio.run(scanner.scan_once())
    with session_factory() as s:
        assert s.get(Discovered, "aa:bb:cc:dd:ee:02").last_seen_at == old

    with session_factory.begin() as s:
        s.get(Discovered, "aa:bb:cc:dd:ee:02").last_seen_at = old - timedelta(minutes=15)
    asyncio.run(scanner.scan_once())
    with session_factory() as s:
        assert s.get(Discovered, "aa:bb:cc:dd:ee:02").last_seen_at > old
    assert calls["resolve"] == ["192.168.1.60"]  # reverse DNS doar prima dată


def test_manual_scan_is_logged(session_factory):
    scanner, _, _ = make_scanner(session_factory, [ArpReply("192.168.1.20", "aa:bb:cc:dd:ee:03")])

    asyncio.run(scanner.scan_once(manual=True))

    with session_factory() as s:
        entry = s.scalar(select(ActionLog))
    assert (entry.action, entry.source, entry.result) == ("scan", "user", "ok")
    assert entry.detail == "1 dispozitive, 1 noi"
