from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()


@router.get("/health", include_in_schema=False)
def health(request: Request) -> JSONResponse:
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "error"
    status_code = 200 if db == "ok" else 503
    return JSONResponse(
        {"status": "ok" if db == "ok" else "error", "db": db, "version": request.app.version},
        status_code=status_code,
    )
