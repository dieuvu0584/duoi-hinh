"""Start the content pipeline from an idea, retry a waiting item, schedule an approved one."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_owner
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import ContentItem, OwnerAccount, VideoAsset
from app.db.session import get_session
from app.services import content_pipeline, content_service, pipeline_runner
from app.services import publishing_service as ps
from app.services.settings_service import get_settings_row

router = APIRouter(prefix="/content", tags=["pipeline"])


class IdeaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life", "rebus", "news"]
    brief: str = Field(min_length=1, max_length=2000)
    # Rebus only: the owner's verified answer (the model never chooses or changes it).
    answer: str | None = Field(default=None, max_length=500)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_for: datetime
    # Where to publish. Facebook gets the 9:16 Reel, YouTube the 16:9 video of the same revision.
    platforms: list[Literal["facebook", "youtube"]] = Field(
        default_factory=lambda: ["facebook"], min_length=1, max_length=2)


def _load(session: Session, content_id: uuid.UUID) -> ContentItem:
    item = session.get(ContentItem, content_id)
    if item is None:
        raise ApiError(404, "not_found", "Content not found")
    return item


@router.post("/ideas", status_code=201)
def create_idea(
    body: IdeaRequest,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Queue an idea; the worker generates it (analysis -> media -> QA -> your approval)."""
    item = content_pipeline.start(session, owner, lane=body.lane, brief=body.brief,
                                  answer=body.answer)
    return {"id": str(item.id), "lane": item.lane, "status": item.status}


@router.post("/{content_id}/retry")
def retry_now(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Skip the back-off of an item that is waiting (e.g. after raising a budget)."""
    item = _load(session, content_id)
    if item.status not in {s.value for s in pipeline_runner.ACTIVE}:
        raise ApiError(409, "not_in_pipeline", "This item is not being generated")
    pipeline_runner.clear_retry(session, item)
    return {"id": str(item.id), "status": item.status}


@router.post("/{content_id}/schedule", status_code=201)
def schedule(
    content_id: uuid.UUID,
    body: ScheduleRequest,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Schedule an APPROVED item on one or both platforms (each gets its own record and gate)."""
    if body.scheduled_for.tzinfo is None:
        raise ApiError(422, "naive_datetime", "scheduled_for must include a timezone")
    if body.scheduled_for <= utcnow():
        raise ApiError(422, "in_the_past", "scheduled_for must be in the future")
    item = _load(session, content_id)
    revision = content_service.current_revision(session, item)
    platforms = list(dict.fromkeys(body.platforms))
    if "youtube" in platforms and not get_settings_row(session).youtube_enabled:
        raise ApiError(409, "youtube_disabled", "YouTube publishing is switched off in Settings")
    videos = {}
    for platform in platforms:  # check everything first so nothing is half-scheduled
        video = session.scalar(select(VideoAsset).where(
            VideoAsset.revision_id == revision.id,
            VideoAsset.target == ps.PLATFORM_TARGET[platform], VideoAsset.status == "valid"))
        if video is None:
            raise ApiError(409, "no_video", "There is no rendered video for the approved revision")
        videos[platform] = video
    records = [ps.schedule(session, item=item, scheduled_for=body.scheduled_for,
                           actor=f"owner:{owner.id}", video_ref=videos[p].storage_ref, platform=p)
               for p in platforms]
    first = records[0]
    return {"record_id": str(first.id), "status": first.status,
            "scheduled_for": first.scheduled_for.isoformat(),
            "records": [{"record_id": str(r.id), "platform": r.platform, "status": r.status,
                         "scheduled_for": r.scheduled_for.isoformat()} for r in records]}
