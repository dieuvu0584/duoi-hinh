"""Owner review surface: queue, explanation ticket, media preview, human-review completion.

Everything here is authenticated (the explanation ticket contains the rebus answer). Media is only
served for the item's CURRENT revision, from inside NHD_MEDIA_DIR.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_owner, get_settings
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import (
    AudioAsset,
    ContentItem,
    ContentRevision,
    OwnerAccount,
    SafetyReview,
    VideoAsset,
)
from app.db.session import get_session
from app.services import content_service
from app.services.media_paths import UnsafeMediaPath, resolve_media_path
from app.services.state_machine import ContentState

router = APIRouter(prefix="/content", tags=["review"])

QUEUE_STATES = {
    ContentState.PENDING_APPROVAL.value,
    ContentState.HELD_FOR_HUMAN.value,
}


def _load(session: Session, content_id: uuid.UUID) -> ContentItem:
    item = session.get(ContentItem, content_id)
    if item is None:
        raise ApiError(404, "not_found", "Content not found")
    return item


def _review_out(review: SafetyReview | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "id": str(review.id), "gate": review.gate, "decision": review.decision,
        "risk_level": review.risk_level, "categories": review.categories,
        "findings": review.findings, "human_review_required": review.human_review_required,
        "human_review_complete": review.human_review_complete,
        "reviewer": review.reviewer, "reviewed_at": review.reviewed_at.isoformat(),
        "expires_at": review.expires_at.isoformat(),
    }


@router.get("")
def list_content(
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Newest first. Without `status`: everything that needs the owner (approval or a hold)."""
    states = {status} if status else QUEUE_STATES
    if not states <= {s.value for s in ContentState}:
        raise ApiError(422, "unknown_status", "Unknown status")
    items = session.scalars(
        select(ContentItem).where(ContentItem.status.in_(states))
        .order_by(ContentItem.updated_at.desc()).limit(limit)
    ).all()
    out = []
    for item in items:
        revision = (
            content_service.current_revision(session, item) if item.current_revision_id else None
        )
        out.append({
            "id": str(item.id), "lane": item.lane, "status": item.status,
            "updated_at": item.updated_at.isoformat(),
            "caption": revision.caption if revision else None,
            "overlay_text": revision.overlay_text if revision else None,
        })
    return {"items": out}


@router.get("/{content_id}/review")
def review_ticket(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """The admin-only explanation ticket for the current revision."""
    item = _load(session, content_id)
    revision = content_service.current_revision(session, item)
    data = item.pipeline_input or {}
    final = content_service.latest_review(session, revision, "final")
    # The intake review belongs to the revision that existed at intake (before media was bound),
    # so it is looked up across the item's revisions; it is context, not an approval basis.
    intake = session.scalar(
        select(SafetyReview).join(ContentRevision, ContentRevision.id == SafetyReview.revision_id)
        .where(ContentRevision.content_item_id == item.id, SafetyReview.gate == "intake")
        .order_by(SafetyReview.reviewed_at.desc()).limit(1))
    videos = session.scalars(
        select(VideoAsset).where(VideoAsset.revision_id == revision.id,
                                 VideoAsset.status == "valid").order_by(VideoAsset.target)
    ).all()
    audio = (
        session.scalar(select(AudioAsset).where(AudioAsset.sha256 == revision.audio_hash))
        if revision.audio_hash else None
    )
    human_ok = bool(final) and (not final.human_review_required or final.human_review_complete)
    return {
        "id": str(item.id), "lane": item.lane, "status": item.status,
        "revision": {
            "id": str(revision.id), "revision_no": revision.revision_no,
            "content_hash": revision.content_hash, "answer_text": revision.answer_text,
            "clue_mapping": revision.clue_mapping, "caption": revision.caption,
            "hashtags": revision.hashtags, "overlay_text": revision.overlay_text,
            "narration_text": revision.narration_text, "voice_id": revision.voice_id,
            "is_ai_generated": revision.is_ai_generated,
        },
        "explanation": {
            "visual_explanation": (data.get("image_prompt") or {}).get("visual_explanation"),
            "analysis": data.get("analysis"),
            "last_qa_issues": data.get("last_qa_issues"),
            "notes": data.get("notes"),
        },
        "safety": {"intake": _review_out(intake), "final": _review_out(final)},
        "videos": [
            {"target": v.target, "mode": v.mode, "width": v.width, "height": v.height,
             "duration_s": str(v.duration_s) if v.duration_s is not None else None,
             "url": f"/api/content/{item.id}/media/{v.target}"}
            for v in videos
        ],
        "audio_url": f"/api/content/{item.id}/audio" if audio else None,
        # Informative only: the state machine and the publish gate decide for real.
        "can_approve": item.status == ContentState.PENDING_APPROVAL.value and human_ok,
        "needs_human_review_completion": bool(
            final and final.human_review_required and not final.human_review_complete),
    }


@router.post("/{content_id}/human-review/complete")
def complete_human_review(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """The owner confirms they personally reviewed the CURRENT revision's final safety review."""
    item = _load(session, content_id)
    revision = content_service.current_revision(session, item)
    final = content_service.latest_review(session, revision, "final")
    if final is None:
        raise ApiError(409, "no_review", "There is no final safety review for this revision")
    if final.decision == "block":
        raise ApiError(409, "review_blocked", "A blocked review cannot be cleared by a human")
    content_service.complete_human_review(session, owner, final.id)
    return _review_out(final)


def _serve(cfg: Settings, ref: str, media_type: str) -> FileResponse:
    try:
        path = resolve_media_path(cfg.media_dir, ref)
    except UnsafeMediaPath as exc:
        raise ApiError(404, "media_unavailable", "Media file is not available") from exc
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store"})


@router.get("/{content_id}/media/{target}")
def video(
    content_id: uuid.UUID,
    target: Literal["reel_9x16", "youtube_16x9"],
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
):
    item = _load(session, content_id)
    revision = content_service.current_revision(session, item)
    asset = session.scalar(
        select(VideoAsset).where(VideoAsset.revision_id == revision.id,
                                 VideoAsset.target == target, VideoAsset.status == "valid"))
    if asset is None:
        raise ApiError(404, "media_unavailable", "No rendered video for this revision")
    return _serve(cfg, asset.storage_ref, "video/mp4")


@router.get("/{content_id}/audio")
def audio(
    content_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
):
    item = _load(session, content_id)
    revision = content_service.current_revision(session, item)
    asset = (
        session.scalar(select(AudioAsset).where(AudioAsset.sha256 == revision.audio_hash))
        if revision.audio_hash else None
    )
    if asset is None:
        raise ApiError(404, "media_unavailable", "No narration for this revision")
    return _serve(cfg, asset.storage_ref, "audio/mpeg")
