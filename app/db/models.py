from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite pierde fusul orar: se scrie în UTC și se citește înapoi ca datetime UTC."""

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
    # Porturi TCP verificate implicit la dispozitivele de acest tip, ex. „3389” sau „22, 443”.
    check_ports: Mapped[str | None] = mapped_column(String(100))


class Device(Base):
    """Dispozitiv salvat de admin; cheia naturală e MAC-ul normalizat."""

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
    # Notificare când stă oprit mai mult de pragul din Setări (și când revine).
    notify: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Porturi proprii (au întâietate față de cele ale tipului); „-” = fără verificare.
    check_ports: Mapped[str | None] = mapped_column(String(100))
    service_status: Mapped[str | None] = mapped_column(String(10))  # ok / down / None
    closed_ports: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    room: Mapped[Room | None] = relationship(back_populates="devices")
    type: Mapped[DeviceType | None] = relationship()

    @property
    def effective_ports(self) -> str | None:
        if self.check_ports == "-":
            return None
        return self.check_ports or (self.type.check_ports if self.type else None)


class Discovered(Base):
    """Lista „Neasignate”: MAC-uri văzute la scanare și nesalvate încă."""

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
    action: Mapped[str] = mapped_column(String(10))  # wol/scan
    source: Mapped[str] = mapped_column(String(10))  # user/room
    result: Mapped[str] = mapped_column(String(10))  # sent/woke/timeout/error
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    device: Mapped[Device | None] = relationship()


class Schedule(Base):
    """Pornire WOL programată pentru un dispozitiv sau pentru toată camera."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    time: Mapped[str] = mapped_column(String(5))  # HH:MM, ora locală (LANMON_TZ)
    weekdays: Mapped[str] = mapped_column(String(7))  # cifre 0–6, luni = 0
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    device: Mapped[Device | None] = relationship()
    room: Mapped[Room | None] = relationship()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
