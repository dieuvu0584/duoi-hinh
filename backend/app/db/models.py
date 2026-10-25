import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid, utcnow


class OwnerAccount(TimestampMixin, Base):
    """Single owner. Created only via the CLI; there is no registration route."""

    __tablename__ = "owner_account"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    devices: Mapped[list["Device"]] = relationship(back_populates="owner")


class Device(TimestampMixin, Base):
    """One row per paired client. Holds the current refresh token hash (one-time use)."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("owner_account.id"))
    label: Mapped[str] = mapped_column(String(100))
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # hash of the token that was replaced by the current one; presenting it again = theft signal
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(String(64))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))

    owner: Mapped[OwnerAccount] = relationship(back_populates="devices")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_lookup", "username", "attempted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemSettings(TimestampMixin, Base):
    """Single row (id = 1). Authoritative source for kill switch and limits (decision D-6)."""

    __tablename__ = "system_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint(
            "daily_budget_limit >= 0 AND monthly_budget_limit >= 0", name="budget_nonneg"
        ),
        CheckConstraint("max_publishes_per_day BETWEEN 0 AND 30", name="publish_cap_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    publishing_kill_switch: Mapped[bool] = mapped_column(Boolean, default=True)
    news_lane_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Budgets default to 0: every paid provider call is blocked until the owner sets them (D-18).
    daily_budget_limit: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    monthly_budget_limit: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    budget_currency: Mapped[str] = mapped_column(String(3), default="USD")
    max_image_regenerations: Mapped[int] = mapped_column(Integer, default=3)
    max_publishes_per_day: Mapped[int] = mapped_column(Integer, default=3)
    max_lateness_minutes: Mapped[int] = mapped_column(Integer, default=30)
    prepare_lead_minutes: Mapped[int] = mapped_column(Integer, default=20)
    # Where and with which voice we publish. Owner-editable; the environment value is only the
    # fallback while a column is empty. Tokens/keys are NOT here (they stay in the environment).
    facebook_page_id: Mapped[str | None] = mapped_column(String(40))
    youtube_channel_id: Mapped[str | None] = mapped_column(String(40))
    elevenlabs_voice_id: Mapped[str | None] = mapped_column(String(80))
    # Idea scanning: owner-chosen times of day (Vietnam time, "HH:MM"). Off until the owner turns
    # it on, because every scan spends a little on the LLM.
    youtube_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Approving publishes at once (no time to pick), on every enabled platform, if the kill switch
    # is off and automation is on. The approval is still the human decision.
    publish_on_approve: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    scan_times: Mapped[list[str]] = mapped_column(
        JSONB, default=lambda: ["10:00", "16:00", "20:00", "00:00"])


class AuditEvent(Base):
    """Append-only (enforced by a database trigger in the migration)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_at", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(80))  # system | owner:<id> | device:<id>
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[str] = mapped_column(String(20))  # ok | denied | error
    request_id: Mapped[str | None] = mapped_column(String(40))
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    note: Mapped[str | None] = mapped_column(Text)


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint("lane IN ('daily_life', 'rebus', 'news')", name="lane_valid"),
        # News must carry a freshness window so stale news can never publish (R-20).
        CheckConstraint("lane <> 'news' OR fresh_until IS NOT NULL", name="news_needs_fresh_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    lane: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_revisions.id", use_alter=True, name="fk_content_items_current_revision")
    )
    # What the owner asked for (lane brief, rebus answer); the pipeline's only input.
    pipeline_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    revisions: Mapped[list["ContentRevision"]] = relationship(
        back_populates="item", foreign_keys="ContentRevision.content_item_id"
    )


class ContentRevision(Base):
    """Immutable (database trigger). Any edit creates a new revision with a new content_hash."""

    __tablename__ = "content_revisions"
    __table_args__ = (
        Index("uq_content_revisions_item_no", "content_item_id", "revision_no", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    content_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    answer_text: Mapped[str | None] = mapped_column(Text)
    clue_mapping: Mapped[dict[str, Any] | list | None] = mapped_column(JSONB)
    rebus_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str] | None] = mapped_column(JSONB)
    overlay_text: Mapped[str | None] = mapped_column(Text)
    narration_text: Mapped[str | None] = mapped_column(Text)
    voice_id: Mapped[str | None] = mapped_column(String(80))
    media_hash: Mapped[str | None] = mapped_column(String(64))
    audio_hash: Mapped[str | None] = mapped_column(String(64))
    template_id: Mapped[str | None] = mapped_column(String(60))
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    hash_version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[ContentItem] = relationship(
        back_populates="revisions", foreign_keys=[content_item_id]
    )


