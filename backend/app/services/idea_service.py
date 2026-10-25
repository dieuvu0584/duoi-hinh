"""Idea scan: feeds -> shortlist (Claude) -> owner picks -> options (Claude) -> owner edits.

Headlines are untrusted data. The model only shortlists and proposes; the backend drops anything
sensitive, checks that a rebus answer is not leaked in its own clues, and nothing reaches the
content pipeline until the owner presses "create" with text they have seen and may have edited.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import utcnow
from app.db.models import (
    ContentItem,
    FeedItem,
    FeedSource,
    IdeaCandidate,
    Idiom,
    OwnerAccount,
    ScanRun,
)
from app.providers.contracts import JsonLLM
from app.providers.errors import ProviderError
from app.services import content_pipeline, feed_service, feedback_service, llm_service
from app.services.audit import record_audit
from app.services.safety_rules import _fold, leaks_answer
from app.services.settings_service import get_settings_row

log = logging.getLogger(__name__)

VN = timezone(timedelta(hours=7))  # Vietnam has no daylight saving time
SLOT_GRACE = timedelta(hours=3)  # a missed slot is still run for this long, then skipped
LOOKBACK = timedelta(hours=48)
CANDIDATE_TTL = timedelta(hours=48)
KEEP_ITEMS = timedelta(days=7)
MAX_PENDING_ITEMS = 80
MIN_SCORE = 50
MAX_CANDIDATES = 10
ACTIVE = ("new", "proposed")
FILTER_PROMPT = "idea_filter"
PROPOSE_PROMPT = "idea_propose"


# ------------------------------------------------------------------------------ schedule
def due_slots(now: datetime, times: list[str], grace: timedelta = SLOT_GRACE) -> list[str]:
    """Slot keys ("YYYY-MM-DDTHH:MM", Vietnam time) that passed less than `grace` ago."""
    local = now.astimezone(VN)
    keys: list[str] = []
    for day_offset in (0, -1):  # yesterday's late slot (e.g. 00:00 crossing midnight) still counts
        day = (local + timedelta(days=day_offset)).date()
        for hhmm in times:
            hour, minute = (int(x) for x in hhmm.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=VN)
            if slot <= local < slot + grace:
                keys.append(slot.strftime("%Y-%m-%dT%H:%M"))
    return sorted(set(keys))


def queue_due_scans(
    session: Session, times: list[str], now: datetime | None = None
) -> list[ScanRun]:
    """Create a `queued` run for each due slot not yet run (the unique slot key makes it once)."""
    created: list[ScanRun] = []
    for key in due_slots(now or utcnow(), times):
        run = ScanRun(slot_key=key, trigger="schedule", status="queued")
        session.add(run)
        try:
            session.commit()
            created.append(run)
        except IntegrityError:
            session.rollback()  # already queued/run for this slot
    return created


def queue_manual_scan(session: Session, owner: OwnerAccount) -> ScanRun:
    busy = session.scalar(select(ScanRun).where(ScanRun.status.in_(("queued", "running"))).limit(1))
    if busy is not None:
        raise ApiError(409, "scan_in_progress", "A scan is already queued or running")
    run = ScanRun(trigger="manual", status="queued")
    session.add(run)
    record_audit(session, actor=f"owner:{owner.id}", action="ideas.scan.request",
                 entity_type="scan_run", entity_id="manual")
    session.commit()
    return run


# ------------------------------------------------------------------------------ scan
def _blacklisted(cfg: Settings, *texts: str | None) -> bool:
    words = [_fold(w.strip()) for w in cfg.blacklist_words.split(",") if w.strip()]
    haystack = _fold(" ".join(t for t in texts if t))
    return any(w in haystack for w in words)


def _title_key(title: str) -> str:
    return _fold(title).strip()


def pick_batch(items: list[FeedItem], limit: int = MAX_PENDING_ITEMS) -> list[FeedItem]:
    """Newest first, but the sources take turns, so one busy feed (or the foreign ones) cannot
    crowd the others out of the limited batch sent to the model."""
    by_source: dict[uuid.UUID, list[FeedItem]] = {}
    for item in items:  # `items` is already newest first
        by_source.setdefault(item.source_id, []).append(item)
    batch: list[FeedItem] = []
    depth = 0
    while len(batch) < limit and any(len(v) > depth for v in by_source.values()):
        for queue in by_source.values():
            if len(queue) > depth and len(batch) < limit:
                batch.append(queue[depth])
        depth += 1
    return batch


def _store_items(session: Session, sources: list[FeedSource], results: dict, now: datetime) -> int:
    known_titles = {
        _title_key(t) for (t,) in session.execute(
            select(FeedItem.title).where(FeedItem.fetched_at > now - LOOKBACK))}
    added = 0
    for src in sources:
        res = results[src.url]
        src.last_fetched_at = now
        if isinstance(res, feed_service.FeedError):
            src.last_error = str(res)[:200]
            continue
        src.last_error = None
        for raw in res:
            digest = feed_service.url_hash(raw.url)
            if session.scalar(select(FeedItem.id).where(FeedItem.url_hash == digest)):
                continue
            key = _title_key(raw.title)
            if key in known_titles:  # the same story from another feed
                continue
            known_titles.add(key)
            session.add(FeedItem(source_id=src.id, url_hash=digest, title=raw.title,
                                 summary=raw.summary or None, url=raw.url,
                                 published_at=raw.published_at, fetched_at=now))
            added += 1
    session.commit()
    return added


async def run_scan(
    session: Session, llm: JsonLLM, cfg: Settings, run_id: uuid.UUID, *,
    fetch: feed_service.Fetcher = feed_service.http_fetch, now: datetime | None = None,
) -> ScanRun:
    now = now or utcnow()
    run = session.get(ScanRun, run_id, populate_existing=True)
    if run is None or run.status != "queued":
        raise ApiError(409, "scan_not_queued", "This scan is not waiting to run")
    run.status, run.started_at = "running", now
    session.commit()
    try:
        sources = list(session.scalars(select(FeedSource).where(FeedSource.enabled.is_(True))))
        results = await feed_service.fetch_many([s.url for s in sources], fetch)
        run.items_new = _store_items(session, sources, results, now)
        pending = pick_batch(list(session.scalars(
            select(FeedItem).where(FeedItem.processed_at.is_(None),
                                   FeedItem.fetched_at > now - LOOKBACK)
            .order_by(FeedItem.published_at.desc().nullslast()).limit(600))))
        if not pending:
            return _finish(session, run, "done", note="no_new_items")
        candidates = await _shortlist(session, llm, cfg, pending, run, now)
        for item in pending:
            item.processed_at = now
        run.candidates = candidates
        failed = [s.name for s in sources if s.last_error]
        return _finish(session, run, "done", note=f"feeds_failed:{len(failed)}" if failed else None)
    except ApiError as exc:  # e.g. budget_*: the items stay unprocessed and are retried next scan
        session.rollback()
        return _finish(session, run, "failed", note=exc.code)
    except llm_service.LlmRefused:
        session.rollback()
        return _finish(session, run, "failed", note="llm_refused")
    except llm_service.LlmInvalidOutput:
        session.rollback()
        return _finish(session, run, "failed", note="llm_invalid_output")
    except ProviderError as exc:
        session.rollback()
        return _finish(session, run, "failed", note=f"provider:{type(exc).__name__}")


def _finish(session: Session, run: ScanRun, status: str, *, note: str | None = None) -> ScanRun:
    run = session.get(ScanRun, run.id, populate_existing=True)
    run.status, run.finished_at, run.note = status, utcnow(), note
    session.commit()
    return run


async def _shortlist(
    session: Session, llm: JsonLLM, cfg: Settings, items: list[FeedItem], run: ScanRun,
    now: datetime,
) -> int:
    names = {sid: name for sid, name in session.execute(select(FeedSource.id, FeedSource.name))}
    payload = [{"id": i, "title": it.title, "summary": (it.summary or "")[:200],
                "source": names.get(it.source_id, "")} for i, it in enumerate(items, start=1)]
    prompt = _read_prompt(cfg, FILTER_PROMPT)
    lessons = feedback_service.approved_lessons(session, "daily_life")
    calib = feedback_service.calibration(session)
    extras = {**({"channel_lessons": lessons} if lessons else {}),
              **({"owner_rated_examples": calib} if calib else {})}
    body = {"headlines": payload, **extras}
    out = await llm_service.call_json(
        session, llm, cfg, purpose="idea_filter", system_prompt=prompt,
        content=[{"type": "text", "text": _dumps(body if extras else payload)}],
        schema_name="idea_filter",
        entity_type="scan_run", entity_id=str(run.id), schema_dir=cfg.schemas_dir,
        est_input_tokens=8000, max_tokens=6000)
    active_urls = {
        s["url"] for (src,) in session.execute(
            select(IdeaCandidate.source_items).where(IdeaCandidate.status.in_(ACTIVE)))
        for s in src}
    kept: list[IdeaCandidate] = []
    for c in sorted(out["candidates"], key=lambda c: -c["score"]):
        ids = sorted(set(c["item_ids"]))
        if not ids or any(i < 1 or i > len(items) for i in ids):
            continue  # the model invented an id
        if c["score"] < MIN_SCORE or c["risk_level"] in {"high", "critical"}:
            continue
        if c["sensitive_categories"] or _blacklisted(cfg, c["situation"], c["why"]):
            continue
        sources = [items[i - 1] for i in ids]
        if any(s.url in active_urls for s in sources):
            continue  # already on the owner's list
        kept.append(IdeaCandidate(
            scan_run_id=run.id, situation=c["situation"].strip(), why=c["why"].strip(),
            score=c["score"], suggested_lane=c["suggested_lane"], risk_level=c["risk_level"],
            categories=[], status="new", expires_at=now + CANDIDATE_TTL,
            source_items=[{"title": s.title, "source": names.get(s.source_id, ""), "url": s.url,
                           "published_at": s.published_at.isoformat() if s.published_at else None}
                          for s in sources]))
        active_urls.update(s.url for s in sources)
        if len(kept) >= MAX_CANDIDATES:
            break
    session.add_all(kept)
    session.commit()
    return len(kept)


def expire_and_purge(session: Session, now: datetime | None = None) -> None:
    """Candidates past their time become `expired`; old headlines are deleted."""
    now = now or utcnow()
    for cand in session.scalars(select(IdeaCandidate).where(
            IdeaCandidate.status.in_(ACTIVE), IdeaCandidate.expires_at < now)):
        cand.status = "expired"
    session.execute(delete(FeedItem).where(FeedItem.fetched_at < now - KEEP_ITEMS))
    session.commit()


# ------------------------------------------------------------------------------ propose
def _active_candidate(session: Session, candidate_id: uuid.UUID) -> IdeaCandidate:
    cand = session.get(IdeaCandidate, candidate_id, populate_existing=True)
    if cand is None:
        raise ApiError(404, "not_found", "Idea not found")
    if cand.status not in ACTIVE:
        raise ApiError(409, "idea_closed", "This idea is no longer open")
    return cand


def clean_options(lane: str, raw: list[dict], bank: list[str], cfg: Settings) -> list[dict]:
    """The backend's own check of the model's options (never trust them as given)."""
    folded_bank = {_fold(t): t for t in bank}
    out: list[dict] = []
    for o in raw:
        brief, title = o["brief"].strip(), o["title"].strip()
        if not brief or not title or _blacklisted(cfg, brief, title, o["answer"], o["explanation"]):
            continue
        option = {"title": title, "brief": brief, "explanation": o["explanation"].strip(),
                  "difficulty": o["difficulty"], "answer": "", "idiom": "", "twist": "",
                  "syllables": []}
        if lane == "rebus":
            answer = " ".join(o["answer"].split())
            if not answer or leaks_answer(answer, brief, title):
                continue  # a puzzle that gives itself away is useless
            option.update(
                answer=answer, twist=o["twist"].strip(),
                idiom=folded_bank.get(_fold(o["idiom"]), ""),
                syllables=answer.split())
        out.append(option)
    return out


async def propose(
    session: Session, llm: JsonLLM, cfg: Settings, candidate_id: uuid.UUID, lane: str,
) -> IdeaCandidate:
    if lane not in {"daily_life", "rebus"}:
        raise ApiError(422, "bad_lane", "Choose daily_life or rebus")
    cand = _active_candidate(session, candidate_id)
    bank: list[Idiom] = []
    if lane == "rebus":
        bank = list(session.scalars(select(Idiom).where(Idiom.enabled.is_(True))))
        if not bank:
            raise ApiError(422, "no_idioms", "Add idioms to the idiom bank first")
    lessons = feedback_service.approved_lessons(session, lane)
    payload = {"lane": lane, "situation": cand.situation,
               "headlines": [s["title"] for s in cand.source_items],
               **({"channel_lessons": lessons} if lessons else {}),
               **({"idiom_bank": [{"text": i.text, "meaning": i.meaning} for i in bank]}
                  if lane == "rebus" else {})}
    try:
        out = await llm_service.call_json(
            session, llm, cfg, purpose="idea_propose",
            system_prompt=_read_prompt(cfg, PROPOSE_PROMPT),
            content=[{"type": "text", "text": _dumps(payload)}], schema_name="idea_propose",
            entity_type="idea_candidate", entity_id=str(cand.id), schema_dir=cfg.schemas_dir,
            est_input_tokens=5000, max_tokens=5000)
    except llm_service.LlmRefused as exc:
        raise ApiError(422, "llm_refused", "The model declined this idea") from exc
    except llm_service.LlmInvalidOutput as exc:
        raise ApiError(502, "llm_invalid_output", "The model's answer could not be used") from exc
    options = clean_options(lane, out["options"], [i.text for i in bank], cfg)
    if not options:
        raise ApiError(422, "no_valid_options", "No usable option came back; try again")
    cand = _active_candidate(session, candidate_id)
    cand.lane, cand.proposals, cand.status = lane, options, "proposed"
    session.commit()
    return cand


def create_content(
    session: Session, owner: OwnerAccount, candidate_id: uuid.UUID, *, lane: str, brief: str,
    answer: str | None, instructions: str | None = None,
) -> ContentItem:
    """The owner's final, possibly edited, text starts the pipeline (rebus answer = verified)."""
    cand = _active_candidate(session, candidate_id)
    item = content_pipeline.start(session, owner, lane=lane, brief=brief, answer=answer,
                                  instructions=instructions)
    cand.status, cand.lane, cand.content_item_id = "created", lane, item.id
    record_audit(session, actor=f"owner:{owner.id}", action="ideas.create",
                 entity_type="idea_candidate", entity_id=str(cand.id),
                 metadata={"lane": lane, "content_item_id": str(item.id)})
    session.commit()
    return item


