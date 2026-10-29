from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DeviceType, Room
from app.services.portcheck import format_ports, parse_ports


@dataclass
class DeviceForm:
    name: str = ""
    room_id: int | None = None
    type_id: int | None = None
    wol_enabled: bool = False
    notify: bool = False
    check_ports: str = ""
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
            notes=str(form.get("notes", "")).strip(),
        )

    def validate(self, db: Session) -> str | None:
        if not self.name:
            return "Numele e obligatoriu."
        if len(self.name) > 100:
            return "Numele poate avea cel mult 100 de caractere."
        if self.room_id is not None and db.get(Room, self.room_id) is None:
            return "Camera aleasă nu mai există."
        if self.type_id is not None and db.get(DeviceType, self.type_id) is None:
            return "Tipul ales nu mai există."
        if self.check_ports != "-":
            try:
                parse_ports(self.check_ports)
            except ValueError as exc:
                return f"{exc}. Exemplu: 3389 sau 22, 443; „-” = fără verificare."
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
        device.notes = self.notes or None


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def choices(db: Session) -> dict:
    return {
        "rooms": db.scalars(select(Room).order_by(Room.sort_order, Room.name)).all(),
        "types": db.scalars(
            select(DeviceType).order_by(DeviceType.sort_order, DeviceType.name)
        ).all(),
    }
