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
