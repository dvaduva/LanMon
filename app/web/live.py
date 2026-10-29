"""Server-Sent Events: schimbările de stare ajung în browser fără refresh."""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from app.db.models import User
from app.web.deps import session_fingerprint

router = APIRouter()

HEARTBEAT_S = 20
LIVE_EVENTS = {"device_status", "service_status", "wol", "scan_completed", "device_discovered"}


def _authenticated(request: Request) -> bool:
    # Fără Depends(get_db): sesiunea DB nu trebuie ținută deschisă cât durează fluxul.
    uid = request.session.get("uid")
    if not uid:
        return False
    with request.app.state.session_factory() as db:
        user = db.get(User, uid)
        return user is not None and request.session.get("fp") == session_fingerprint(user)


async def event_stream(request: Request, heartbeat_s: float = HEARTBEAT_S):
    bus = request.app.state.events
    queue = bus.subscribe()
    try:
        yield "retry: 5000\n\n"
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event.kind in LIVE_EVENTS:
                yield f"event: {event.kind}\ndata: {json.dumps(event.data)}\n\n"
    finally:
        bus.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> Response:
    if not await asyncio.to_thread(_authenticated, request):
        return Response(status_code=401)
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
