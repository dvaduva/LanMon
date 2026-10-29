from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ActionLog, Device, StatusEvent, User
from app.web.deps import current_user, flash, get_db, redirect, templates
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
    return {
        "device": device,
        "form": form,
        "events": events,
        "last_wol": last_wol,
        "waking": device.id in request.app.state.waker.pending,
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
    form = DeviceForm(
        name=device.name,
        room_id=device.room_id,
        type_id=device.type_id,
        wol_enabled=device.wol_enabled,
        notify=device.notify,
        check_ports=device.check_ports or "",
        notes=device.notes or "",
    )
    return templates.TemplateResponse(request, "device.html", _context(request, db, device, form))


@router.post("/{device_id}")
async def update(
    device_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is None:
        return redirect(request, "/")
    form = DeviceForm.parse(await request.form())
    error = form.validate(db)
    if error:
        context = _context(request, db, device, form, error=error)
        return templates.TemplateResponse(request, "device.html", context, status_code=400)
    form.apply(device)
    db.commit()
    flash(request, "Modificările au fost salvate.")
    return redirect(request, f"/devices/{device.id}")


@router.post("/{device_id}/delete")
def delete(
    device_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is not None:
        db.delete(device)
        db.commit()
        flash(request, f"„{device.name}” a fost șters. Va reapărea în „Neasignate” la scanare.")
    return redirect(request, "/")
