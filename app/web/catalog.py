"""Managing rooms and device types: the same form (name, icon, order)."""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Device, DeviceType, Room, User
from app.i18n import N_
from app.icons import ROOM_ICONS, TYPE_ICONS
from app.services.portcheck import format_ports, parse_ports
from app.web.deps import admin_user, flash, get_db, redirect, t, templates, tn, type_name


@dataclass(frozen=True)
class Catalog:
    model: type
    device_column: object
    path: str
    title: str
    icons: list[str]
    has_ports: bool = False


CATALOGS = [
    Catalog(Room, Device.room_id, "/rooms", N_("Rooms"), ROOM_ICONS),
    Catalog(DeviceType, Device.type_id, "/types", N_("Types"), TYPE_ICONS, has_ports=True),
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

    def ports_or_error(request: Request, text: str) -> tuple[str | None, str | None]:
        if not c.has_ports:
            return None, None
        try:
            return format_ports(parse_ports(text)), None
        except ValueError as exc:
            return None, t(request, "Invalid port: {port}. Example: 3389 or 22, 443.", port=exc)

    def validate(request: Request, db: Session, name: str, item_id: int | None) -> str | None:
        if not name:
            return t(request, "The name is required.")
        if len(name) > 100:
            return t(request, "The name can have at most 100 characters.")
        clash = db.scalar(select(c.model).where(func.lower(c.model.name) == name.lower()))
        if clash is not None and clash.id != item_id:
            return t(request, "“{name}” already exists.", name=type_name(request, clash))
        return None

    @router.get("")
    def index(
        request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)
    ) -> Response:
        items = db.scalars(select(c.model).order_by(c.model.sort_order, c.model.name)).all()
        return render(request, "catalog_list.html", items=items, usage=usage(db))

    @router.get("/new")
    def new(request: Request, user: User = Depends(admin_user)) -> Response:
        return render(
            request,
            "catalog_form.html",
            item=None,
            name="",
            selected_icon=c.icons[0],
            sort_order=0,
            check_ports="",
        )

    @router.post("/new")
    def create(
        request: Request,
        name: str = Form(""),
        icon: str = Form(""),
        sort_order: int = Form(0),
        check_ports: str = Form(""),
        user: User = Depends(admin_user),
        db: Session = Depends(get_db),
    ) -> Response:
        name = name.strip()
        ports, ports_error = ports_or_error(request, check_ports)
        error = validate(request, db, name, None) or ports_error
        if error:
            return render(
                request,
                "catalog_form.html",
                400,
                item=None,
                name=name,
                selected_icon=icon,
                sort_order=sort_order,
                check_ports=check_ports,
                error=error,
            )
        item = c.model(name=name, icon=icon if icon in c.icons else None, sort_order=sort_order)
        if c.has_ports:
            item.check_ports = ports
        db.add(item)
        db.commit()
        flash(request, t(request, "“{name}” was added.", name=name))
        return redirect(request, c.path)

    @router.get("/{item_id}")
    def edit(
        item_id: int,
        request: Request,
        user: User = Depends(admin_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        return render(
            request,
            "catalog_form.html",
            item=item,
            name=type_name(request, item),
            selected_icon=item.icon,
            sort_order=item.sort_order,
            check_ports=getattr(item, "check_ports", None) or "",
            used=usage(db).get(item.id, 0),
        )

    @router.post("/{item_id}")
    def update(
        item_id: int,
        request: Request,
        name: str = Form(""),
        icon: str = Form(""),
        sort_order: int = Form(0),
        check_ports: str = Form(""),
        user: User = Depends(admin_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        name = name.strip()
        # A built-in type shown translated and saved unchanged keeps its English name.
        if name == type_name(request, item):
            name = item.name
        ports, ports_error = ports_or_error(request, check_ports)
        error = validate(request, db, name, item.id) or ports_error
        if error:
            return render(
                request,
                "catalog_form.html",
                400,
                item=item,
                name=name,
                selected_icon=icon,
                sort_order=sort_order,
                check_ports=check_ports,
                used=usage(db).get(item.id, 0),
                error=error,
            )
        item.name = name
        if c.has_ports:
            item.check_ports = ports
        item.icon = icon if icon in c.icons else item.icon
        item.sort_order = sort_order
        db.commit()
        flash(request, t(request, "Changes saved."))
        return redirect(request, c.path)

    @router.post("/{item_id}/delete")
    def delete(
        item_id: int,
        request: Request,
        user: User = Depends(admin_user),
        db: Session = Depends(get_db),
    ) -> Response:
        item = db.get(c.model, item_id)
        if item is None:
            return redirect(request, c.path)
        used = usage(db).get(item.id, 0)
        if used:
            message = tn(
                request,
                "“{name}” has {n} device. Move it elsewhere first.",
                "“{name}” has {n} devices. Move them elsewhere first.",
                used,
                name=type_name(request, item),
            )
            flash(request, message, "error")
            return redirect(request, f"{c.path}/{item.id}")
        db.delete(item)
        db.commit()
        flash(request, t(request, "“{name}” was deleted.", name=type_name(request, item)))
        return redirect(request, c.path)

    return router


routers = [make_router(c) for c in CATALOGS]
templates.env.globals["catalogs"] = CATALOGS
