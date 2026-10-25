import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    # Un singur worker: scanerul și ping-ul sunt task-uri de fundal în același proces.
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
