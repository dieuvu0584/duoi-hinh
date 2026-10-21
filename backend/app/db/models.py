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
        # At most one non-terminal publishing record per revision (no double scheduling).
        Index(
            "uq_publishing_records_one_active",
            "revision_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('FAILED', 'CANCELLED', 'SKIPPED_LATE', 'RETRACTED')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_revisions.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    intent_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="NOT_SCHEDULED", index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta_video_id: Mapped[str | None] = mapped_column(String(64))
    meta_post_id: Mapped[str | None] = mapped_column(String(64))
    media_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    copyright_clear: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(60))


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
