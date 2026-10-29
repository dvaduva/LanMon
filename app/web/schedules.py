"""WOL schedules: list, add, enable/disable, delete."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Device, Room, Schedule, User
from app.i18n import WEEKDAYS
from app.web.deps import admin_user, flash, get_db, redirect, t, templates

router = APIRouter(prefix="/schedules")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def describe_days(request: Request, weekdays: str) -> str:
    if weekdays == "0123456":
        return t(request, "daily")
    if weekdays == "01234":
        return t(request, "Monday–Friday")
    if weekdays == "56":
        return t(request, "weekend")
    return ", ".join(t(request, WEEKDAYS[int(d)]) for d in weekdays)


def _render(request: Request, db: Session, status_code: int = 200, **extra) -> Response:
    schedules = db.scalars(select(Schedule).order_by(Schedule.time, Schedule.id)).all()
    devices = db.scalars(select(Device).where(Device.wol_enabled).order_by(Device.name)).all()
    rooms = db.scalars(select(Room).order_by(Room.sort_order, Room.name)).all()
    context = {
        "schedules": schedules,
        "devices": devices,
        "rooms": rooms,
        "weekdays": WEEKDAYS,
        "describe_days": lambda weekdays: describe_days(request, weekdays),
        "tz": request.app.state.settings.tz,
        "values": {"target": "", "time": "08:00", "days": "01234"},
        **extra,
    }
    return templates.TemplateResponse(request, "schedules.html", context, status_code=status_code)


@router.get("")
def index(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    return _render(request, db)


@router.post("")
async def create(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    target = str(form.get("target", ""))
    time = str(form.get("time", "")).strip()
    days = "".join(sorted({d for d in form.getlist("days") if d in "0123456" and len(d) == 1}))
    schedule = Schedule(time=time, weekdays=days, enabled=True)
    error = None
    kind, _, raw_id = target.partition(":")
    if kind == "device" and raw_id.isdigit() and db.get(Device, int(raw_id)) is not None:
        schedule.device_id = int(raw_id)
    elif kind == "room" and raw_id.isdigit() and db.get(Room, int(raw_id)) is not None:
        schedule.room_id = int(raw_id)
    else:
        error = t(request, "Choose a device or a room.")
    if not error and not TIME_RE.match(time):
        error = t(request, "The time must be in the HH:MM format.")
    if not error and not days:
        error = t(request, "Choose at least one day.")
    if error:
        values = {"target": target, "time": time, "days": days}
        return _render(request, db, 400, error=error, values=values)
    db.add(schedule)
    db.commit()
    flash(request, t(request, "Schedule added."))
    return redirect(request, "/schedules")


@router.post("/{schedule_id}/toggle")
def toggle(
    schedule_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    schedule = db.get(Schedule, schedule_id)
    if schedule is not None:
        schedule.enabled = not schedule.enabled
        db.commit()
    return redirect(request, "/schedules")


@router.post("/{schedule_id}/delete")
def delete(
    schedule_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    schedule = db.get(Schedule, schedule_id)
    if schedule is not None:
        db.delete(schedule)
        db.commit()
        flash(request, t(request, "Schedule deleted."))
    return redirect(request, "/schedules")
