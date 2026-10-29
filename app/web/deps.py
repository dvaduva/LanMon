from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import User
from app.i18n import (
    BUILTIN_TYPES,
    LANGUAGES,
    WEEKDAYS,
    detail_text,
    gettext,
    negotiate,
    ngettext,
    normalize,
)
from app.icons import ROOM_ICONS, TYPE_ICONS
from app.security import new_token, tokens_match

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals.update(room_icons=ROOM_ICONS, type_icons=TYPE_ICONS)


class Redirect(Exception):  # noqa: N818 — a control-flow signal, not an error
    def __init__(self, url: str) -> None:
        self.url = url


def redirect(request: Request, url: str) -> Response:
    """303 redirect; HTMX requests get HX-Redirect so the whole page changes."""
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = new_token()
    return token


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append([kind, message])


def pop_flashes(request: Request) -> list[list[str]]:
    return request.session.pop("flash", [])


templates.env.globals.update(csrf_token=csrf_token, pop_flashes=pop_flashes)


def language(request: Request) -> str:
    """The signed-in user's language, else the one chosen on the login page, else the browser's."""
    lang = getattr(request.state, "lang", None) or normalize(request.session.get("lang"))
    return lang or negotiate(request.headers.get("accept-language"))


def t(request: Request, message: str, **params) -> str:
    return gettext(language(request), message, **params)


def tn(request: Request, singular: str, plural: str, n: int, **params) -> str:
    return ngettext(language(request), singular, plural, n, **params)


@pass_context
def _template_gettext(context, message: str, **params) -> str:
    return t(context["request"], message, **params)


@pass_context
def _template_ngettext(context, singular: str, plural: str, n: int, **params) -> str:
    return tn(context["request"], singular, plural, n, **params)


@pass_context
def _template_detail(context, text: str | None) -> str:
    return detail_text(language(context["request"]), text)


@pass_context
def _template_language(context) -> str:
    return language(context["request"])


def type_name(request: Request, item) -> str:
    """Built-in device types keep English names in the database and are translated on display."""
    if getattr(item, "is_builtin", False) and item.name in BUILTIN_TYPES:
        return t(request, item.name)
    return item.name


@pass_context
def _template_type_name(context, item) -> str:
    return type_name(context["request"], item)


templates.env.globals.update(
    _=_template_gettext,
    ngettext=_template_ngettext,
    detail=_template_detail,
    current_language=_template_language,
    type_name=_template_type_name,
    languages=LANGUAGES,
    weekdays=WEEKDAYS,
)


class CsrfError(Exception):
    pass


async def verify_csrf(request: Request) -> None:
    """Every state-changing request must carry the session token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.url.path.startswith("/api/"):
        return  # the API doesn't use the session cookie but a bearer token
    given = request.headers.get("X-CSRF-Token")
    if not given:
        form = await request.form()
        given = form.get("csrf_token")
    if not tokens_match(request.session.get("csrf"), given if isinstance(given, str) else None):
        raise CsrfError


def session_fingerprint(user: User) -> str:
    """Changes with the password, so a password change invalidates other sessions."""
    return user.password_hash[-16:]


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session.update(
        uid=user.id, fp=session_fingerprint(user), csrf=new_token(), lang=user.language
    )


def has_users(db: Session) -> bool:
    return bool(db.scalar(select(exists().where(User.id.is_not(None)))))


class Forbidden(Exception):  # noqa: N818 — signal: the page is for administrators only
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or request.session.get("fp") != session_fingerprint(user):
        request.session.pop("uid", None)
        raise Redirect("/login" if has_users(db) else "/setup")
    request.state.user = user
    request.state.lang = normalize(user.language) or language(request)
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden
    return user


def viewer(request: Request) -> User | None:
    """The current user, for templates (members get a smaller menu)."""
    return getattr(request.state, "user", None)


templates.env.globals["viewer"] = viewer
