"""Start the content pipeline from an idea, retry a waiting item, schedule an approved one."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_owner, get_publish_providers
from app.api.review import hold_info
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import ContentItem, OwnerAccount, PublishingRecord
from app.db.session import get_session
from app.services import auth_service, content_pipeline, content_service, pipeline_runner
from app.services import publishing_service as ps
from app.services.state_machine import ContentState

router = APIRouter(prefix="/content", tags=["pipeline"])


class IdeaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life", "rebus", "news"]
    brief: str = Field(min_length=1, max_length=2000)
    # Rebus only: the owner's verified answer (the model never chooses or changes it).
    answer: str | None = Field(default=None, max_length=500)
    # Specific requests for the AI (e.g. "make the cat bigger, no crowd"), most useful when the
    # owner recreates a video they were not happy with.
    instructions: str | None = Field(default=None, max_length=1500)


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
                                  answer=body.answer, instructions=body.instructions)
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


@router.post("/{content_id}/resume")
def resume_held(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Retry an item held because the WORK failed (render timeout, image prompt could not be made).

    Holds that come from a safety or answer finding (analysis, QA, final review) are not
    resumable: the owner corrects the idea and creates it again, or rejects it."""
    item = _load(session, content_id)
    hold = hold_info(item)
    if hold is None or hold["kind"] != "held":
        raise ApiError(409, "not_held", "This item is not on hold")
    if not hold["can_resume"]:
        raise ApiError(409, "not_resumable",
                       "This hold came from a review finding; correct the idea or reject it")
    # A render that failed restarts at rendering (the image and narration already exist and are
    # not bought again); a failed generation starts the generation stage over.
    target = (ContentState.RENDERING if hold["stage"] == "render"
              else ContentState.READY_FOR_GENERATION)
    content_service.transition_content(session, item, target, actor=f"owner:{owner.id}",
                                       reason="owner_resume")
    pipeline_runner.clear_retry(session, item)
    return {"id": str(item.id), "status": item.status}


# Records the owner may still cancel: nothing is public and no upload/publish call is in flight.
CANCELLABLE = {"SCHEDULED", "READY_TO_PUBLISH", "BLOCKED", "EXPIRED"}
BUSY = {"PREPARING", "PUBLISHING", "UNKNOWN_RESULT", "RETRACTION_REQUESTED"}
ENDED = ("FAILED", "CANCELLED", "SKIPPED_LATE", "RETRACTED")


class _NoProvider:
    """Stands in when a platform has no provider: only used when no draft exists there."""

    async def delete(self, *, video_id: str) -> None:  # pragma: no cover - guarded by the caller
        raise RuntimeError("no provider")


def _active_records(session: Session, item: ContentItem) -> list[PublishingRecord]:
    return list(session.scalars(
        select(PublishingRecord).where(
            PublishingRecord.revision_id == item.current_revision_id,
            PublishingRecord.status.notin_(ENDED)).order_by(PublishingRecord.platform)))


@router.post("/{content_id}/cancel-schedule")
async def cancel_schedule(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
    providers=Depends(get_publish_providers),
):
    """Take an approved post back before it goes public: cancels every queued/ready record (the
    private draft at Facebook/YouTube is deleted) and returns the item to APPROVED."""
    item = _load(session, content_id)
    active = _active_records(session, item)
    if any(r.status in BUSY for r in active):
        raise ApiError(409, "busy",
                       "A video is being uploaded or published right now; try again in a minute")
    todo = [r for r in active if r.status in CANCELLABLE]
    if not todo:
        raise ApiError(409, "nothing_to_cancel", "Nothing is queued for publishing")
    for record in todo:
        provider = providers.get(record.platform)
        if provider is None and record.meta_video_id:
            raise ApiError(409, "provider_unavailable",
                           "Cannot remove the draft: this platform is not configured")
        await ps.cancel(session, provider or _NoProvider(), record.id, actor=f"owner:{owner.id}")
    session.expire_all()
    return {"cancelled": [r.platform for r in todo], "status": _load(session, content_id).status}


class RetractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)
    password: str = Field(min_length=1, max_length=256)


@router.post("/{content_id}/retract")
async def retract_published(
    content_id: uuid.UUID,
    body: RetractRequest,
    request: Request,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
    providers=Depends(get_publish_providers),
):
    """Take PUBLISHED posts down (the password and a reason are required; both are audited)."""
    auth_service.verify_owner_password(session, owner, body.password, client_ip(request))
    item = _load(session, content_id)
    published = [r for r in _active_records(session, item) if r.status == "PUBLISHED"]
    if not published:
        raise ApiError(409, "nothing_to_retract", "There is no published post to take down")
    results = {}
    for record in published:
        provider = providers.get(record.platform)
        if provider is None:
            results[record.platform] = "provider_unavailable"
            continue
        results[record.platform] = await ps.retract(
            session, provider, record.id, reason=body.reason, actor=f"owner:{owner.id}")
    return {"results": results}


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
    del revision  # the service reads the current revision itself
    records = ps.schedule_item(session, item=item, when=body.scheduled_for,
                               platforms=list(body.platforms), actor=f"owner:{owner.id}")
    first = records[0]
    return {"record_id": str(first.id), "status": first.status,
            "scheduled_for": first.scheduled_for.isoformat(),
            "records": [{"record_id": str(r.id), "platform": r.platform, "status": r.status,
                         "scheduled_for": r.scheduled_for.isoformat()} for r in records]}
