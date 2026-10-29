from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ActionLog, Device, Room, User
from app.web.deps import (
    Forbidden,
    admin_user,
    current_user,
    flash,
    get_db,
    redirect,
    t,
    templates,
    tn,
)

router = APIRouter()

# How long a wake-up result ("Started" / "No response") stays visible.
RESULT_VISIBLE_FOR = timedelta(minutes=10)


def wol_states(request: Request, db: Session) -> dict[int, str]:
    """device_id → waking / woke / timeout / error, shown next to the device."""
    states: dict[int, str] = {}
    since = datetime.now(UTC) - RESULT_VISIBLE_FOR
    latest = (
        select(ActionLog.device_id, func.max(ActionLog.id).label("id"))
        .where(ActionLog.action == "wol", ActionLog.created_at >= since)
        .group_by(ActionLog.device_id)
        .subquery()
    )
    for entry in db.scalars(select(ActionLog).join(latest, ActionLog.id == latest.c.id)):
        if entry.result != "sent":
            states[entry.device_id] = entry.result
    for device_id in request.app.state.waker.pending:
        states[device_id] = "waking"
    return states


def _back(request: Request, default: str) -> str:
    """Back to the page the request came from (local paths only)."""
    referer = urlparse(request.headers.get("referer", ""))
    if referer.netloc and referer.netloc != request.url.netloc:
        return default
    path = referer.path
    return path if path.startswith("/") and not path.startswith("//") else default


@router.post("/devices/{device_id}/wake")
async def wake_device(
    device_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is None:
        return redirect(request, "/")
    if not user.can_see(device):
        raise Forbidden
    if not device.wol_enabled:
        flash(
            request,
            t(request, "“{name}” doesn't have Wake-on-LAN enabled.", name=device.name),
            "error",
        )
    elif await request.app.state.waker.wake([device.id], source="user"):
        flash(request, t(request, "Sent the wake-up packet to “{name}”.", name=device.name))
    elif device.id in request.app.state.waker.pending:
        flash(request, t(request, "“{name}” is already starting.", name=device.name))
    else:
        flash(
            request,
            t(request, "Couldn't send the packet to “{name}”. See the Log.", name=device.name),
            "error",
        )
    return redirect(request, _back(request, f"/devices/{device.id}"))


@router.post("/rooms/{room_id}/wake")
async def wake_room(
    room_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    room = db.get(Room, room_id)
    if room is None:
        return redirect(request, "/")
    if not user.is_admin and room not in user.rooms:
        raise Forbidden
    ids = db.scalars(
        select(Device.id).where(
            Device.room_id == room.id, Device.wol_enabled.is_(True), Device.status == "offline"
        )
    ).all()
    sent = await request.app.state.waker.wake(ids, source="room") if ids else []
    if sent:
        flash(
            request,
            tn(
                request,
                "Sent the wake-up packet to {n} device in “{room}”.",
                "Sent the wake-up packet to {n} devices in “{room}”.",
                len(sent),
                room=room.name,
            ),
        )
    else:
        message = t(request, "No offline device with Wake-on-LAN in “{room}”.", room=room.name)
        flash(request, message, "error")
    return redirect(request, "/")


@router.get("/log")
def action_log(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    entries = db.scalars(
        select(ActionLog)
        .options(selectinload(ActionLog.device))
        .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
        .limit(200)
    ).all()
    return templates.TemplateResponse(request, "log.html", {"entries": entries})
