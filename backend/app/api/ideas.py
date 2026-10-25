"""Idea inbox: scan results, picking, proposals, feed sources and the idiom bank."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_owner, get_llm, get_settings
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.urlsafety import is_safe_public_https_url
from app.db.models import FeedSource, IdeaCandidate, Idiom, OwnerAccount, ScanRun
from app.db.session import get_session
from app.services import external_prompts, feedback_service, idea_service
from app.services.audit import record_audit

router = APIRouter(prefix="/ideas", tags=["ideas"])


def _candidate_out(c: IdeaCandidate, *, detail: bool = False) -> dict[str, Any]:
    out = {
        "id": str(c.id), "status": c.status, "situation": c.situation, "why": c.why,
        "score": c.score, "suggested_lane": c.suggested_lane,
        "sources": [{"title": s["title"], "source": s["source"], "url": s["url"]}
                    for s in c.source_items],
        "created_at": c.created_at.isoformat(), "expires_at": c.expires_at.isoformat(),
        "lane": c.lane, "content_item_id": str(c.content_item_id) if c.content_item_id else None,
        "owner_rating": c.owner_rating,
    }
    if detail:
        out["proposals"] = c.proposals or []
    return out


def _load(session: Session, candidate_id: uuid.UUID) -> IdeaCandidate:
    cand = session.get(IdeaCandidate, candidate_id)
    if cand is None:
        raise ApiError(404, "not_found", "Idea not found")
    return cand


# --------------------------------------------------------------------------- candidates
@router.get("/candidates")
def list_candidates(
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=100),
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    states = {s for s in (status or "new,proposed").split(",") if s}
    if not states <= {"new", "proposed", "created", "dismissed", "expired"}:
        raise ApiError(422, "unknown_status", "Unknown status")
    rows = session.scalars(
        select(IdeaCandidate).where(IdeaCandidate.status.in_(states))
        .order_by(IdeaCandidate.score.desc(), IdeaCandidate.created_at.desc()).limit(limit)).all()
    last = session.scalar(select(ScanRun).order_by(ScanRun.requested_at.desc()).limit(1))
    return {
        "items": [_candidate_out(c) for c in rows],
        "last_scan": None if last is None else {
            "status": last.status, "trigger": last.trigger, "note": last.note,
            "requested_at": last.requested_at.isoformat(), "candidates": last.candidates,
            "items_new": last.items_new,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None},
    }


@router.get("/candidates/{candidate_id}")
def get_candidate(
    candidate_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    return _candidate_out(_load(session, candidate_id), detail=True)


class RatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=1, le=5)


@router.post("/candidates/{candidate_id}/rating")
def rate_candidate(
    candidate_id: uuid.UUID,
    body: RatingRequest,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """The owner's stars for an idea (any status); Claude learns from them when lessons are made."""
    cand = feedback_service.rate_idea(session, owner, candidate_id, body.rating)
    return _candidate_out(cand)


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life"]


@router.post("/candidates/{candidate_id}/propose")
async def propose(
    candidate_id: uuid.UUID,
    body: ProposeRequest,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
    llm=Depends(get_llm),
):
    """Ask Claude for 3 options for the chosen lane (rebus: twisting an idiom from the bank)."""
    cand = await idea_service.propose(session, llm, cfg, candidate_id, body.lane)
    return _candidate_out(cand, detail=True)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life"]
    brief: str = Field(min_length=1, max_length=2000)
    answer: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=1500)


class PromptsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lane: Literal["daily_life"]
    brief: str = Field(min_length=1, max_length=2000)
    instructions: str | None = Field(default=None, max_length=1500)


@router.post("/prompts")
def prompts_for_other_tools(
    body: PromptsRequest,
    owner: OwnerAccount = Depends(current_owner),
):
    """Copy-ready prompts (Gemini, Flow/Veo, HeyGen) from the idea text; no model call, no cost."""
    return {"prompts": external_prompts.build_from_brief(
        lane=body.lane, brief=body.brief, instructions=body.instructions)}


