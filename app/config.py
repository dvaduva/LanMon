import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings from LANMON_* environment variables (or from .env)."""

    model_config = SettingsConfigDict(env_prefix="LANMON_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    # Relative to the current folder when running locally; the Docker image sets /data.
    data_dir: Path = Path("data")
    subnet: str | None = None
    interface: str | None = None
    secret_key: str = ""
    log_level: str = "info"
    # Time zone for WOL schedules and uptime days.
    tz: str = "Europe/Bucharest"
    version: str = "dev"
    # Session cookie over HTTPS only (e.g. behind `tailscale serve`).
    cookie_secure: bool = False
    # Scanner, pinger and other background tasks; disabled in tests.
    background_tasks: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lanmon.db"

    def resolved_secret_key(self) -> str:
        """LANMON_SECRET_KEY or, if missing, a key generated once and kept in data_dir."""
        if self.secret_key:
            return self.secret_key
        path = self.data_dir / "secret.key"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
        return path.read_text().strip()

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
