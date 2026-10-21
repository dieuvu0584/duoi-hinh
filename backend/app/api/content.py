import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import current_owner
from app.core.errors import ApiError
from app.db.models import ContentItem, OwnerAccount
from app.db.session import get_session
from app.services import content_service

router = APIRouter(prefix="/content", tags=["content"])


class RevisionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_text: str | None = Field(default=None, max_length=500)
    clue_mapping: list[dict[str, Any]] | dict[str, Any] | None = None
    rebus_spec: dict[str, Any] | None = None
    caption: str | None = Field(default=None, max_length=2000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    overlay_text: str | None = Field(default=None, max_length=500)
    media_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    audio_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    template_id: str | None = Field(default=None, max_length=60)
    is_ai_generated: bool = True


class CreateContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life", "rebus", "news"]
    revision: RevisionFields
    fresh_until: datetime | None = None


class DecisionNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


def _load(session: Session, content_id: uuid.UUID) -> ContentItem:
    item = session.get(ContentItem, content_id)
    if item is None:
        raise ApiError(404, "not_found", "Content not found")
    return item


def _out(session: Session, item: ContentItem) -> dict[str, Any]:
    revision = content_service.current_revision(session, item)
    return {
        "id": str(item.id), "lane": item.lane, "status": item.status,
        "fresh_until": item.fresh_until.isoformat() if item.fresh_until else None,
        "current_revision": {
            "id": str(revision.id), "revision_no": revision.revision_no,
            "content_hash": revision.content_hash, "caption": revision.caption,
            "hashtags": revision.hashtags, "overlay_text": revision.overlay_text,
            "is_ai_generated": revision.is_ai_generated,
            # The answer is admin-only metadata; this route is authenticated (rebus answer private).
            "answer_text": revision.answer_text,
        },
    }


@router.post("", status_code=201)
def create(
    body: CreateContent,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    item = content_service.create_content(
        session, owner, lane=body.lane, fresh_until=body.fresh_until,
        revision=body.revision.model_dump(exclude_unset=True),
    )
    return _out(session, item)


@router.get("/{content_id}")
def get(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    return _out(session, _load(session, content_id))


@router.post("/{content_id}/revisions", status_code=201)
def new_revision(
    content_id: uuid.UUID,
    body: RevisionFields,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    item = _load(session, content_id)
    content_service.edit_content(session, owner, item, body.model_dump(exclude_unset=True))
    return _out(session, item)


@router.post("/{content_id}/approve")
def approve(
    content_id: uuid.UUID,
    body: DecisionNote,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    item = _load(session, content_id)
    content_service.approve(session, owner, item, note=body.note)
    return _out(session, item)


@router.post("/{content_id}/reject")
def reject(
    content_id: uuid.UUID,
    body: DecisionNote,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    item = _load(session, content_id)
    content_service.reject(session, owner, item, note=body.note)
    return _out(session, item)
