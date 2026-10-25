from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DeviceType, Room


@dataclass
class DeviceForm:
    name: str = ""
    room_id: int | None = None
    type_id: int | None = None
    wol_enabled: bool = False
    notes: str = ""

    @classmethod
    def parse(cls, form) -> "DeviceForm":
        return cls(
            name=str(form.get("name", "")).strip(),
            room_id=_int_or_none(form.get("room_id")),
            type_id=_int_or_none(form.get("type_id")),
            wol_enabled=form.get("wol_enabled") == "on",
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
        return None

    def apply(self, device) -> None:
        device.name = self.name
        device.room_id = self.room_id
        device.type_id = self.type_id
        device.wol_enabled = self.wol_enabled
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
