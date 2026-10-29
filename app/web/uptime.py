from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.models import User
from app.services.uptime import device_uptime
from app.web.dashboard import group_by_room
from app.web.deps import current_user, get_db, templates

router = APIRouter()

DAYS = 30


@router.get("/uptime")
def uptime_page(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    tz = ZoneInfo(request.app.state.settings.tz)
    now = datetime.now(UTC)
    groups = group_by_room(db, user)
    uptimes = {d.id: device_uptime(db, d.id, now, DAYS, tz) for g in groups for d in g.devices}
    context = {"groups": groups, "uptimes": uptimes, "days": DAYS}
    return templates.TemplateResponse(request, "uptime.html", context)
