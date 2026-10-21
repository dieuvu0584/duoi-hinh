from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ".env" next to the working directory, or the repo-root one when running from backend/.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_prefix="NHD_", extra="ignore"
    )

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://nhd:nhd_dev_only@127.0.0.1:5432/nhd"
    redis_url: str = "redis://127.0.0.1:6379/0"
    # Seeds system_settings on first boot only; the database value is authoritative afterwards.
    publishing_kill_switch: bool = True
    publish_mode: Literal["dry_run", "live"] = "dry_run"

    # Meta (names come straight from .env: META_*). The token is a SecretStr and never printed.
    meta_page_id: str | None = Field(default=None, validation_alias=AliasChoices("META_PAGE_ID"))
    meta_access_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("META_ACCESS_TOKEN"))
    meta_api_version: str = Field(
        default="v26.0", validation_alias=AliasChoices("META_API_VERSION"))

    # Auth. The secret must be set explicitly outside development (see validate_for_runtime).
    jwt_secret: str = "dev-only-insecure-secret-change-me-32-bytes-min"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    login_max_failures: int = 5
    login_window_minutes: int = 15

    def validate_for_runtime(self) -> None:
        """Refuse to start in a non-development environment with insecure defaults."""
        if self.environment != "development":
            if self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32:
                raise RuntimeError("NHD_JWT_SECRET must be set to a random value (>= 32 chars)")
            if "nhd_dev_only" in self.database_url:
                raise RuntimeError("NHD_DATABASE_URL still uses the development password")
            if self.publish_mode == "dry_run" and self.meta_access_token:
                raise RuntimeError(
                    "a Meta token is configured but NHD_PUBLISH_MODE=dry_run: set it to 'live' "
                    "deliberately or remove the token"
                )


settings = Settings()