class SafetyReview(Base):
    """Immutable record of one review, bound to the exact revision hash it examined."""

    __tablename__ = "safety_reviews"
    __table_args__ = (
        CheckConstraint("gate IN ('intake', 'final', 'prepublish')", name="gate_valid"),
        CheckConstraint("decision IN ('allow', 'hold_for_human', 'block')", name="decision_valid"),
        Index("ix_safety_reviews_lookup", "revision_id", "content_hash", "gate", "reviewed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_revisions.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    gate: Mapped[str] = mapped_column(String(12))
    decision: Mapped[str] = mapped_column(String(20))
    risk_level: Mapped[str] = mapped_column(String(10))
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    human_review_required: Mapped[bool] = mapped_column(Boolean)
    human_review_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(40))
    reviewer: Mapped[str] = mapped_column(String(80))  # llm:<model> | owner:<id>
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="decision_valid"),
        Index("ix_approvals_lookup", "revision_id", "content_hash", "decided_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_revisions.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(10))
    actor: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublishingRecord(TimestampMixin, Base):
    __tablename__ = "publishing_records"
    __table_args__ = (
        CheckConstraint("platform IN ('facebook', 'youtube')", name="platform_valid"),
        # At most one non-terminal publishing record per revision AND platform (no double
        # scheduling; one Facebook Reel and one YouTube video of the same revision may coexist).
        Index(
            "uq_publishing_records_one_active",
            "revision_id",
            "platform",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('FAILED', 'CANCELLED', 'SKIPPED_LATE', 'RETRACTED')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_revisions.id"))
    platform: Mapped[str] = mapped_column(String(10), default="facebook")  # facebook | youtube
    content_hash: Mapped[str] = mapped_column(String(64))
    intent_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="NOT_SCHEDULED", index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Provider video id and public reference. The `meta_` prefix is historical: on YouTube these
    # hold the YouTube videoId and the watch URL.
    meta_video_id: Mapped[str | None] = mapped_column(String(64))
    meta_post_id: Mapped[str | None] = mapped_column(String(64))
    media_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    copyright_clear: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    # Path of the rendered video inside NHD_MEDIA_DIR (never an arbitrary filesystem path).
    video_ref: Mapped[str | None] = mapped_column(String(300))
    reconcile_attempts: Mapped[int] = mapped_column(Integer, default=0)


class AudioAsset(Base):
    """A synthesised narration, content-addressed by `text_hash` (the cache key)."""

    __tablename__ = "audio_assets"
    __table_args__ = (CheckConstraint("kind IN ('narration', 'music', 'mix')", name="kind_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(12), default="narration")
    provider: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(60))
    voice_id: Mapped[str] = mapped_column(String(80))
    synth_settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # sha256(normalizer version + normalised text + voice + model + settings): same key = same audio
    text_hash: Mapped[str] = mapped_column(String(64), unique=True)
    normalized_text: Mapped[str] = mapped_column(Text)
    normalizer_version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_ref: Mapped[str] = mapped_column(String(300))  # relative to NHD_MEDIA_DIR
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    characters: Mapped[int] = mapped_column(Integer)
    credits_charged: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cost_ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_ledger.id"))
    license_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TokenHealth(Base):
    """Result of the latest provider token check. Absent or old = publishing is blocked."""

    __tablename__ = "token_health"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    valid: Mapped[bool] = mapped_column(Boolean)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scopes: Mapped[list[str] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(200))


class ProviderBudget(TimestampMixin, Base):
    """Per-provider spending caps in USD. No row, or a limit of 0, means it cannot spend (D-18)."""

    __tablename__ = "provider_budgets"
    __table_args__ = (
        CheckConstraint("monthly_limit_usd >= 0", name="monthly_nonneg"),
        CheckConstraint("daily_limit_usd IS NULL OR daily_limit_usd >= 0", name="daily_nonneg"),
    )

    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    monthly_limit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    daily_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))


class CostLedger(Base):
    """One row per paid provider call: reserved BEFORE the call, settled after it."""

    __tablename__ = "cost_ledger"
    __table_args__ = (
        CheckConstraint("status IN ('reserved', 'settled', 'released')", name="status_valid"),
        CheckConstraint("estimated_usd >= 0", name="estimate_nonneg"),
        Index("ix_cost_ledger_provider_time", "provider", "reserved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider: Mapped[str] = mapped_column(String(30))
    operation: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    request_id: Mapped[str | None] = mapped_column(String(120))
    estimated_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    actual_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    status: Mapped[str] = mapped_column(String(10), default="reserved")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class LlmCall(Base):
    """One structured LLM call: prompt (by hash), model, tokens, cost and whether it validated."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(60))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    schema_name: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(120))
    cost_ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_ledger.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompositionCheck(Base):
    """Result of the crop-safety check for one master image (docs/video-pipeline.md)."""

    __tablename__ = "composition_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    image_ref: Mapped[str] = mapped_column(String(300))
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(60))
    expected_clues: Mapped[list[str]] = mapped_column(JSONB)
    boxes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    problems: Mapped[list[str]] = mapped_column(JSONB)
    modes: Mapped[dict[str, str]] = mapped_column(JSONB)
    outside: Mapped[dict[str, list[str]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    cost_ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_ledger.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImageAsset(Base):
    """A generated (or uploaded) master image."""

    __tablename__ = "image_assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    seed: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_ref: Mapped[str] = mapped_column(String(300))  # relative to NHD_MEDIA_DIR
    cost_ledger_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_ledger.id"))
    # Which content item asked for it: the number of images per item bounds regeneration.
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_items.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoAsset(Base):
    """A rendered video (one target, one mode), content-addressed by the hash of its render spec."""

    __tablename__ = "video_assets"
    __table_args__ = (
        CheckConstraint("target IN ('reel_9x16', 'youtube_16x9')", name="target_valid"),
        CheckConstraint("mode IN ('fill', 'fit')", name="mode_valid"),
        CheckConstraint("status IN ('valid', 'invalid')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    target: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(4))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_hash: Mapped[str] = mapped_column(String(64), unique=True)
    storage_ref: Mapped[str] = mapped_column(String(300))  # relative to NHD_MEDIA_DIR
    sha256: Mapped[str] = mapped_column(String(64))
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8), default="valid")
    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audio_assets.id"))
    master_ref: Mapped[str | None] = mapped_column(String(300))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_revisions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedSource(Base):
    """An RSS/Atom feed the idea scan reads (owner-managed; URLs must be public https)."""

    __tablename__ = "feed_sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    kind: Mapped[str] = mapped_column(String(12), default="rss")  # rss | google_news
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedItem(Base):
    """One headline from a feed. The text is UNTRUSTED external data (threat-model T-1)."""

    __tablename__ = "feed_items"
    __table_args__ = (Index("ix_feed_items_fetched", "fetched_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feed_sources.id", ondelete="CASCADE"))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(String(600))
    url: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScanRun(Base):
    """One idea scan. `slot_key` makes a scheduled slot run at most once."""

    __tablename__ = "scan_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    slot_key: Mapped[str | None] = mapped_column(String(20), unique=True)
    trigger: Mapped[str] = mapped_column(String(10))  # schedule | manual
    status: Mapped[str] = mapped_column(String(8), default="queued")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(300))


class IdeaCandidate(Base):
    """A shortlisted idea waiting for the owner to pick it. Expires so old news does not linger."""

    __tablename__ = "idea_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'proposed', 'created', 'dismissed', 'expired')",
            name="status_valid"),
        Index("ix_idea_candidates_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    scan_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scan_runs.id"))
    situation: Mapped[str] = mapped_column(Text)  # neutral one-liner in the model's own words
    why: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    suggested_lane: Mapped[str] = mapped_column(String(12))  # daily_life | rebus | either
    risk_level: Mapped[str] = mapped_column(String(10))
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(10), default="new")
    lane: Mapped[str | None] = mapped_column(String(10))
    proposals: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_items.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Idiom(Base):
    """The owner's bank of idioms/proverbs that rebus ideas can twist."""

    __tablename__ = "idioms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    text: Mapped[str] = mapped_column(String(120), unique=True)
    meaning: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoFeedback(Base):
    """The owner's verdict on a finished video: one row per content item (the latest wins)."""

    __tablename__ = "video_feedback"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_items.id"), unique=True)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("content_revisions.id"))
    rating: Mapped[int] = mapped_column(Integer)
    labels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    note: Mapped[str] = mapped_column(String(500), default="")
    # Optional levels 1-5: group (image, text) -> dimension (surprise, meaning, impact, humor)
    scores: Mapped[dict[str, dict[str, int]]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lesson(Base):
    """A short rule for this channel's videos, distilled from feedback and approved by the owner.

    Only `approved` lessons reach a prompt; they are style guidance and never override the
    answer, safety or schema rules (the backend enforces those itself)."""

    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'approved', 'rejected')", name="status_valid"),
        CheckConstraint("lane IN ('all', 'daily_life', 'rebus', 'news')", name="lane_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    text: Mapped[str] = mapped_column(String(300))
    lane: Mapped[str] = mapped_column(String(10), default="all")
    status: Mapped[str] = mapped_column(String(10), default="proposed")
    source: Mapped[str] = mapped_column(String(10), default="auto")  # auto | owner
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