MAX_WAITING_FOR_OWNER = 6  # stop making videos while this many already wait for the owner
AUTO_RISK = "low"  # only ideas the filter rated low risk are turned into videos by themselves


def _vn_day_start_utc(now: datetime) -> datetime:
    local = now.astimezone(timezone(timedelta(hours=7)))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def auto_create(
    session: Session, llm: JsonLLM, cfg: Settings, *, now: datetime | None = None,
) -> ContentItem | None:
    """Turn the best waiting idea into a video, at most `auto_create_per_day` a day.

    The item still stops at "waiting for approval": the owner approves every post. One idea per
    call, so a burst can never spend more than one idea's worth between two checks."""
    now = now or utcnow()
    row = get_settings_row(session)
    limit = row.auto_create_per_day
    if limit <= 0 or row.generation_paused:
        return None
    since = _vn_day_start_utc(now)
    made_today = sum(
        1 for (data,) in session.execute(
            select(ContentItem.pipeline_input).where(ContentItem.created_at >= since))
        if (data or {}).get("auto_created"))
    if made_today >= limit:
        return None
    waiting = session.scalar(select(func.count()).select_from(ContentItem).where(
        ContentItem.status.in_(["PENDING_APPROVAL", "HELD_FOR_HUMAN"]))) or 0
    if waiting >= MAX_WAITING_FOR_OWNER:
        return None
    ranked = session.scalars(
        select(IdeaCandidate).where(
            IdeaCandidate.status == "new", IdeaCandidate.risk_level == AUTO_RISK,
            IdeaCandidate.score >= row.auto_create_min_score,
            IdeaCandidate.expires_at > now)
        .order_by(IdeaCandidate.score.desc(), IdeaCandidate.created_at).limit(30)).all()
    per_scan: dict[uuid.UUID, int] = {}
    cand = None
    for c in ranked:
        # At most `auto_create_per_scan` ideas from the same scan become videos on their own.
        if c.scan_run_id is not None:
            if c.scan_run_id not in per_scan:
                per_scan[c.scan_run_id] = session.scalar(
                    select(func.count()).select_from(ContentItem).where(
                        ContentItem.pipeline_input["auto_scan_run"].astext
                        == str(c.scan_run_id))) or 0
            if per_scan[c.scan_run_id] >= row.auto_create_per_scan:
                continue
        cand = c
        break
    if cand is None:
        return None
    cand_id, scan_run_id = cand.id, cand.scan_run_id
    try:
        cand = await propose(session, llm, cfg, cand_id, "daily_life")
        brief = cand.proposals[0]["brief"]
        item = content_pipeline.start(session, None, lane="daily_life", brief=brief)
    except (ApiError, llm_service.LlmInvalidOutput, llm_service.LlmRefused, KeyError, IndexError):
        # One bad idea must not be retried forever (each try costs money): set it aside.
        session.rollback()
        cand = session.get(IdeaCandidate, cand_id)
        if cand is not None:
            cand.status = "dismissed"
            record_audit(session, actor="system", action="ideas.auto_failed",
                         entity_type="idea_candidate", entity_id=str(cand_id))
            session.commit()
        return None
    item.pipeline_input = {
        **(item.pipeline_input or {}), "auto_created": True,
        **({"auto_scan_run": str(scan_run_id)} if scan_run_id else {})}
    cand = session.get(IdeaCandidate, cand_id)
    cand.status, cand.lane, cand.content_item_id = "created", "daily_life", item.id
    record_audit(session, actor="system", action="ideas.auto_create",
                 entity_type="idea_candidate", entity_id=str(cand_id),
                 metadata={"content_item_id": str(item.id)})
    session.commit()
    return item


def dismiss(session: Session, owner: OwnerAccount, candidate_id: uuid.UUID) -> None:
    cand = _active_candidate(session, candidate_id)
    cand.status = "dismissed"
    record_audit(session, actor=f"owner:{owner.id}", action="ideas.dismiss",
                 entity_type="idea_candidate", entity_id=str(cand.id))
    session.commit()


# ------------------------------------------------------------------------------ helpers
def _read_prompt(cfg: Settings, name: str) -> str:
    return (Path(cfg.prompts_dir) / f"{name}.md").read_text(encoding="utf-8")


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
