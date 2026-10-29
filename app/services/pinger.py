import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.db.models import Device, StatusEvent
from app.db.settings_store import get_int
from app.events import Event, EventBus
from app.services.arptable import read_arp_table
from app.services.portcheck import closed_ports, parse_ports
from app.services.scanner import LAST_SEEN_WRITE_EVERY

log = logging.getLogger(__name__)

ONLINE, OFFLINE, UNKNOWN = "online", "offline", "unknown"
PingMany = Callable[[list[str]], Awaitable[set[str]]]
PortProbe = Callable[[str, list[int]], Awaitable[list[int]]]
SERVICE_OK, SERVICE_DOWN = "ok", "down"
# „Serviciu căzut” abia după atâtea cicluri consecutive cu porturi închise.
SERVICE_DOWN_AFTER = 2


class StatusTracker:
    """Anti-flapping: „offline” după N eșecuri consecutive, „online” la primul răspuns."""

    def __init__(self, offline_after: int = 2) -> None:
        self.offline_after = offline_after
        self._failures: dict[int, int] = {}

    def observe(self, device_id: int, current: str, alive: bool) -> str | None:
        """Întoarce noua stare dacă se schimbă, altfel None."""
        if alive:
            self._failures[device_id] = 0
            return ONLINE if current != ONLINE else None
        failures = self._failures.get(device_id, 0) + 1
        self._failures[device_id] = failures
        if current == UNKNOWN or (current == ONLINE and failures >= self.offline_after):
            return OFFLINE
        return None


async def icmp_ping_many(ips: list[str]) -> set[str]:
    from icmplib import async_multiping

    if not ips:
        return set()
    hosts = await async_multiping(
        ips, count=2, interval=0.2, timeout=1, concurrent_tasks=64, privileged=True
    )
    return {h.address for h in hosts if h.is_alive}


@dataclass(frozen=True)
class Target:
    id: int
    mac: str
    ip: str | None
    status: str
    ports: tuple[int, ...] = ()
    service_status: str | None = None
    closed: str | None = None


@dataclass(frozen=True)
class Outcome:
    target: Target
    alive: bool
    ip: str | None
    new_status: str | None
    # (stare serviciu, porturi închise) când se schimbă față de ce e în DB, altfel None
    service: tuple[str | None, str | None] | None = None


