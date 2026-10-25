"""The owner's verdicts on finished videos, and the lessons distilled from them.

Feedback is stored as the owner wrote it. A model may only PROPOSE lessons from it; a lesson
reaches a prompt only after the owner approves it, and prompts treat it as style guidance that
never overrides the answer, safety or schema rules (those are enforced by backend code).
"""

import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import ContentItem, ContentRevision, IdeaCandidate, Lesson, VideoFeedback
from app.services import llm_service
from app.services.audit import record_audit

LABELS = (
    "great", "funny", "confusing_image", "unclear_clues", "too_cluttered", "bad_text",
    "bad_voiceover", "too_slow", "answer_too_easy", "answer_too_hard",
)
GROUPS = ("image", "text")  # the picture, and the words (overlay text, caption, voiceover)
DIMENSIONS = ("surprise", "meaning", "impact", "humor")
MAX_PROMPT_LESSONS = 15
DISTILL_WINDOW = 40  # most recent verdicts sent to the model
MAX_LESSONS = 60  # approved + proposed kept; keeps prompts and the list small


def clean_scores(scores: dict[str, dict[str, int]] | None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for group, dims in (scores or {}).items():
        if group not in GROUPS or not isinstance(dims, dict):
            raise ApiError(422, "unknown_score_group", f"Unknown score group: {group}")
        for dim, value in dims.items():
            if dim not in DIMENSIONS:
                raise ApiError(422, "unknown_score_dimension", f"Unknown dimension: {dim}")
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise ApiError(422, "bad_score", "Levels are whole numbers from 1 to 5")
            out.setdefault(group, {})[dim] = value
    return out


def save_feedback(session: Session, owner, item_id: uuid.UUID, *, rating: int,
                  labels: list[str], note: str,
                  scores: dict[str, dict[str, int]] | None = None) -> VideoFeedback:
    item = session.get(ContentItem, item_id)
    if item is None:
        raise ApiError(404, "not_found", "Content not found")
    bad = [x for x in labels if x not in LABELS]
    if bad:
        raise ApiError(422, "unknown_label", f"Unknown label: {bad[0]}")
    revision = session.scalar(
        select(ContentRevision).where(ContentRevision.content_item_id == item_id)
        .order_by(ContentRevision.revision_no.desc()).limit(1))
    row = session.scalar(select(VideoFeedback).where(VideoFeedback.content_item_id == item_id))
    now = utcnow()
    if row is None:
        row = VideoFeedback(content_item_id=item_id, created_at=now)
        session.add(row)
    row.revision_id = revision.id if revision else None
    row.rating, row.labels = rating, sorted(set(labels))
    row.note, row.updated_at = note.strip(), now
    row.scores = clean_scores(scores)
    record_audit(session, actor=f"owner:{owner.id}", action="feedback.saved", result="ok",
                 entity_type="content_item", entity_id=str(item_id),
                 metadata={"rating": rating, "labels": row.labels})
    session.commit()
    return row


def feedback_out(row: VideoFeedback | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"rating": row.rating, "labels": list(row.labels or []), "note": row.note,
            "scores": row.scores or {}, "updated_at": row.updated_at.isoformat()}


def summary(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(VideoFeedback, ContentItem.lane).join(
            ContentItem, ContentItem.id == VideoFeedback.content_item_id)).all()
    by_lane: dict[str, list[int]] = defaultdict(list)
    labels: Counter[str] = Counter()
    levels: dict[str, list[int]] = defaultdict(list)
    for fb, lane in rows:
        by_lane[lane].append(fb.rating)
        labels.update(fb.labels or [])
        for group, dims in (fb.scores or {}).items():
            for dim, value in dims.items():
                levels[f"{group}.{dim}"].append(value)
    rated_ideas = session.execute(
        select(IdeaCandidate.owner_rating, IdeaCandidate.score)
        .where(IdeaCandidate.owner_rating.is_not(None))).all()
    return {
        "ideas": {
            "count": len(rated_ideas),
            "average_rating": (round(sum(r for r, _ in rated_ideas) / len(rated_ideas), 2)
                               if rated_ideas else None),
            # What Claude's own score looks like for each star level the owner gave.
            "average_claude_score_by_rating": {
                str(star): round(sum(s for r, s in rated_ideas if r == star)
                                 / sum(1 for r, _ in rated_ideas if r == star), 1)
                for star in range(1, 6) if any(r == star for r, _ in rated_ideas)},
        },
        "count": len(rows),
        "by_lane": {lane: {"count": len(r), "average": round(sum(r) / len(r), 2)}
                    for lane, r in by_lane.items()},
        "labels": dict(labels.most_common()),
        "levels": {k: {"count": len(v), "average": round(sum(v) / len(v), 2)}
                   for k, v in sorted(levels.items())},
    }


def lesson_out(row: Lesson) -> dict[str, Any]:
    return {"id": str(row.id), "text": row.text, "lane": row.lane, "status": row.status,
            "source": row.source, "reason": row.reason,
            "created_at": row.created_at.isoformat()}


def approved_lessons(session: Session, lane: str) -> list[str]:
    """What a prompt may see: approved lessons for this lane (and for all), newest first."""
    rows = session.scalars(
        select(Lesson).where(Lesson.status == "approved", Lesson.lane.in_(["all", lane]))
        .order_by(Lesson.decided_at.desc().nullslast(), Lesson.created_at.desc())
        .limit(MAX_PROMPT_LESSONS)).all()
    return [r.text for r in rows]


def add_lesson(session: Session, owner, *, text: str, lane: str) -> Lesson:
    row = Lesson(text=text.strip(), lane=lane, status="approved", source="owner",
                 decided_at=utcnow())
    session.add(row)
    record_audit(session, actor=f"owner:{owner.id}", action="lesson.added", result="ok",
                 entity_type="lesson", entity_id=str(row.id), metadata={"lane": lane})
    session.commit()
    return row


def decide(session: Session, owner, lesson_id: uuid.UUID, *, status: str | None,
           text: str | None, lane: str | None) -> Lesson:
    row = session.get(Lesson, lesson_id)
    if row is None:
        raise ApiError(404, "not_found", "Lesson not found")
    if text is not None:
        row.text = text.strip()
    if lane is not None:
        row.lane = lane
    if status is not None and status != row.status:
        row.status, row.decided_at = status, utcnow()
    record_audit(session, actor=f"owner:{owner.id}", action="lesson.updated", result="ok",
                 entity_type="lesson", entity_id=str(row.id), metadata={"status": row.status})
    session.commit()
    return row


def delete(session: Session, owner, lesson_id: uuid.UUID) -> None:
    row = session.get(Lesson, lesson_id)
    if row is None:
        raise ApiError(404, "not_found", "Lesson not found")
    record_audit(session, actor=f"owner:{owner.id}", action="lesson.deleted", result="ok",
                 entity_type="lesson", entity_id=str(row.id), metadata={})
    session.delete(row)
    session.commit()


def _evidence(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(VideoFeedback, ContentItem).join(
            ContentItem, ContentItem.id == VideoFeedback.content_item_id)
        .order_by(VideoFeedback.updated_at.desc()).limit(DISTILL_WINDOW)).all()
    out = []
    for fb, item in rows:
        rev = session.get(ContentRevision, fb.revision_id) if fb.revision_id else None
        data = item.pipeline_input or {}
        qa = (data.get("qa") or [{}])[-1]
        out.append({
            "lane": item.lane, "rating": fb.rating, "labels": fb.labels, "note": fb.note,
            "levels": fb.scores or {},
            "overlay_text": rev.overlay_text if rev else None,
            "caption": rev.caption if rev else None,
            "clues": [c.get("visual_clue") for c in (rev.clue_mapping or [])
                      if isinstance(c, dict)] if rev else [],
            "qa_score": qa.get("score_total"), "qa_issues": (data.get("last_qa_issues") or [])[:4],
        })
    return out


def _idea_evidence(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(IdeaCandidate).where(IdeaCandidate.owner_rating.is_not(None))
        .order_by(IdeaCandidate.created_at.desc()).limit(DISTILL_WINDOW)).all()
    return [{"situation": c.situation[:200], "score": c.score, "owner_rating": c.owner_rating,
             "lane": c.lane or c.suggested_lane, "made": c.status == "created"} for c in rows]


def rate_idea(session: Session, owner, candidate_id: uuid.UUID, rating: int) -> IdeaCandidate:
    cand = session.get(IdeaCandidate, candidate_id)
    if cand is None:
        raise ApiError(404, "not_found", "Idea not found")
    cand.owner_rating = rating
    record_audit(session, actor=f"owner:{owner.id}", action="idea.rated", result="ok",
                 entity_type="idea_candidate", entity_id=str(cand.id),
                 metadata={"rating": rating})
    session.commit()
    return cand


async def distill(session: Session, llm, cfg: Settings, owner) -> list[Lesson]:
    """Ask the model for NEW lesson proposals from recent feedback; nothing is applied."""
    evidence = _evidence(session)
    ideas = _idea_evidence(session)
    if len(evidence) + len(ideas) < 3:
        raise ApiError(409, "not_enough_feedback",
                       "Cần ít nhất 3 video hoặc ý tưởng đã có đánh giá để rút bài học")
    existing = session.scalars(
        select(Lesson).where(Lesson.status != "rejected")
        .order_by(Lesson.created_at.desc()).limit(MAX_LESSONS)).all()
    if len(existing) >= MAX_LESSONS:
        raise ApiError(409, "too_many_lessons", "Hãy xóa bớt bài học cũ trước")
    payload = {"feedback": evidence, "idea_feedback": ideas,
               "existing_lessons": [{"text": r.text, "lane": r.lane} for r in existing]}
    prompt = (Path(cfg.prompts_dir) / "distill_lessons.md").read_text(encoding="utf-8")
    try:
        out = await llm_service.call_json(
            session, llm, cfg, purpose="distill_lessons", system_prompt=prompt,
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            schema_name="lessons_distill", entity_type="lessons", entity_id="distill",
            schema_dir=cfg.schemas_dir, est_input_tokens=9000, max_tokens=3000)
    except llm_service.LlmRefused as exc:
        raise ApiError(422, "llm_refused", "The model declined") from exc
    except llm_service.LlmInvalidOutput as exc:
        raise ApiError(502, "llm_invalid_output", "The model's answer could not be used") from exc
    known = {r.text.strip().lower() for r in existing}
    made: list[Lesson] = []
    for item in out["lessons"]:
        text = item["text"].strip()
        if not text or text.lower() in known:
            continue
        known.add(text.lower())
        row = Lesson(text=text, lane=item["lane"], status="proposed", source="auto",
                     reason=item["reason"].strip()[:300])
        session.add(row)
        made.append(row)
    record_audit(session, actor=f"owner:{owner.id}", action="lesson.distilled", result="ok",
                 entity_type="lessons", entity_id="distill", metadata={"proposed": len(made)})
    session.commit()
    return made
