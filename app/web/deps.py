from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import User
from app.icons import ROOM_ICONS, TYPE_ICONS
from app.security import new_token, tokens_match

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals.update(room_icons=ROOM_ICONS, type_icons=TYPE_ICONS)


class Redirect(Exception):  # noqa: N818 — e un semnal de control, nu o eroare
    def __init__(self, url: str) -> None:
        self.url = url


def redirect(request: Request, url: str) -> Response:
    """Redirect 303; pentru cereri HTMX folosește HX-Redirect ca să schimbe toată pagina."""
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


class CsrfError(Exception):
    pass


async def verify_csrf(request: Request) -> None:
    """Toate cererile care modifică ceva trebuie să poarte tokenul din sesiune."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    given = request.headers.get("X-CSRF-Token")
    if not given:
        form = await request.form()
        given = form.get("csrf_token")
    if not tokens_match(request.session.get("csrf"), given if isinstance(given, str) else None):
        raise CsrfError


def session_fingerprint(user: User) -> str:
    """Se schimbă odată cu parola, deci schimbarea parolei invalidează celelalte sesiuni."""
    return user.password_hash[-16:]


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session.update(uid=user.id, fp=session_fingerprint(user), csrf=new_token())


def has_users(db: Session) -> bool:
    return bool(db.scalar(select(exists().where(User.id.is_not(None)))))


class Forbidden(Exception):  # noqa: N818 — semnal: pagina e doar pentru administrator
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or request.session.get("fp") != session_fingerprint(user):
        request.session.pop("uid", None)
        raise Redirect("/login" if has_users(db) else "/setup")
    request.state.user = user
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden
    return user


def viewer(request: Request) -> User | None:
    """Utilizatorul curent, pentru șabloane (meniul diferă pentru membri)."""
    return getattr(request.state, "user", None)


templates.env.globals["viewer"] = viewer
