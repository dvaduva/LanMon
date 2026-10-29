from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite drops the time zone: values are written in UTC and read back as UTC datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    icon: Mapped[str | None] = mapped_column(String(50))

    devices: Mapped[list["Device"]] = relationship(back_populates="room")


class DeviceType(Base):
    __tablename__ = "device_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    icon: Mapped[str | None] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # TCP ports checked by default on devices of this type, e.g. "3389" or "22, 443".
    check_ports: Mapped[str | None] = mapped_column(String(100))


class Device(Base):
    """A device saved by an admin; the natural key is the normalized MAC."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    type_id: Mapped[int | None] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"))
    last_ip: Mapped[str | None] = mapped_column(String(45))
    hostname: Mapped[str | None] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(255))
    wol_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="unknown")  # online/offline/unknown
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_random_mac: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # Notify when it stays offline longer than the threshold in Settings (and when it's back).
    notify: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Own ports (take precedence over the type's); "-" = no check.
    check_ports: Mapped[str | None] = mapped_column(String(100))
    service_status: Mapped[str | None] = mapped_column(String(10))  # ok / down / None
    closed_ports: Mapped[str | None] = mapped_column(String(100))
    # Remote shutdown over SSH; ssh_host_key = host key remembered on first connection.
    shutdown_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    ssh_user: Mapped[str | None] = mapped_column(String(100))
    os: Mapped[str | None] = mapped_column(String(10))  # windows / linux / macos
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    room: Mapped[Room | None] = relationship(back_populates="devices")
    type: Mapped[DeviceType | None] = relationship()

    @property
    def effective_ports(self) -> str | None:
        if self.check_ports == "-":
            return None
        return self.check_ports or (self.type.check_ports if self.type else None)


class Discovered(Base):
    """The "Unassigned" list: MACs seen by the scanner and not saved yet."""

    __tablename__ = "discovered"

    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    ip: Mapped[str | None] = mapped_column(String(45))
    hostname: Mapped[str | None] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10))
    ip: Mapped[str | None] = mapped_column(String(45))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ActionLog(Base):
    __tablename__ = "actions_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(10))  # wol/scan/shutdown
    source: Mapped[str] = mapped_column(String(10))  # user/room/schedule/api
    result: Mapped[str] = mapped_column(String(10))  # sent/woke/timeout/error/ok
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    device: Mapped[Device | None] = relationship()


class Schedule(Base):
    """A scheduled WOL wake-up for a device or a whole room."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    time: Mapped[str] = mapped_column(String(5))  # HH:MM, local time (LANMON_TZ)
    weekdays: Mapped[str] = mapped_column(String(7))  # digits 0–6, Monday = 0
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    device: Mapped[Device | None] = relationship()
    room: Mapped[Room | None] = relationship()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class ApiToken(Base):
    """Token for the REST API (Home Assistant); only its hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12))  # start of the token, so it can be recognized
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


user_rooms = Table(
    "user_rooms",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("room_id", ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True),
)

ADMIN, MEMBER = "admin", "member"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # admin: everything; member: sees and controls only devices in their allowed rooms.
    role: Mapped[str] = mapped_column(String(10), default=ADMIN, server_default=ADMIN)
    language: Mapped[str] = mapped_column(String(5), default="en", server_default="en")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    rooms: Mapped[list[Room]] = relationship(secondary=user_rooms, order_by=Room.sort_order)

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    def can_see(self, device: "Device") -> bool:
        if self.is_admin:
            return True
        return device.room_id is not None and device.room_id in {r.id for r in self.rooms}
