import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionLog, Device, Discovered
from app.db.settings_store import get_int
from app.events import Event, EventBus
from app.i18n import N_
from app.services.arp import ArpReply, ArpUnavailable, arp_scan
from app.services.netinfo import NetInfo
from app.services.oui import lookup_vendor

log = logging.getLogger(__name__)

# last_seen_at is written at most every 15 minutes so the QNAP disks can spin down.
LAST_SEEN_WRITE_EVERY = timedelta(minutes=15)


@dataclass(frozen=True)
class Seen:
    ip: str
    at: datetime


@dataclass(frozen=True)
class ScanResult:
    found: int
    new: int
    at: datetime


async def resolve_hostname(ip: str, timeout: float = 2) -> str | None:
    try:
        name, _aliases, _ips = await asyncio.wait_for(
            asyncio.to_thread(socket.gethostbyaddr, ip), timeout
        )
    except (OSError, TimeoutError):
        return None
    return name.rstrip(".") or None


class Scanner:
    """Periodic and on-demand ARP scans; new MACs land in `discovered`."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventBus,
        network: Callable[[], NetInfo],
        arp: Callable[[str, str | None, float], list[ArpReply]] = arp_scan,
        resolve: Callable[[str], object] = resolve_hostname,
        arp_timeout: float = 3,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._network = network
        self._arp = arp
        self._resolve = resolve
        self._arp_timeout = arp_timeout
        self._trigger = asyncio.Event()
        self._lock = asyncio.Lock()
        self.seen: dict[str, Seen] = {}
        self.last_result: ScanResult | None = None
        # Why scanning doesn't work (shown on the Dashboard); None when it works.
        self.last_error: str | None = None

    def request_scan(self) -> None:
        """“Scan now”: the next cycle starts immediately."""
        self._trigger.set()

    async def run(self) -> None:
        manual = False
        while True:
            try:
                await self.scan_once(manual=manual)
            except ArpUnavailable as exc:
                # A setup problem, not a bug: one clear message, no traceback every cycle.
                if self.last_error != str(exc):
                    log.warning("%s", exc)
                self.last_error = str(exc)
            except Exception as exc:
                log.exception("ARP scan failed")
                self.last_error = f"{N_('The ARP scan failed')}: {exc}"
            interval = await asyncio.to_thread(self._interval)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._trigger.wait(), timeout=interval)
            manual = self._trigger.is_set()
            self._trigger.clear()

    def _interval(self) -> int:
        with self._session_factory() as session:
            return get_int(session, "scan_interval_s", 300)

    async def scan_once(self, manual: bool = False) -> ScanResult:
        async with self._lock:
            started = datetime.now(UTC)
            try:
                info = await asyncio.to_thread(self._network)
                replies = await asyncio.to_thread(
                    self._arp, str(info.network), info.interface, self._arp_timeout
                )
            except Exception as exc:
                if manual:
                    await asyncio.to_thread(self._log_manual, started, "error", str(exc))
                raise

            # Reverse DNS only for new MACs or ones whose IP changed.
            to_resolve = [r for r in replies if self.seen.get(r.mac, Seen("", started)).ip != r.ip]
            names = await asyncio.gather(*(self._resolve(r.ip) for r in to_resolve))
            hostnames = {r.mac: n for r, n in zip(to_resolve, names, strict=True) if n}

            new_macs = await asyncio.to_thread(self._apply, replies, hostnames, started)
            for reply in replies:
                self.seen[reply.mac] = Seen(reply.ip, started)
            result = ScanResult(found=len(replies), new=len(new_macs), at=started)
            self.last_result = result
            self.last_error = None
            if manual:
                detail = f"{result.found}/{result.new}"  # found/new, rendered by the log page
                await asyncio.to_thread(self._log_manual, started, "ok", detail)

        for mac in new_macs:
            self._events.publish(Event("device_discovered", {"mac": mac}))
        self._events.publish(Event("scan_completed", {"found": result.found, "new": result.new}))
        log.info("ARP scan: %d devices, %d new", result.found, result.new)
        return result

    def _apply(
        self, replies: list[ArpReply], hostnames: dict[str, str], now: datetime
    ) -> list[str]:
        macs = [r.mac for r in replies]
        new_macs: list[str] = []
        with self._session_factory.begin() as session:
            devices = {
                d.mac: d for d in session.scalars(select(Device).where(Device.mac.in_(macs)))
            }
            known = {
                d.mac: d
                for d in session.scalars(select(Discovered).where(Discovered.mac.in_(macs)))
            }
            for reply in replies:
                hostname = hostnames.get(reply.mac)
                device = devices.get(reply.mac)
                if device is not None:
                    if device.last_ip != reply.ip:
                        device.last_ip = reply.ip
                    if hostname and device.hostname != hostname:
                        device.hostname = hostname
                    continue

                entry = known.get(reply.mac)
                if entry is None:
                    session.add(
                        Discovered(
                            mac=reply.mac,
                            ip=reply.ip,
                            hostname=hostname,
                            vendor=lookup_vendor(reply.mac),
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                    )
                    new_macs.append(reply.mac)
                    continue

                changed = entry.ip != reply.ip or (hostname and entry.hostname != hostname)
                if changed or now - entry.last_seen_at >= LAST_SEEN_WRITE_EVERY:
                    entry.ip = reply.ip
                    entry.hostname = hostname or entry.hostname
                    entry.last_seen_at = now
        return new_macs

    def _log_manual(self, started: datetime, result: str, detail: str) -> None:
        with self._session_factory.begin() as session:
            session.add(
                ActionLog(
                    action="scan",
                    source="user",
                    result=result,
                    detail=detail,
                    created_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
