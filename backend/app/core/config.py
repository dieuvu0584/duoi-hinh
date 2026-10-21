from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NHD_", extra="ignore")
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://nhd:nhd_dev_only@localhost:5432/nhd"
    redis_url: str = "redis://localhost:6379/0"
    publishing_kill_switch: bool = True
    publish_mode: Literal["dry_run", "live"] = "dry_run"

settings = Settings()
