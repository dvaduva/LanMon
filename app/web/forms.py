from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DeviceType, Room
from app.services.portcheck import format_ports, parse_ports
from app.services.shutdown import OS_NAMES
from app.web.deps import t


@dataclass
class DeviceForm:
    name: str = ""
    room_id: int | None = None
    type_id: int | None = None
    wol_enabled: bool = False
    notify: bool = False
    check_ports: str = ""
    shutdown_enabled: bool = False
    ssh_user: str = ""
    os: str = ""
    forget_host_key: bool = False
    notes: str = ""

    @classmethod
    def parse(cls, form) -> "DeviceForm":
        return cls(
            name=str(form.get("name", "")).strip(),
            room_id=_int_or_none(form.get("room_id")),
            type_id=_int_or_none(form.get("type_id")),
            wol_enabled=form.get("wol_enabled") == "on",
            notify=form.get("notify") == "on",
            check_ports=str(form.get("check_ports", "")).strip(),
            shutdown_enabled=form.get("shutdown_enabled") == "on",
            ssh_user=str(form.get("ssh_user", "")).strip(),
            os=str(form.get("os", "")),
            forget_host_key=form.get("forget_host_key") == "on",
            notes=str(form.get("notes", "")).strip(),
        )

    def validate(self, request, db: Session) -> str | None:
        if not self.name:
            return t(request, "The name is required.")
        if len(self.name) > 100:
            return t(request, "The name can have at most 100 characters.")
        if self.room_id is not None and db.get(Room, self.room_id) is None:
            return t(request, "The chosen room no longer exists.")
        if self.type_id is not None and db.get(DeviceType, self.type_id) is None:
            return t(request, "The chosen type no longer exists.")
        if self.check_ports != "-":
            try:
                parse_ports(self.check_ports)
            except ValueError as exc:
                return t(
                    request,
                    "Invalid port: {port}. Example: 3389 or 22, 443; “-” = no check.",
                    port=exc,
                )
        if self.os and self.os not in OS_NAMES:
            return t(request, "Unknown operating system.")
        if self.shutdown_enabled and not (self.ssh_user and self.os):
            return t(request, "For remote shutdown, choose the SSH user and the operating system.")
        if len(self.ssh_user) > 100:
            return t(request, "The SSH user can have at most 100 characters.")
        return None

    def apply(self, device) -> None:
        device.name = self.name
        device.room_id = self.room_id
        device.type_id = self.type_id
        device.wol_enabled = self.wol_enabled
        device.notify = self.notify
        if self.check_ports == "-":
            device.check_ports = "-"
        else:
            device.check_ports = format_ports(parse_ports(self.check_ports))
        device.shutdown_enabled = self.shutdown_enabled
        device.ssh_user = self.ssh_user or None
        device.os = self.os or None
        if self.forget_host_key:
            device.ssh_host_key = None
        device.notes = self.notes or None


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def choices(db: Session) -> dict:
    return {
        "os_names": OS_NAMES,
        "rooms": db.scalars(select(Room).order_by(Room.sort_order, Room.name)).all(),
        "types": db.scalars(
            select(DeviceType).order_by(DeviceType.sort_order, DeviceType.name)
        ).all(),
    }
