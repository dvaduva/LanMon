from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Device, Room, User
from app.web.deps import current_user, flash, get_db, redirect, templates
from app.web.unassigned import unassigned_count
from app.web.wol import wol_states

router = APIRouter()


@dataclass
class RoomGroup:
    room: Room | None
    devices: list[Device] = field(default_factory=list)

    @property
    def online(self) -> int:
        return sum(d.status == "online" for d in self.devices)

    @property
    def wakeable(self) -> int:
        return sum(d.wol_enabled and d.status == "offline" for d in self.devices)


def group_by_room(db: Session) -> list[RoomGroup]:
    rooms = db.scalars(select(Room).order_by(Room.sort_order, Room.name)).all()
    devices = db.scalars(
        select(Device).options(selectinload(Device.type)).order_by(Device.name)
    ).all()
    groups = {room.id: RoomGroup(room) for room in rooms}
    no_room = RoomGroup(None)
    for device in devices:
        groups.get(device.room_id, no_room).devices.append(device)
    result = [g for g in groups.values() if g.devices]
    if no_room.devices:
        result.append(no_room)
    return result


@router.get("/")
def dashboard(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    groups = group_by_room(db)
    context = {
        "groups": groups,
        "online": sum(g.online for g in groups),
        "total": sum(len(g.devices) for g in groups),
        "unassigned": unassigned_count(db),
        "last_scan": request.app.state.scanner.last_result,
        "wol": wol_states(request, db),
    }
    # Reîmprospătarea periodică (HTMX) cere doar lista; SSE vine în Faza 4.
    template = "_dashboard_groups.html" if request.headers.get("HX-Request") else "dashboard.html"
    return templates.TemplateResponse(request, template, context)


@router.post("/scan")
def scan_now(request: Request, user: User = Depends(current_user)) -> Response:
    request.app.state.scanner.request_scan()
    flash(request, "Scanarea a pornit. Dispozitivele noi apar în „Neasignate” în câteva secunde.")
    return redirect(request, request.headers.get("HX-Current-URL") or "/")
