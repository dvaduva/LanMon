from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Device, Discovered, User
from app.mac import is_random_mac, normalize_mac
from app.web.deps import admin_user, flash, get_db, redirect, t, templates
from app.web.forms import DeviceForm, choices

router = APIRouter(prefix="/unassigned")


def _visible(ignored: bool):
    saved = select(Device.mac)
    return select(Discovered).where(Discovered.ignored.is_(ignored), Discovered.mac.not_in(saved))


def unassigned_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(_visible(False).subquery())) or 0


def _get(db: Session, mac: str) -> Discovered | None:
    try:
        return db.get(Discovered, normalize_mac(mac))
    except ValueError:
        return None


def suggested_name(entry: Discovered) -> str:
    if entry.hostname:
        return entry.hostname.split(".")[0]
    return entry.vendor or entry.mac


@router.get("")
def unassigned(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    order = (Discovered.last_seen_at.desc(),)
    context = {
        "entries": db.scalars(_visible(False).order_by(*order)).all(),
        "ignored": db.scalars(_visible(True).order_by(*order)).all(),
        "is_random_mac": is_random_mac,
    }
    return templates.TemplateResponse(request, "unassigned.html", context)


@router.get("/{mac}/add")
def add_form(
    mac: str, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    entry = _get(db, mac)
    if entry is None:
        return redirect(request, "/unassigned")
    form = DeviceForm(name=suggested_name(entry))
    context = {"entry": entry, "form": form, **choices(db)}
    return templates.TemplateResponse(request, "device_add.html", context)


@router.post("/{mac}/add")
async def add(
    mac: str, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    entry = _get(db, mac)
    if entry is None:
        return redirect(request, "/unassigned")
    form = DeviceForm.parse(await request.form())
    error = form.validate(request, db)
    if error:
        context = {"entry": entry, "form": form, "error": error, **choices(db)}
        return templates.TemplateResponse(request, "device_add.html", context, status_code=400)

    device = Device(
        mac=entry.mac,
        last_ip=entry.ip,
        hostname=entry.hostname,
        vendor=entry.vendor,
        is_random_mac=is_random_mac(entry.mac),
        status="unknown",
    )
    form.apply(device)
    db.add(device)
    db.delete(entry)
    db.commit()
    flash(
        request,
        t(request, "“{name}” was added. Its status shows after the next ping.", name=device.name),
    )
    return redirect(request, "/unassigned")


@router.post("/{mac}/ignore")
def ignore(
    mac: str, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    return _set_ignored(request, db, mac, True)


@router.post("/{mac}/restore")
def restore(
    mac: str, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    return _set_ignored(request, db, mac, False)


def _set_ignored(request: Request, db: Session, mac: str, ignored: bool) -> Response:
    entry = _get(db, mac)
    if entry is not None:
        entry.ignored = ignored
        db.commit()
    return redirect(request, "/unassigned")