class Pinger:
    """Ping periodic pentru dispozitivele salvate; scrie schimbările de stare."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventBus,
        ping_many: PingMany = icmp_ping_many,
        arp_lookup: Callable[[], dict[str, str]] = read_arp_table,
        extra_arp: Callable[[], dict[str, str]] | None = None,
        tracker: StatusTracker | None = None,
        port_probe: PortProbe = closed_ports,
    ) -> None:
        self._port_probe = port_probe
        self._service_failures: dict[int, int] = {}
        self._session_factory = session_factory
        self._events = events
        self._ping_many = ping_many
        self._arp_lookup = arp_lookup
        self._extra_arp = extra_arp
        self._tracker = tracker or StatusTracker()
        # last_seen_at ținut în memorie; în DB doar la schimbarea stării sau o dată la 15 min.
        self._last_seen: dict[int, datetime] = {}
        self._last_seen_written: dict[int, datetime] = {}

    async def run(self) -> None:
        while True:
            try:
                await self.cycle()
            except Exception:
                log.exception("Ciclul de ping a eșuat")
            await asyncio.sleep(await asyncio.to_thread(self._interval))

    def _interval(self) -> int:
        with self._session_factory() as session:
            return get_int(session, "ping_interval_s", 30)

    def _load_targets(self) -> list[Target]:
        with self._session_factory() as session:
            devices = session.scalars(select(Device).options(joinedload(Device.type)))
            targets = []
            for d in devices:
                try:
                    ports = tuple(parse_ports(d.effective_ports))
                except ValueError:
                    ports = ()
                targets.append(
                    Target(
                        d.id, d.mac, d.last_ip, d.status, ports, d.service_status, d.closed_ports
                    )
                )
            return targets

    async def _check_services(self, outcomes: list[Outcome]) -> list[Outcome]:
        """Porturile TCP, doar pentru dispozitivele care răspund la ping."""
        checked = [o for o in outcomes if o.alive and o.ip and o.target.ports]
        results = await asyncio.gather(
            *(self._port_probe(o.ip, list(o.target.ports)) for o in checked)
        )
        closed_by_id = {o.target.id: c for o, c in zip(checked, results, strict=True)}
        updated = []
        for o in outcomes:
            t = o.target
            if t.id in closed_by_id:
                closed = closed_by_id[t.id]
                if closed:
                    failures = self._service_failures.get(t.id, 0) + 1
                    self._service_failures[t.id] = failures
                    if failures >= SERVICE_DOWN_AFTER or t.service_status == SERVICE_DOWN:
                        wanted = (SERVICE_DOWN, ", ".join(map(str, closed)))
                    else:
                        wanted = (t.service_status, t.closed)  # încă nu e sigur
                else:
                    self._service_failures[t.id] = 0
                    wanted = (SERVICE_OK, None)
            else:
                self._service_failures.pop(t.id, None)
                wanted = (None, None)
            current = (t.service_status, t.closed)
            service = wanted if wanted != current else None
            updated.append(Outcome(t, o.alive, o.ip, o.new_status, service))
        return updated

    def _arp_table(self) -> dict[str, str]:
        table = dict(self._extra_arp() if self._extra_arp else {})
        table.update(self._arp_lookup())  # kernelul are întâietate, e mai proaspăt
        return table

    async def cycle(self) -> list[Outcome]:
        now = datetime.now(UTC)
        targets = await asyncio.to_thread(self._load_targets)
        if not targets:
            return []

        alive = await self._ping_many(sorted({t.ip for t in targets if t.ip}))

        # Fără răspuns: poate DHCP a dat alt IP. Căutăm MAC-ul în tabela ARP.
        failed = [t for t in targets if t.ip not in alive]
        moved: dict[int, str] = {}
        if failed:
            table = await asyncio.to_thread(self._arp_table)
            candidates = {t.id: table[t.mac] for t in failed if table.get(t.mac, t.ip) != t.ip}
            if candidates:
                alive_moved = await self._ping_many(sorted(set(candidates.values())))
                moved = {i: ip for i, ip in candidates.items() if ip in alive_moved}

        outcomes = []
        for target in targets:
            ip = moved.get(target.id, target.ip)
            is_alive = target.ip in alive or target.id in moved
            if is_alive:
                self._last_seen[target.id] = now
            new_status = self._tracker.observe(target.id, target.status, is_alive)
            outcomes.append(Outcome(target, is_alive, ip, new_status))

        outcomes = await self._check_services(outcomes)
        await asyncio.to_thread(self._apply, outcomes, now)

        for o in outcomes:
            if o.new_status:
                data = {"device_id": o.target.id, "status": o.new_status, "ip": o.ip}
                self._events.publish(Event("device_status", data))
            if o.service:
                data = {"device_id": o.target.id, "service": o.service[0], "closed": o.service[1]}
                self._events.publish(Event("service_status", data))
        return outcomes

    def _apply(self, outcomes: list[Outcome], now: datetime) -> None:
        with self._session_factory.begin() as session:
            for o in outcomes:
                ip_changed = o.ip != o.target.ip
                last_seen = self._last_seen.get(o.target.id)
                written = self._last_seen_written.get(o.target.id)
                flush_seen = last_seen is not None and (
                    o.new_status is not None
                    or written is None
                    or now - written >= LAST_SEEN_WRITE_EVERY
                )
                if not (o.new_status or ip_changed or flush_seen or o.service):
                    continue

                device = session.get(Device, o.target.id)
                if device is None:
                    continue
                if ip_changed:
                    device.last_ip = o.ip
                if flush_seen:
                    device.last_seen_at = last_seen
                    self._last_seen_written[o.target.id] = now
                if o.service:
                    device.service_status, device.closed_ports = o.service
                if o.new_status:
                    device.status = o.new_status
                    device.status_changed_at = now
                    session.add(
                        StatusEvent(device_id=device.id, status=o.new_status, ip=o.ip, at=now)
                    )
