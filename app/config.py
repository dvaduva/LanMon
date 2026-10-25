from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Setări din variabile de mediu cu prefixul LANMON_ (sau din .env)."""

    model_config = SettingsConfigDict(env_prefix="LANMON_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: Path = Path("/data")
    subnet: str | None = None
    interface: str | None = None
    secret_key: str = ""
    log_level: str = "info"
    version: str = "dev"
    # Scanerul și ping-ul; dezactivate în teste.
    background_tasks: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lanmon.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