@router.post("/candidates/{candidate_id}/create", status_code=201)
def create(
    candidate_id: uuid.UUID,
    body: CreateRequest,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    """Start the content pipeline from the owner's (edited) brief and verified answer."""
    item = idea_service.create_content(
        session, owner, candidate_id, lane=body.lane, brief=body.brief, answer=body.answer,
        instructions=body.instructions)
    return {"id": str(item.id), "lane": item.lane, "status": item.status}


@router.post("/candidates/{candidate_id}/dismiss", status_code=204)
def dismiss(
    candidate_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    idea_service.dismiss(session, owner, candidate_id)


@router.post("/scan", status_code=202)
def scan_now(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    """Queue a scan; the worker runs it within about a minute."""
    run = idea_service.queue_manual_scan(session, owner)
    return {"id": str(run.id), "status": run.status}


# --------------------------------------------------------------------------- sources
class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=10, max_length=500)


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _source_out(s: FeedSource) -> dict[str, Any]:
    return {"id": str(s.id), "name": s.name, "url": s.url, "kind": s.kind, "enabled": s.enabled,
            "last_fetched_at": s.last_fetched_at.isoformat() if s.last_fetched_at else None,
            "last_error": s.last_error}


@router.get("/sources")
def list_sources(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    rows = session.scalars(select(FeedSource).order_by(FeedSource.name)).all()
    return {"items": [_source_out(s) for s in rows]}


@router.post("/sources", status_code=201)
def add_source(
    body: SourceCreate,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    url = body.url.strip()
    if not is_safe_public_https_url(url):
        raise ApiError(422, "unsafe_url", "Only public https feed URLs are allowed")
    kind = "google_news" if url.startswith("https://news.google.com/") else "rss"
    source = FeedSource(name=body.name.strip(), url=url, kind=kind, enabled=True)
    session.add(source)
    record_audit(session, actor=f"owner:{owner.id}", action="ideas.source.add",
                 entity_type="feed_source", entity_id=url[:80])
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(409, "source_exists", "This feed is already in the list") from exc
    return _source_out(source)


@router.patch("/sources/{source_id}")
def patch_source(
    source_id: uuid.UUID,
    body: SourcePatch,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    source = session.get(FeedSource, source_id)
    if source is None:
        raise ApiError(404, "not_found", "Source not found")
    source.enabled = body.enabled
    session.commit()
    return _source_out(source)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(
    source_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    source = session.get(FeedSource, source_id)
    if source is None:
        raise ApiError(404, "not_found", "Source not found")
    record_audit(session, actor=f"owner:{owner.id}", action="ideas.source.delete",
                 entity_type="feed_source", entity_id=source.url[:80])
    session.delete(source)
    session.commit()


# --------------------------------------------------------------------------- idiom bank
class IdiomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=2, max_length=120)
    meaning: str = Field(default="", max_length=300)


def _idiom_out(i: Idiom) -> dict[str, Any]:
    return {"id": str(i.id), "text": i.text, "meaning": i.meaning, "enabled": i.enabled}


@router.get("/idioms")
def list_idioms(
    owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)
):
    rows = session.scalars(select(Idiom).order_by(Idiom.text)).all()
    return {"items": [_idiom_out(i) for i in rows]}


@router.post("/idioms", status_code=201)
def add_idiom(
    body: IdiomCreate,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    idiom = Idiom(text=" ".join(body.text.split()), meaning=body.meaning.strip(), enabled=True)
    session.add(idiom)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(409, "idiom_exists", "This idiom is already in the bank") from exc
    return _idiom_out(idiom)


@router.delete("/idioms/{idiom_id}", status_code=204)
def delete_idiom(
    idiom_id: uuid.UUID,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    idiom = session.get(Idiom, idiom_id)
    if idiom is None:
        raise ApiError(404, "not_found", "Idiom not found")
    session.delete(idiom)
    session.commit()
