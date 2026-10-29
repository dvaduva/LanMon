import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ApiToken, Device, User
from app.web.deps import admin_user, flash, get_db, redirect
from app.web.settings import render_settings

router = APIRouter()

TOKEN_PREFIX = "lm_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(db: Session, name: str) -> str:
    """Întoarce tokenul în clar; în baza de date rămâne doar hash-ul."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(ApiToken(name=name, token_hash=hash_token(token), prefix=token[:10]))
    return token


def api_token(request: Request, db: Session = Depends(get_db)) -> ApiToken:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    row = None
    if scheme.lower() == "bearer" and token:
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token.strip())))
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Token lipsă sau invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    row.last_used_at = datetime.now(UTC)
    db.commit()
    return row


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def device_json(device: Device) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "room": device.room.name if device.room else None,
        "type": device.type.name if device.type else None,
        "mac": device.mac,
        "ip": device.last_ip,
        "status": device.status,
        "online": device.status == "online",
        "status_changed_at": _iso(device.status_changed_at),
        "last_seen_at": _iso(device.last_seen_at),
        "service_status": device.service_status if device.status == "online" else None,
        "closed_ports": device.closed_ports if device.service_status == "down" else None,
        "can_wake": device.wol_enabled,
        "can_shutdown": device.shutdown_enabled,
    }


def _device(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispozitivul nu există.")
    return device


@router.get("/api/v1/devices")
def list_devices(token: ApiToken = Depends(api_token), db: Session = Depends(get_db)) -> list:
    devices = db.scalars(
        select(Device)
        .options(selectinload(Device.room), selectinload(Device.type))
        .order_by(Device.name)
    ).all()
    return [device_json(d) for d in devices]


@router.get("/api/v1/devices/{device_id}")
def get_device(
    device_id: int, token: ApiToken = Depends(api_token), db: Session = Depends(get_db)
) -> dict:
    return device_json(_device(db, device_id))


@router.post("/api/v1/devices/{device_id}/wake")
async def wake(
    device_id: int,
    request: Request,
    token: ApiToken = Depends(api_token),
    db: Session = Depends(get_db),
) -> Response:
    device = _device(db, device_id)
    waker = request.app.state.waker
    if not device.wol_enabled:
        return _result(False, f"„{device.name}” nu are Wake-on-LAN activat.", 409)
    if await waker.wake([device.id], source="api"):
        return _result(True, f"Am trimis pachetul de pornire către „{device.name}”.")
    if device.id in waker.pending:
        return _result(True, f"„{device.name}” se pornește deja.")
    return _result(False, f"Nu am putut trimite pachetul către „{device.name}”.", 502)


@router.post("/api/v1/devices/{device_id}/shutdown")
async def shutdown(
    device_id: int,
    request: Request,
    token: ApiToken = Depends(api_token),
    db: Session = Depends(get_db),
) -> Response:
    device = _device(db, device_id)
    if not device.shutdown_enabled:
        return _result(False, f"„{device.name}” nu are oprirea de la distanță activată.", 409)
    db.close()
    ok, message = await request.app.state.shutdowner.shutdown(device_id, source="api")
    return _result(ok, message, 200 if ok else 502)


def _result(ok: bool, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"ok": ok, "message": message}, status_code=status_code)


@router.post("/settings/tokens")
async def add_token(
    request: Request,
    name: str = Form(""),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    name = name.strip()[:100] or "Home Assistant"
    token = create_token(db, name)
    db.commit()
    # Tokenul se arată o singură dată, direct în pagină (nu prin redirect/cookie).
    return await render_settings(request, db, user, new_token=token, new_token_name=name)


@router.post("/settings/tokens/{token_id}/delete")
def delete_token(
    token_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(ApiToken, token_id)
    if row is not None:
        db.delete(row)
        db.commit()
        flash(request, f"Tokenul „{row.name}” a fost revocat.")
    return redirect(request, "/settings")
