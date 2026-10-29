import asyncio
import contextlib
import logging
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Device, Discovered
from app.db.settings_store import get_int, get_str
from app.events import Event, EventBus

log = logging.getLogger(__name__)

CHECK_EVERY_S = 30


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    security: str  # starttls / ssl / none
    username: str | None
    password: str | None
    sender: str
    recipient: str


@dataclass(frozen=True)
class NotifyConfig:
    telegram_token: str | None
    telegram_chat: str | None
    email: EmailConfig | None
    offline_min: int
    new_device: bool
    wol_failed: bool

    @property
    def channels(self) -> list[str]:
        names = []
        if self.telegram_token and self.telegram_chat:
            names.append("Telegram")
        if self.email:
            names.append("email")
        return names


def load_config(session: Session) -> NotifyConfig:
    host = get_str(session, "notify_email_host")
    recipient = get_str(session, "notify_email_to")
    email = None
    if host and recipient:
        email = EmailConfig(
            host=host,
            port=get_int(session, "notify_email_port", 587),
            security=get_str(session, "notify_email_security") or "starttls",
            username=get_str(session, "notify_email_user"),
            password=get_str(session, "notify_email_password"),
            sender=get_str(session, "notify_email_from") or recipient,
            recipient=recipient,
        )
    return NotifyConfig(
        telegram_token=get_str(session, "notify_telegram_token"),
        telegram_chat=get_str(session, "notify_telegram_chat"),
        email=email,
        offline_min=get_int(session, "notify_offline_min", 10),
        new_device=get_str(session, "notify_new_device") == "1",
        wol_failed=get_str(session, "notify_wol_failed") == "1",
    )


async def send_telegram(token: str, chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
    if response.status_code != 200:
        try:
            detail = response.json().get("description", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Telegram: {detail}")


def send_email(config: EmailConfig, subject: str, text: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message.set_content(text)
    context = ssl.create_default_context()
    if config.security == "ssl":
        smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(config.host, config.port, timeout=20)
    with smtp:
        if config.security == "starttls":
            smtp.starttls(context=context)
        if config.username:
            smtp.login(config.username, config.password or "")
        smtp.send_message(message)


Sender = Callable[[NotifyConfig, str], Awaitable[list[str]]]


async def deliver(config: NotifyConfig, text: str) -> list[str]:
    """Trimite pe toate canalele configurate; întoarce erorile (gol = totul a mers)."""
    errors = []
    if config.telegram_token and config.telegram_chat:
        try:
            await send_telegram(config.telegram_token, config.telegram_chat, text)
        except Exception as exc:
            errors.append(f"Telegram: {exc}")
    if config.email:
        subject = "LanMon: " + text.splitlines()[0]
        try:
            await asyncio.to_thread(send_email, config.email, subject, text)
        except Exception as exc:
            errors.append(f"Email: {exc}")
    return errors


def _minutes(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


class Notifier:
    """Ascultă evenimentele și trimite notificări după regulile din Setări."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventBus,
        send: Sender = deliver,
        check_every: float = CHECK_EVERY_S,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._send = send
        self._check_every = check_every
        # Dispozitive pentru care s-a trimis „oprit”; la revenire se trimite „a revenit”.
        self.offline_notified: set[int] = set()
        self.service_down_notified: set[int] = set()

    def prime(self) -> None:
        """La pornire nu se notifică din nou dispozitivele deja oprite."""
        with self._session_factory() as session:
            ids = session.scalars(
                select(Device.id).where(Device.notify, Device.status == "offline")
            )
            self.offline_notified = set(ids)

    async def run(self) -> None:
        await asyncio.to_thread(self.prime)
        loop = asyncio.get_running_loop()
        next_check = loop.time() + self._check_every
        queue = self._events.subscribe()
        try:
            while True:
                with contextlib.suppress(TimeoutError):
                    timeout = max(0.0, next_check - loop.time())
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                    await self._safely(self.handle(event))
                if loop.time() >= next_check:
                    await self._safely(self.check_offline())
                    next_check = loop.time() + self._check_every
        finally:
            self._events.unsubscribe(queue)

    async def _safely(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("Notificarea a eșuat")

    async def _notify(self, config: NotifyConfig, text: str) -> None:
        if not config.channels:
            return
        errors = await self._send(config, text)
        for error in errors:
            log.warning("Notificare netrimisă: %s", error)

    async def handle(self, event: Event) -> None:
        if event.kind == "device_discovered":
            await self._new_device(event.data["mac"])
        elif event.kind == "wol" and event.data.get("result") in ("timeout", "error"):
            await self._wol_failed(event.data["device_id"], event.data["result"])
        elif event.kind == "service_status" and event.data.get("service") in ("down", "ok"):
            await self._service(event.data["device_id"], event.data["service"], event.data)

    async def _new_device(self, mac: str) -> None:
        def load():
            with self._session_factory() as session:
                config = load_config(session)
                row = session.get(Discovered, mac)
                if row is None:
                    return config, None
                return config, (row.mac, row.ip, row.vendor, row.hostname)

        config, row = await asyncio.to_thread(load)
        if not config.new_device or row is None:
            return
        mac, ip, vendor, hostname = row
        details = ", ".join(x for x in (ip, vendor, hostname) if x)
        await self._notify(
            config, f"Dispozitiv nou în rețea: {mac}" + (f" ({details})" if details else "")
        )

    async def _wol_failed(self, device_id: int, result: str) -> None:
        def load():
            with self._session_factory() as session:
                device = session.get(Device, device_id)
                return load_config(session), device.name if device else None

        config, name = await asyncio.to_thread(load)
        if not config.wol_failed or name is None:
            return
        reason = (
            "nu a răspuns în timpul de așteptare" if result == "timeout" else "eroare la trimitere"
        )
        await self._notify(config, f"Pornirea {name} a eșuat: {reason}.")

    async def _service(self, device_id: int, service: str, data: dict) -> None:
        def load():
            with self._session_factory() as session:
                device = session.get(Device, device_id)
                if device is None or not device.notify:
                    return load_config(session), None
                return load_config(session), device.name

        config, name = await asyncio.to_thread(load)
        if name is None:
            return
        if service == "down":
            await self._notify(
                config, f"{name}: serviciu căzut (porturi închise: {data['closed']})."
            )
            self.service_down_notified.add(device_id)
        elif device_id in self.service_down_notified:
            self.service_down_notified.discard(device_id)
            await self._notify(config, f"{name}: serviciile răspund din nou.")

    async def check_offline(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)

        def load():
            with self._session_factory() as session:
                config = load_config(session)
                rows = session.execute(
                    select(Device.id, Device.name, Device.status, Device.status_changed_at).where(
                        Device.notify
                    )
                ).all()
                return config, rows

        config, rows = await asyncio.to_thread(load)
        threshold = timedelta(minutes=config.offline_min)
        for device_id, name, status, changed_at in rows:
            if status == "offline" and device_id not in self.offline_notified:
                if changed_at and now - changed_at >= threshold:
                    self.offline_notified.add(device_id)
                    await self._notify(config, f"{name} e oprit de {_minutes(now - changed_at)}.")
            elif status == "online" and device_id in self.offline_notified:
                self.offline_notified.discard(device_id)
                await self._notify(config, f"{name} a revenit online.")
        # Dispozitivele care nu mai au notificări (sau au fost șterse) ies din evidență.
        self.offline_notified &= {row[0] for row in rows}
