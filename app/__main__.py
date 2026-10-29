import logging

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # A single worker: the scanner and pinger are background tasks in the same process.
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level,
        proxy_headers=True,
        # Open SSE connections must not keep the container alive on `docker compose up -d`.
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
