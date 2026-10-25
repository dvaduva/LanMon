"""Administrarea camerelor și a tipurilor: același formular (nume, iconiță, ordine)."""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Device, DeviceType, Room, User
from app.icons import ROOM_ICONS, TYPE_ICONS
from app.web.deps import current_user, flash, get_db, redirect, templates


@dataclass(frozen=True)
class Catalog:
    model: type
    device_column: object
    path: str
    title: str
    other: str  # „altă cameră” / „alt tip”
    icons: list[str]


CATALOGS = [
    Catalog(Room, Device.room_id, "/rooms", "Camere", "altă cameră", ROOM_ICONS),
    Catalog(DeviceType, Device.type_id, "/types", "Tipuri", "alt tip", TYPE_ICONS),
]


def make_router(c: Catalog) -> APIRouter:
    router = APIRouter(prefix=c.path)

    def usage(db: Session) -> dict[int, int]:
        rows = db.execute(
            select(c.device_column, func.count())
            .where(c.device_column.is_not(None))
            .group_by(c.device_column)
        )
        return dict(rows.all())

    def render(request: Request, template: str, status_code: int = 200, **context) -> Response:
        return templates.TemplateResponse(
            request, template, {"catalog": c, **context}, status_code=status_code
        )

    def validate(db: Session, name: str, item_id: int | None) -> str | None:
        if not name:
            return "Numele e obligatoriu."
        if len(name) > 100:
            return "Numele poate avea cel mult 100 de caractere."
        clash = db.scalar(select(c.model).where(func.lower(c.model.name) == name.lower()))
        if clash is not None and clash.id != item_id:
            return f"Există deja „{clash.name}”."
        return None

    @router.get("")
    def index(
        request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
    ) -> Response:
        items = db.scalars(select(c.model).order_by(c.model.sort_order, c.model.name)).all()
        return render(request, "catalog_list.html", items=items, usage=usage(db))

    @router.get("/new")
    def new(request: Request, user: User = Depends(current_user)) -> Response:
        return render(
            request, "catalog_form.html", item=None, name="", selected_icon=c.icons[0], sort_order=0
        )

    @router.post("/new")
    def create(
        request: Request,
        name: str = Form(""),
        icon: str = Form(""),
        sort_order: int = Form(0),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> Response:
        name = name.strip()
        error = validate(db, name, None)
        if error:
            return render(
                request,
                "catalog_form.html",
                400,
                item=None,
                name=name,
                selected_icon=icon,
                sort_order=sort_order,
                error=error,
            )
        db.add(c.model(name=name, icon=icon if icon in c.icons else None, sort_order=sort_order))
        db.commit()
        flash(request, f"„{name}” a fost adăugat.")
        return redirect(request, c.path)

    @router.get("/{item_id}")
    def edit(
        item_id: int,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        return render(
            request,
            "catalog_form.html",
            item=item,
            name=item.name,
            selected_icon=item.icon,
            sort_order=item.sort_order,
            used=usage(db).get(item.id, 0),
        )

    @router.post("/{item_id}")
    def update(
        item_id: int,
        request: Request,
        name: str = Form(""),
        icon: str = Form(""),
        sort_order: int = Form(0),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        name = name.strip()
        error = validate(db, name, item.id)
        if error:
            return render(
                request,
                "catalog_form.html",
                400,
                item=item,
                name=name,
                selected_icon=icon,
                sort_order=sort_order,
                used=usage(db).get(item.id, 0),
                error=error,
            )
        item.name = name
        item.icon = icon if icon in c.icons else item.icon
        item.sort_order = sort_order
        db.commit()
        flash(request, "Modificările au fost salvate.")
        return redirect(request, c.path)

    @router.post("/{item_id}/delete")
    def delete(
        item_id: int,
        request: Request,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        used = usage(db).get(item.id, 0)
        if used:
            flash(
                request,
                f"„{item.name}” are {used} dispozitive. Mută-le întâi în {c.other}.",
                "error",
            )
            return redirect(request, f"{c.path}/{item.id}")
        db.delete(item)
        db.commit()
        flash(request, f"„{item.name}” a fost șters.")
        return redirect(request, c.path)

    return router


routers = [make_router(c) for c in CATALOGS]
templates.env.globals["catalogs"] = CATALOGS
