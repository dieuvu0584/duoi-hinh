"""Owner verdicts on finished videos and the lessons distilled from them."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_owner, get_llm, get_settings
from app.core.config import Settings
from app.db.models import Lesson, OwnerAccount
from app.db.session import get_session
from app.services import feedback_service as fs

router = APIRouter(tags=["feedback"])

Lane = Literal["all", "daily_life", "rebus", "news"]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=1, le=5)
    labels: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=500)
    # Optional levels 1-5: {"image": {"surprise": 4, ...}, "text": {"humor": 2, ...}}
    scores: dict[str, dict[str, int]] = Field(default_factory=dict)


@router.post("/content/{content_id}/feedback")
def save_feedback(
    content_id: uuid.UUID, body: FeedbackRequest,
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    """Store (or replace) the owner's verdict on this video."""
    return fs.feedback_out(fs.save_feedback(
        session, owner, content_id, rating=body.rating, labels=body.labels, note=body.note,
        scores=body.scores))


@router.get("/feedback/summary")
def feedback_summary(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    return fs.summary(session)


@router.get("/lessons")
def list_lessons(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    rows = session.scalars(select(Lesson).order_by(Lesson.created_at.desc()).limit(200)).all()
    return {"lessons": [fs.lesson_out(r) for r in rows]}


class LessonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=300)
    lane: Lane = "all"


@router.post("/lessons", status_code=201)
def add_lesson(
    body: LessonCreate,
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    return fs.lesson_out(fs.add_lesson(session, owner, text=body.text, lane=body.lane))


class LessonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["proposed", "approved", "rejected"] | None = None
    text: str | None = Field(default=None, min_length=3, max_length=300)
    lane: Lane | None = None


@router.patch("/lessons/{lesson_id}")
def patch_lesson(
    lesson_id: uuid.UUID, body: LessonPatch,
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    return fs.lesson_out(fs.decide(session, owner, lesson_id, status=body.status,
                                   text=body.text, lane=body.lane))


@router.delete("/lessons/{lesson_id}", status_code=204)
def delete_lesson(
    lesson_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
):
    fs.delete(session, owner, lesson_id)


@router.post("/lessons/distill")
async def distill_lessons(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings), llm=Depends(get_llm),
):
    """Ask Claude to PROPOSE lessons from recent feedback (a few cents); nothing is applied."""
    made = await fs.distill(session, llm, cfg, owner)
    return {"proposed": [fs.lesson_out(r) for r in made]}
