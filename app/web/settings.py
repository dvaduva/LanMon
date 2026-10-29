import asyncio
import ipaddress
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.db.backup import backup_database
from app.db.models import ApiToken, User
from app.db.settings_store import get_int, get_str, set_value
from app.i18n import LANGUAGES, N_
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.services.notifier import load_config
from app.web.deps import (
    admin_user,
    current_user,
    flash,
    get_db,
    login_session,
    redirect,
    t,
    templates,
)

router = APIRouter()


@dataclass(frozen=True)
class IntSetting:
    key: str
    label: str
    default: int
    minimum: int
    maximum: int
    unit: str = N_("seconds")


INT_SETTINGS = [
    IntSetting("ping_interval_s", N_("Ping interval"), 30, 10, 600),
    IntSetting("scan_interval_s", N_("ARP scan interval"), 300, 60, 86400),
    IntSetting("wol_timeout_s", N_("WOL check timeout"), 180, 30, 900),
]


NOTIFY_FIELDS = [
    "notify_telegram_chat",
    "notify_email_host",
    "notify_email_port",
    "notify_email_security",
    "notify_email_user",
    "notify_email_from",
    "notify_email_to",
    "notify_offline_min",
]
# Secret fields: never shown; empty on save = keep the old value.
NOTIFY_SECRETS = ["notify_telegram_token", "notify_email_password"]
EMAIL_SECURITY = {"starttls": "STARTTLS (587)", "ssl": "SSL/TLS (465)", "none": N_("No encryption")}


def _context(request: Request, db: Session, user: User, **extra) -> dict:
    values = {s.key: get_int(db, s.key, s.default) for s in INT_SETTINGS}
    notify = {key: get_str(db, key) or "" for key in NOTIFY_FIELDS}
    notify.update({key: bool(get_str(db, key)) for key in NOTIFY_SECRETS})
    notify["notify_new_device"] = get_str(db, "notify_new_device") == "1"
    notify["notify_wol_failed"] = get_str(db, "notify_wol_failed") == "1"
    return {
        "notify": notify,
        "email_security": EMAIL_SECURITY,
        "notify_channels": [t(request, c) for c in load_config(db).channels],
        "user": user,
        "int_settings": INT_SETTINGS,
        "values": values,
        "subnet": get_str(db, "subnet") or "",
        "env_subnet": request.app.state.settings.subnet,
        "api_tokens": db.scalars(select(ApiToken).order_by(ApiToken.created_at)).all()
        if user.is_admin
        else [],
        "ssh_public_key": request.app.state.shutdowner.public_key() if user.is_admin else None,
        **extra,
    }


async def render_settings(
    request: Request, db: Session, user: User, status_code: int = 200, **extra
) -> Response:
    try:
        info = await asyncio.to_thread(request.app.state.network)
        network = f"{info.interface} · {info.address} (broadcast {info.broadcast})"
    except Exception as exc:
        network = t(request, "not detected: {error}", error=exc)
    context = _context(request, db, user, network=network, **extra)
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)


@router.get("/account")
def account_redirect(request: Request) -> Response:
    return redirect(request, "/settings")


