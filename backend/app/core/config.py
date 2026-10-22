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

    # Rendered videos live under this directory; publishing refuses any path outside it.
    media_dir: str = "media"

    # Words the owner never wants in public text (checked deterministically, in addition to the LLM
    # review). Comma-separated in NHD_BLACKLIST_WORDS; empty by default.
    blacklist_words: str = ""
    # Folder with the system prompts (prompts/*.md) and the JSON schemas (docs/schemas).
    prompts_dir: str = "prompts"
    schemas_dir: str = "docs/schemas"

    # Rendering: x264 preset (slower = smaller files) and an optional font with Vietnamese glyphs.
    x264_preset: str = "medium"
    font_path: str | None = None

    # Claude (vision composition check). Prices are USD per million tokens for the default model
    # (claude-opus-5-5 list price, 2026-10-06); they only drive the budget estimate/settlement.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    claude_model: str = "claude-opus-5-5"
    claude_usd_per_mtok_in: float = 4.0
    claude_usd_per_mtok_out: float = 20.0

    # Image generation through fal.ai. USD per megapixel comes from fal.ai/pricing (FLUX.2: 0.012).
    fal_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("FAL_API_KEY"))
    fal_model: str = "fal-ai/flux-2"
    fal_usd_per_megapixel: float = 0.012

    # ElevenLabs (voiceover). The key is a SecretStr; USD per credit is an ASSUMPTION used for the
    # budget estimate (set it from your plan/invoice; the default is deliberately on the high side).
    elevenlabs_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str | None = Field(
        default=None, validation_alias=AliasChoices("ELEVENLABS_VOICE_ID"))
    elevenlabs_model_id: str = Field(
        default="eleven_v3", validation_alias=AliasChoices("ELEVENLABS_MODEL_ID"))
    elevenlabs_usd_per_credit: float = 0.0003

    # YouTube channel (identifier only; the OAuth secrets are not used yet).
    youtube_channel_id: str | None = Field(
        default=None, validation_alias=AliasChoices("YOUTUBE_CHANNEL_ID"))

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
