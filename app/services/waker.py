import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionLog, Device
from app.db.settings_store import get_float
from app.events import Event, EventBus
from app.i18n import N_
from app.services.netinfo import NetInfo
from app.services.pinger import ONLINE, PingMany, icmp_ping_many
from app.services.wol import WOL_PORTS, send_magic_packet

log = logging.getLogger(__name__)

SENT, WOKE, TIMEOUT, ERROR = "sent", "woke", "timeout", "error"


@dataclass(frozen=True)
class WakeTarget:
    id: int
    name: str
    mac: str
    ip: str | None


class Waker:
    """Sends the magic packet and checks by ping whether the device came up."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventBus,
        network: Callable[[], NetInfo],
        send: Callable[..., int] = send_magic_packet,
        ping_many: PingMany = icmp_ping_many,
        poll_interval: float = 5,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._network = network
        self._send = send
        self._ping_many = ping_many
        self.poll_interval = poll_interval
        self.pending: dict[int, int] = {}  # device_id → id of its actions_log row
        self._tasks: set[asyncio.Task] = set()

    def close_interrupted(self) -> None:
        """On startup: actions still marked "sent" were interrupted by a restart."""
        with self._session_factory.begin() as session:
            session.execute(
                update(ActionLog)
                .where(ActionLog.action == "wol", ActionLog.result == SENT)
                .values(
                    result=ERROR,
                    detail=N_("Interrupted by an app restart"),
                    finished_at=datetime.now(UTC),
                )
            )

    async def wake(self, device_ids: Iterable[int], source: str = "user") -> list[int]:
        """Starts the check for each device; returns the ids the packet was sent to."""
        targets = await asyncio.to_thread(self._load, list(device_ids))
        targets = [t for t in targets if t.id not in self.pending]
        if not targets:
            return []
        timeout = await asyncio.to_thread(self._timeout)
        try:
            info = await asyncio.to_thread(self._network)
            broadcasts = [info.broadcast, "255.255.255.255"]
            source_ip: str | None = str(info.address.ip)
        except Exception as exc:
            log.warning("Couldn't detect the network for WOL: %s", exc)
            broadcasts, source_ip = ["255.255.255.255"], None

        sent = []
        for target in targets:
            try:
                await asyncio.to_thread(
                    self._send, target.mac, broadcasts, WOL_PORTS, source_ip=source_ip
                )
            except OSError as exc:
                detail = f"{N_('Sending failed')}: {exc}"
                await asyncio.to_thread(self._log, target, source, ERROR, detail, True)
                self._publish(target, ERROR)
                continue
            action_id = await asyncio.to_thread(self._log, target, source, SENT, None, False)
            self.pending[target.id] = action_id
            task = asyncio.create_task(self._verify(target, action_id, timeout))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self._publish(target, SENT)
            sent.append(target.id)
        return sent

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _verify(self, target: WakeTarget, action_id: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        result = TIMEOUT
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(self.poll_interval)
                # The IP and status may be updated meanwhile by the scanner and the pinger.
                ip, status = await asyncio.to_thread(self._current, target.id)
                if status == ONLINE or (ip and ip in await self._ping_many([ip])):
                    result = WOKE
                    break
        except asyncio.CancelledError:
            self.pending.pop(target.id, None)
            raise
        except Exception:
            log.exception("WOL check failed for %s", target.name)
            result = ERROR
        try:
            await asyncio.to_thread(self._finish, action_id, result)
            self._publish(target, result)
        finally:
            # Removed from `pending` only once the result is logged, so the UI never sees
            # a finished wake-up that still shows as "sent".
            self.pending.pop(target.id, None)
        log.info("WOL %s: %s", target.name, result)

    def _publish(self, target: WakeTarget, result: str) -> None:
        self._events.publish(Event("wol", {"device_id": target.id, "result": result}))

    def _timeout(self) -> float:
        with self._session_factory() as session:
            return get_float(session, "wol_timeout_s", 180)

    def _load(self, device_ids: list[int]) -> list[WakeTarget]:
        with self._session_factory() as session:
            rows = session.execute(
                select(Device.id, Device.name, Device.mac, Device.last_ip).where(
                    Device.id.in_(device_ids), Device.wol_enabled.is_(True)
                )
            )
            return [WakeTarget(*row) for row in rows]

    def _current(self, device_id: int) -> tuple[str | None, str | None]:
        with self._session_factory() as session:
            row = session.execute(
                select(Device.last_ip, Device.status).where(Device.id == device_id)
            ).first()
            return (row[0], row[1]) if row else (None, None)

    def _log(
        self, target: WakeTarget, source: str, result: str, detail: str | None, finished: bool
    ) -> int:
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            entry = ActionLog(
                device_id=target.id,
                action="wol",
                source=source,
                result=result,
                detail=detail,
                created_at=now,
                finished_at=now if finished else None,
            )
            session.add(entry)
            session.flush()
            return entry.id

    def _finish(self, action_id: int, result: str) -> None:
        with self._session_factory.begin() as session:
            entry = session.get(ActionLog, action_id)
            if entry is not None:
                entry.result = result
                entry.finished_at = datetime.now(UTC)
