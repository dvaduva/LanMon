"""Administrarea utilizatorilor: roluri și camerele permise membrilor."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ADMIN, MEMBER, Room, User
from app.security import MIN_PASSWORD_LENGTH, hash_password
from app.web.deps import admin_user, flash, get_db, redirect, templates

router = APIRouter(prefix="/users")

ROLES = {ADMIN: "Administrator", MEMBER: "Membru"}


def _rooms(db: Session) -> list[Room]:
    return list(db.scalars(select(Room).order_by(Room.sort_order, Room.name)))


def _render(request: Request, db: Session, template: str, status_code: int = 200, **extra):
    context = {"roles": ROLES, "all_rooms": _rooms(db), **extra}
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def _admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == ADMIN)) or 0


def _apply(db: Session, user: User, form, creating: bool) -> str | None:
    username = str(form.get("username", "")).strip()
    role = str(form.get("role", MEMBER))
    password = str(form.get("password", ""))
    if not username:
        return "Numele de utilizator e obligatoriu."
    clash = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if clash is not None and clash is not user:
        return f"Există deja utilizatorul „{clash.username}”."
    if role not in ROLES:
        return "Rol necunoscut."
    if (creating or password) and len(password) < MIN_PASSWORD_LENGTH:
        return f"Parola trebuie să aibă cel puțin {MIN_PASSWORD_LENGTH} caractere."
    if not creating and user.role == ADMIN and role != ADMIN and _admins(db) <= 1:
        return "Trebuie să rămână cel puțin un administrator."
    room_ids = {int(r) for r in form.getlist("rooms") if str(r).isdigit()}
    user.username = username
    user.role = role
    user.rooms = [r for r in _rooms(db) if r.id in room_ids] if role == MEMBER else []
    if password:
        user.password_hash = hash_password(password)
    return None


@router.get("")
def index(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    users = db.scalars(select(User).order_by(User.username)).all()
    return _render(request, db, "users.html", users=users)


@router.get("/new")
def new(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    return _render(request, db, "user_form.html", item=None, values=User(role=MEMBER, username=""))


@router.post("/new")
async def create(
    request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    form = await request.form()
    item = User(password_hash="")
    error = _apply(db, item, form, creating=True)
    if error:
        db.expunge_all()
        return _render(request, db, "user_form.html", 400, item=None, values=item, error=error)
    db.add(item)
    db.commit()
    flash(request, f"Utilizatorul „{item.username}” a fost creat.")
    return redirect(request, "/users")


@router.get("/{user_id}")
def edit(
    user_id: int, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    item = db.get(User, user_id)
    if item is None:
        return redirect(request, "/users")
    return _render(request, db, "user_form.html", item=item, values=item)


@router.post("/{user_id}")
async def update(
    user_id: int, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    item = db.get(User, user_id)
    if item is None:
        return redirect(request, "/users")
    form = await request.form()
    error = _apply(db, item, form, creating=False)
    if error:
        db.rollback()
        return _render(request, db, "user_form.html", 400, item=item, values=item, error=error)
    db.commit()
    flash(request, "Modificările au fost salvate.")
    return redirect(request, "/users")


@router.post("/{user_id}/delete")
def delete(
    user_id: int, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
) -> Response:
    item = db.get(User, user_id)
    if item is None:
        return redirect(request, "/users")
    if item.id == user.id:
        flash(request, "Nu îți poți șterge propriul cont.", "error")
    elif item.role == ADMIN and _admins(db) <= 1:
        flash(request, "Trebuie să rămână cel puțin un administrator.", "error")
    else:
        db.delete(item)
        db.commit()
        flash(request, f"Utilizatorul „{item.username}” a fost șters.")
    return redirect(request, "/users")
