import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Setări din variabile de mediu cu prefixul LANMON_ (sau din .env)."""

    model_config = SettingsConfigDict(env_prefix="LANMON_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    # Relativ la folderul curent când rulează local; imaginea Docker setează /data.
    data_dir: Path = Path("data")
    subnet: str | None = None
    interface: str | None = None
    secret_key: str = ""
    log_level: str = "info"
    version: str = "dev"
    # Cookie-ul de sesiune doar prin HTTPS (ex. în spatele `tailscale serve`).
    cookie_secure: bool = False
    # Scanerul și ping-ul; dezactivate în teste.
    background_tasks: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lanmon.db"

    def resolved_secret_key(self) -> str:
        """LANMON_SECRET_KEY sau, dacă lipsește, o cheie generată o dată și păstrată în data_dir."""
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