@router.get("/settings")
async def settings_page(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    return await render_settings(request, db, user)


@router.post("/settings")
async def save_settings(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    errors = []
    parsed: dict[str, int] = {}
    for s in INT_SETTINGS:
        try:
            value = int(str(form.get(s.key, "")))
        except ValueError:
            errors.append(t(request, "{label}: enter a whole number.", label=t(request, s.label)))
            continue
        if not s.minimum <= value <= s.maximum:
            errors.append(
                t(
                    request,
                    "{label}: between {min} and {max} {unit}.",
                    label=t(request, s.label),
                    min=s.minimum,
                    max=s.maximum,
                    unit=t(request, s.unit),
                )
            )
        parsed[s.key] = value

    subnet = str(form.get("subnet", "")).strip()
    if subnet:
        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError:
            errors.append(t(request, "Invalid subnet. Example: 192.168.1.0/24."))
        else:
            if network.prefixlen < 16:
                errors.append(t(request, "The subnet is too large to scan (at least /16)."))
            subnet = str(network)

    if errors:
        return await render_settings(request, db, user, 400, error=" ".join(errors))
    for key, value in parsed.items():
        set_value(db, key, str(value))
    set_value(db, "subnet", subnet or None)
    db.commit()
    flash(request, t(request, "Settings saved. New intervals apply from the next cycle."))
    return redirect(request, "/settings")


@router.post("/settings/notifications")
async def save_notifications(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    values = {key: str(form.get(key, "")).strip() for key in NOTIFY_FIELDS + NOTIFY_SECRETS}
    errors = []
    if values["notify_email_port"] and (
        not values["notify_email_port"].isdigit()
        or not 0 < int(values["notify_email_port"]) < 65536
    ):
        errors.append(t(request, "The SMTP port must be a number between 1 and 65535."))
    if values["notify_email_security"] not in EMAIL_SECURITY:
        values["notify_email_security"] = "starttls"
    offline = values["notify_offline_min"] or "10"
    if not offline.isdigit() or not 1 <= int(offline) <= 1440:
        errors.append(t(request, "The “offline” threshold must be between 1 and 1440 minutes."))
    if values["notify_telegram_token"] and not values["notify_telegram_chat"]:
        errors.append(
            t(
                request,
                "For Telegram, also fill in the Chat ID: send your bot a message, then open "
                "https://api.telegram.org/bot<token>/getUpdates and copy the number from "
                '"chat":{"id":…}.',
            )
        )
    if values["notify_email_host"] and "@" not in values["notify_email_to"]:
        errors.append(t(request, "For email, fill in the recipient's address."))
    if errors:
        return await render_settings(request, db, user, 400, error=" ".join(errors))

    for key in NOTIFY_FIELDS:
        set_value(db, key, values[key] or None)
    set_value(db, "notify_offline_min", offline)
    for key in NOTIFY_SECRETS:
        if values[key]:
            set_value(db, key, values[key])
    # Without a chat id / SMTP server the channel is off and its secret is cleared.
    if not values["notify_telegram_chat"]:
        set_value(db, "notify_telegram_token", None)
    if not values["notify_email_host"]:
        set_value(db, "notify_email_password", None)
    set_value(db, "notify_new_device", "1" if form.get("notify_new_device") == "on" else "0")
    set_value(db, "notify_wol_failed", "1" if form.get("notify_wol_failed") == "on" else "0")
    db.commit()
    flash(request, t(request, "Notification settings saved."))
    return redirect(request, "/settings")


@router.post("/settings/notifications/test")
async def test_notifications(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    config = load_config(db)
    if not config.channels:
        flash(request, t(request, "No channel is configured (Telegram or email)."), "error")
        return redirect(request, "/settings")
    message = t(request, "Test message from LanMon.")
    errors = await request.app.state.notify_send(config, message)
    if errors:
        text = t(request, "The test message couldn't be sent.") + " " + " ".join(errors)
        flash(request, text, "error")
    else:
        channels = t(request, " and ").join(t(request, c) for c in config.channels)
        flash(request, t(request, "Test message sent on {channels}.", channels=channels))
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
        error = t(request, "The current password is wrong.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = t(request, "The password must have at least {n} characters.", n=MIN_PASSWORD_LENGTH)
    elif password != confirm:
        error = t(request, "The passwords don't match.")
    else:
        error = None
    if error:
        return await render_settings(request, db, user, 400, error=error)
    user.password_hash = hash_password(password)
    db.commit()
    login_session(request, user)
    flash(request, t(request, "Password changed. Your other sessions were signed out."))
    return redirect(request, "/settings")


@router.post("/settings/language")
async def change_language(
    request: Request,
    language: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    if language in LANGUAGES:
        user.language = language
        db.commit()
        request.session["lang"] = language
        request.state.lang = language
        flash(request, t(request, "Language saved."))
    return redirect(request, "/settings")


@router.get("/settings/backup")
async def download_backup(request: Request, user: User = Depends(admin_user)) -> Response:
    settings = request.app.state.settings
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    tmp = settings.data_dir / "backups" / f".download-{stamp}.db"
    await asyncio.to_thread(backup_database, settings.db_path, tmp)
    return FileResponse(
        tmp,
        media_type="application/vnd.sqlite3",
        filename=f"lanmon-{stamp}.db",
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )
