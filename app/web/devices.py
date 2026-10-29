from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActionLog, Device, StatusEvent, User
from app.services.shutdown import describe
from app.services.uptime import device_uptime
from app.web.deps import (
    Forbidden,
    admin_user,
    current_user,
    flash,
    get_db,
    language,
    redirect,
    t,
    templates,
)
from app.web.forms import DeviceForm, choices

router = APIRouter(prefix="/devices")


def _context(request: Request, db: Session, device: Device, form: DeviceForm, **extra) -> dict:
    events = db.scalars(
        select(StatusEvent)
        .where(StatusEvent.device_id == device.id)
        .order_by(StatusEvent.at.desc(), StatusEvent.id.desc())
        .limit(20)
    ).all()
    last_wol = db.scalar(
        select(ActionLog)
        .where(ActionLog.device_id == device.id, ActionLog.action == "wol")
        .order_by(ActionLog.id.desc())
        .limit(1)
    )
    last_shutdown = db.scalar(
        select(ActionLog)
        .where(ActionLog.device_id == device.id, ActionLog.action == "shutdown")
        .order_by(ActionLog.id.desc())
        .limit(1)
    )
    return {
        "device": device,
        "last_shutdown": last_shutdown,
        "form": form,
        "events": events,
        "last_wol": last_wol,
        "waking": device.id in request.app.state.waker.pending,
        "uptime": device_uptime(
            db, device.id, datetime.now(UTC), 30, ZoneInfo(request.app.state.settings.tz)
        ),
        **choices(db),
        **extra,
    }


@router.get("/{device_id}")
def detail(
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
    form = DeviceForm(
        name=device.name,
        room_id=device.room_id,
        type_id=device.type_id,
        wol_enabled=device.wol_enabled,
        notify=device.notify,
        check_ports=device.check_ports or "",
        shutdown_enabled=device.shutdown_enabled,
        ssh_user=device.ssh_user or "",
        os=device.os or "",
        notes=device.notes or "",
    )
    return templates.TemplateResponse(request, "device.html", _context(request, db, device, form))


@router.post("/{device_id}")
async def update(
    device_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is None:
        return redirect(request, "/")
    form = DeviceForm.parse(await request.form())
    error = form.validate(request, db)
    if error:
        context = _context(request, db, device, form, error=error)
        return templates.TemplateResponse(request, "device.html", context, status_code=400)
    form.apply(device)
    db.commit()
    flash(request, t(request, "Changes saved."))
    return redirect(request, f"/devices/{device.id}")


@router.post("/{device_id}/shutdown")
async def shutdown(
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
    db.close()
    result = await request.app.state.shutdowner.shutdown(device_id, source="user")
    flash(request, describe(language(request), result), "ok" if result.ok else "error")
    return redirect(request, f"/devices/{device_id}")


@router.post("/{device_id}/delete")
def delete(
    device_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is not None:
        db.delete(device)
        db.commit()
        message = t(
            request,
            "“{name}” was deleted. It shows up again in “Unassigned” on the next scan.",
            name=device.name,
        )
        flash(request, message)
    return redirect(request, "/")
