import asyncio
import ipaddress
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db.backup import backup_database
from app.db.models import User
from app.db.settings_store import get_int, get_str, set_value
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.web.deps import current_user, flash, get_db, login_session, redirect, templates

router = APIRouter()


@dataclass(frozen=True)
class IntSetting:
    key: str
    label: str
    default: int
    minimum: int
    maximum: int
    unit: str = "secunde"


INT_SETTINGS = [
    IntSetting("ping_interval_s", "Interval ping", 30, 10, 600),
    IntSetting("scan_interval_s", "Interval scanare ARP", 300, 60, 86400),
    IntSetting("wol_timeout_s", "Timeout verificare WOL", 180, 30, 900),
]


def _context(request: Request, db: Session, user: User, **extra) -> dict:
    values = {s.key: get_int(db, s.key, s.default) for s in INT_SETTINGS}
    return {
        "user": user,
        "int_settings": INT_SETTINGS,
        "values": values,
        "subnet": get_str(db, "subnet") or "",
        "env_subnet": request.app.state.settings.subnet,
        **extra,
    }


async def _render(request: Request, db: Session, user: User, status_code: int = 200, **extra):
    try:
        info = await asyncio.to_thread(request.app.state.network)
        network = f"{info.interface} · {info.address} (broadcast {info.broadcast})"
    except Exception as exc:
        network = f"nedetectată: {exc}"
    context = _context(request, db, user, network=network, **extra)
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)


@router.get("/account")
def account_redirect(request: Request) -> Response:
    return redirect(request, "/settings")


@router.get("/settings")
async def settings_page(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    return await _render(request, db, user)


@router.post("/settings")
async def save_settings(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    errors = []
    parsed: dict[str, int] = {}
    for s in INT_SETTINGS:
        try:
            value = int(str(form.get(s.key, "")))
        except ValueError:
            errors.append(f"{s.label}: introdu un număr întreg.")
            continue
        if not s.minimum <= value <= s.maximum:
            errors.append(f"{s.label}: între {s.minimum} și {s.maximum} {s.unit}.")
        parsed[s.key] = value

    subnet = str(form.get("subnet", "")).strip()
    if subnet:
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError:
            errors.append("Subnet invalid. Exemplu: 192.168.1.0/24.")
        else:
            if network.prefixlen < 16:
                errors.append("Subnetul e prea mare pentru scanare (minim /16).")
            subnet = str(network)

    if errors:
        return await _render(request, db, user, 400, error=" ".join(errors))
    for key, value in parsed.items():
        set_value(db, key, str(value))
    set_value(db, "subnet", subnet or None)
    db.commit()
    flash(request, "Setările au fost salvate. Intervalele noi se aplică de la următorul ciclu.")
    return redirect(request, "/settings")


@router.post("/settings/password")
async def change_password(
    request: Request,
    current: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not verify_password(user.password_hash, current):
        error = "Parola actuală e greșită."
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = f"Parola trebuie să aibă cel puțin {MIN_PASSWORD_LENGTH} caractere."
    elif password != confirm:
        error = "Parolele nu coincid."
    else:
        error = None
    if error:
        return await _render(request, db, user, 400, error=error)
    user.password_hash = hash_password(password)
    db.commit()
    login_session(request, user)
    flash(request, "Parola a fost schimbată. Celelalte sesiuni au fost deconectate.")
    return redirect(request, "/settings")


@router.get("/settings/backup")
async def download_backup(request: Request, user: User = Depends(current_user)) -> Response:
    settings = request.app.state.settings
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    tmp = settings.data_dir / "backups" / f".descarcare-{stamp}.db"
    await asyncio.to_thread(backup_database, settings.db_path, tmp)
    return FileResponse(
        tmp,
        media_type="application/vnd.sqlite3",
        filename=f"lanmon-{stamp}.db",
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )
