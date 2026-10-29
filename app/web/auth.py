import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.i18n import LANGUAGES
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.web.deps import (
    flash,
    get_db,
    has_users,
    language,
    login_session,
    redirect,
    t,
    templates,
)

router = APIRouter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _password_problem(request: Request, password: str, confirm: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return t(request, "The password must have at least {n} characters.", n=MIN_PASSWORD_LENGTH)
    if password != confirm:
        return t(request, "The passwords don't match.")
    return None


@router.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)) -> Response:
    if has_users(db):
        return redirect(request, "/login")
    return templates.TemplateResponse(request, "setup.html", {"username": "admin"})


@router.post("/setup")
def setup(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    if has_users(db):
        return redirect(request, "/login")
    username = username.strip()
    if not username:
        error = t(request, "Choose a username.")
    else:
        error = _password_problem(request, password, confirm)
    if error:
        context = {"username": username, "error": error}
        return templates.TemplateResponse(request, "setup.html", context, status_code=400)
    user = User(
        username=username, password_hash=hash_password(password), language=language(request)
    )
    db.add(user)
    db.commit()
    login_session(request, user)
    flash(request, t(request, "The administrator account was created."))
    return redirect(request, "/")


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)) -> Response:
    if not has_users(db):
        return redirect(request, "/setup")
    return templates.TemplateResponse(request, "login.html", {"username": ""})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    limiter = request.app.state.login_limiter
    key = _client_key(request)
    locked = limiter.locked_for(key)
    if locked:
        minutes = math.ceil(locked / 60)
        error = t(request, "Too many failed attempts. Try again in {n} min.", n=minutes)
        context = {"username": username, "error": error}
        return templates.TemplateResponse(request, "login.html", context, status_code=429)

    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(user.password_hash, password):
        limiter.failure(key)
        context = {"username": username, "error": t(request, "Wrong username or password.")}
        return templates.TemplateResponse(request, "login.html", context, status_code=401)

    limiter.success(key)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    login_session(request, user)
    return redirect(request, "/")


@router.post("/logout")
def logout(request: Request) -> Response:
    lang = request.session.get("lang")
    request.session.clear()
    if lang:
        request.session["lang"] = lang  # the login page stays in the same language
    return redirect(request, "/login")


@router.get("/language/{code}")
def choose_language(code: str, request: Request) -> Response:
    """Language switch on the sign-in and setup pages (signed-in users change it in Settings)."""
    if code in LANGUAGES:
        request.session["lang"] = code
    target = "/setup" if request.headers.get("referer", "").endswith("/setup") else "/login"
    return redirect(request, target)
