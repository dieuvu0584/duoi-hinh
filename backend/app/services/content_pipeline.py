"""Content pipeline: idea -> concept -> image -> narration -> videos -> QA -> safety -> approval.

One call to `run_stage` performs exactly ONE stage chosen from the content item's status, so the
whole thing is resumable after a crash or a budget stop: re-running a stage never pays twice
(images, audio and checks are looked up by prompt/content hash before anything is bought).

    DRAFT -> analyze -> READY_FOR_GENERATION | HELD_FOR_HUMAN | REJECTED
    READY_FOR_GENERATION / NEEDS_REWORK -> generate (image, composition, narration) -> RENDERING
    RENDERING -> render both videos -> QA_PENDING
    QA_PENDING -> quality review of keyframes -> FINAL_SAFETY_PENDING | NEEDS_REWORK | REJECTED
    FINAL_SAFETY_PENDING -> safety review -> PENDING_APPROVAL | HELD_FOR_HUMAN | BLOCKED

The owner then approves in the app; nothing here can publish. The backend decides everything that
matters (answer never changed, leak and word-list checks, thresholds, hard-failure override, human
review rule, regeneration limit); the models only measure and advise.
"""

import asyncio
import base64
import copy
import json
import logging
import secrets
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import (
    AudioAsset,
    CompositionCheck,
    ContentItem,
    ContentRevision,
    ImageAsset,
    OwnerAccount,
    VideoAsset,
)
from app.providers.contracts import ImageProvider, JsonLLM, SpeechProvider, VisionProvider
from app.providers.errors import ProviderError
from app.services import (
    composition_service,
    content_service,
    image_service,
    llm_service,
    narration_service,
    render_service,
)
from app.services.audit import record_audit
from app.services.composition import Box
from app.services.safety_rules import _fold, leaks_answer
from app.services.settings_service import effective_voice_id, get_settings_row
from app.services.state_machine import ContentState as CS

log = logging.getLogger(__name__)

PASS_SCORE = 80
MAX_CONCEPT_REPAIRS = 1  # automatic redesigns of a concept that cannot be drawn
REJECT_SCORE = 65
TEMPLATE_ID = "rendition-v1"
WAITING = {CS.PENDING_APPROVAL, CS.HELD_FOR_HUMAN, CS.REJECTED, CS.BLOCKED, CS.APPROVED,
           CS.SCHEDULED, CS.PUBLISHED, CS.ARCHIVED, CS.SUPERSEDED}


@dataclass
class Prompts:
    analyze_topic: str
    create_image_prompt: str
    quality_review: str
    safety_review: str


def load_prompts(directory: str | Path) -> Prompts:
    d = Path(directory)
    return Prompts(*(
        (d / f"{name}.md").read_text(encoding="utf-8")
        for name in ("analyze_topic", "create_image_prompt", "quality_review", "safety_review")))


def extract_keyframes(video: Path, *, max_side: int = 768) -> list[bytes]:
    """Three JPEG frames (about 15%, 50%, 90% of the video) for the vision reviews."""
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    duration = float(info)
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, fraction in enumerate((0.15, 0.5, 0.9)):
            out = Path(tmp) / f"f{i}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{duration * fraction:.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", f"scale='min({max_side},iw)':-2", "-q:v", "4", str(out)],
                capture_output=True, check=True, timeout=120)
            frames.append(out.read_bytes())
    return frames


@dataclass
class PipelineDeps:
    cfg: Settings
    llm: JsonLLM
    vision: VisionProvider
    images: ImageProvider
    speech: SpeechProvider
    prompts: Prompts
    render_both: Callable[..., dict[str, VideoAsset]] = render_service.render_both
    keyframes: Callable[[Path], list[bytes]] = extract_keyframes


@dataclass
class StageOutcome:
    stage: str
    status: str
    note: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- helpers
def _item(session: Session, item_id: uuid.UUID) -> ContentItem:
    item = session.get(ContentItem, item_id, populate_existing=True)
    if item is None:
        raise ApiError(404, "not_found", "Content not found")
    return item


def _hold(session: Session, item: ContentItem, target: CS, reason: str, stage: str) -> StageOutcome:
    """Move to a waiting/terminal state and remember why (audit + pipeline_input.notes)."""
    content_service.transition_content(session, item, target, actor="system", reason=reason)
    data = copy.deepcopy(item.pipeline_input or {})
    data.setdefault("notes", []).append({"stage": stage, "reason": reason})
    item.pipeline_input = data
    session.commit()
    return StageOutcome(stage, target.value, reason)


def _blacklisted(cfg: Settings, *texts: str | None) -> list[str]:
    words = [w.strip() for w in cfg.blacklist_words.split(",") if w.strip()]
    haystack = f" {_fold(' '.join(t for t in texts if t))} "
    return [w for w in words if f" {_fold(w)} " in haystack]


def _clue_names(clue_mapping: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for clue in clue_mapping[:8]:
        text = str(clue.get("clue_id") or clue.get("visual_clue") or "").strip()[:80]
        if text and text not in names:
            names.append(text)
    return names


def _png_block(png: bytes) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(png).decode("ascii")}}


def _revision(session: Session, item: ContentItem) -> ContentRevision:
    return content_service.current_revision(session, item)


def _text_fields(rev: ContentRevision) -> dict[str, Any]:
    return {"answer_text": rev.answer_text, "clue_mapping": rev.clue_mapping,
            "caption": rev.caption, "hashtags": rev.hashtags, "overlay_text": rev.overlay_text,
            "narration_text": rev.narration_text, "voice_id": rev.voice_id,
            "is_ai_generated": rev.is_ai_generated, "template_id": rev.template_id,
            "media_hash": rev.media_hash, "audio_hash": rev.audio_hash}


# ----------------------------------------------------------------------------- start
def start(
    session: Session, owner: OwnerAccount | None, *, lane: str, brief: str,
    answer: str | None = None, instructions: str | None = None,
) -> ContentItem:
    """Create the item (DRAFT). For a rebus the owner-given answer is the verified answer.
    `instructions` are the owner's specific requests for the AI (e.g. when recreating a video)."""
    if lane not in {"daily_life", "rebus", "news"}:
        raise ApiError(422, "bad_lane", "Unknown lane")
    if not brief.strip() or len(brief) > 2000:
        raise ApiError(422, "bad_brief", "The brief is empty or too long")
    if lane == "rebus" and not (answer and answer.strip()):
        raise ApiError(422, "answer_required", "A rebus needs the owner's answer")
    item = content_service.create_content(
        session, owner, lane=lane, revision={"answer_text": answer if lane == "rebus" else None},
        fresh_until=None)
    if instructions and len(instructions) > 1500:
        raise ApiError(422, "bad_instructions", "The instructions are too long")
    item.pipeline_input = {"brief": brief.strip(),
                           "answer": answer.strip() if answer else None, "notes": [],
                           "owner_instructions": (instructions or "").strip() or None}
    session.commit()
    return item


# ----------------------------------------------------------------------------- stage: analyze
async def _analyze(session: Session, deps: PipelineDeps, item: ContentItem) -> StageOutcome:
    cfg = deps.cfg
    # No narration voice configured (admin setting or env): stop before paying for anything.
    voice = effective_voice_id(get_settings_row(session), cfg)
    if not voice:
        return StageOutcome("analyze", item.status, "waiting: voice_not_configured")
    data = copy.deepcopy(item.pipeline_input or {})
    payload = {"lane": item.lane, "brief": data.get("brief"), "answer": data.get("answer")}
    if data.get("owner_instructions"):
        payload["owner_instructions"] = data["owner_instructions"]
    feedback = data.pop("analysis_feedback", None)
    if feedback:  # a redesign after the image model could not draw the first concept
        payload["feedback"] = feedback
    try:
        analysis = await llm_service.call_json(
            session, deps.llm, cfg, purpose="analyze_topic",
            system_prompt=deps.prompts.analyze_topic,
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            schema_name="analyze_topic", entity_type="content_item", entity_id=str(item.id),
            schema_dir=cfg.schemas_dir)
    except llm_service.LlmRefused:
        return _hold_from_draft(session, item, CS.HELD_FOR_HUMAN, "llm_refused", "analyze")
    except llm_service.LlmInvalidOutput:
        return _hold_from_draft(session, item, CS.HELD_FOR_HUMAN, "llm_invalid_output", "analyze")

    answer = data.get("answer") if item.lane == "rebus" else analysis["answer_or_message"]
    problems: list[str] = []
    if item.lane == "rebus" and _fold(analysis["answer_or_message"]) != _fold(answer or ""):
        problems.append("answer_changed_by_model")  # the answer is frozen: a human decides
    if item.lane == "rebus" and leaks_answer(
            answer, analysis["caption"], analysis["overlay_text"], analysis["narration_text"],
            " ".join(analysis["hashtags"])):
        problems.append("answer_leak")
    hits = _blacklisted(cfg, analysis["caption"], analysis["overlay_text"],
                        analysis["narration_text"], " ".join(analysis["hashtags"]))
    if hits:
        problems.append("blacklisted_words")

    risk, categories = analysis["risk_level"], analysis["sensitive_categories"]
    if item.status == CS.DRAFT.value:
        content_service.transition_content(session, item, CS.ANALYZING, actor="system")
    data["analysis"] = analysis
    item.pipeline_input = data
    if "answer_leak" in problems:
        # The leaking text is kept only in pipeline_input for the owner to read; no revision (and
        # therefore nothing that could ever be rendered or published) is created from it.
        return _hold(session, item, CS.HELD_FOR_HUMAN, ",".join(problems), "analyze")
    revision = content_service.add_revision(session, None, item, {
        "answer_text": answer, "clue_mapping": analysis["clue_mapping"],
        "caption": analysis["caption"], "hashtags": analysis["hashtags"],
        "overlay_text": render_service.strip_unrenderable(analysis["overlay_text"]),
        "narration_text": analysis["narration_text"],
        "voice_id": voice, "is_ai_generated": True, "template_id": TEMPLATE_ID})
    decision = {"proceed": "allow", "hold_for_human": "hold_for_human",
                "reject": "block"}[analysis["decision"]]
    content_service.record_safety_review(
        session, item=item, revision=revision, gate="intake", decision=decision,
        risk_level=risk, categories=categories, reviewer=f"llm:{cfg.claude_model}",
        policy_version="intake-1", findings=[{"category": c, "description": c} for c in categories])

    reason: str | None = None
    target = CS.READY_FOR_GENERATION
    if analysis["decision"] == "reject":
        target, reason = CS.REJECTED, "analysis_rejected: " + "; ".join(analysis["reasons"])[:200]
    elif analysis["decision"] == "hold_for_human":
        target, reason = CS.HELD_FOR_HUMAN, "analysis_hold: " + "; ".join(analysis["reasons"])[:200]
    elif problems:
        target, reason = CS.HELD_FOR_HUMAN, ",".join(problems)
    elif risk in {"high", "critical"}:
        target, reason = CS.HELD_FOR_HUMAN, f"risk_{risk}"
    if target == CS.READY_FOR_GENERATION:
        content_service.transition_content(session, item, target, actor="system")
        session.commit()
        return StageOutcome("analyze", target.value)
    return _hold(session, item, target, reason or "held", "analyze")


def _hold_from_draft(session: Session, item: ContentItem, target: CS, reason: str,
                     stage: str) -> StageOutcome:
    if item.status == CS.DRAFT.value:
        content_service.transition_content(session, item, CS.ANALYZING, actor="system")
    return _hold(session, item, target, reason, stage)


# ----------------------------------------------------------------------------- stage: generate
async def _generate(session: Session, deps: PipelineDeps, item: ContentItem) -> StageOutcome:
    cfg = deps.cfg
    settings_row = get_settings_row(session)
    attempts = session.scalar(select(func.count()).select_from(ImageAsset).where(
        ImageAsset.content_item_id == item.id)) or 0
    if item.status == CS.NEEDS_REWORK.value and attempts >= settings_row.max_image_regenerations:
        return _hold(session, item, CS.REJECTED, "regeneration_limit", "generate")
    resumed = item.status == CS.GENERATING.value
    if not resumed:
        content_service.transition_content(session, item, CS.GENERATING, actor="system")
        session.commit()
    revision = _revision(session, item)
    analysis = (item.pipeline_input or {}).get("analysis") or {}
    stored = (item.pipeline_input or {}).get("image_prompt")
    media_bound = bool(revision.media_hash and revision.audio_hash)
    if resumed and media_bound and stored and stored.get("image_id") and not stored.get("redo"):
        bought = session.get(ImageAsset, uuid.UUID(stored["image_id"]))
        if bought is not None and bought.sha256 == revision.media_hash:
            # This stage already finished (assets are bound to the current revision) but the status
            # did not move on: continue to rendering instead of buying anything again.
            content_service.transition_content(session, item, CS.RENDERING, actor="system")
            session.commit()
            return StageOutcome("generate", CS.RENDERING.value, "already_generated")

    # 1) the image prompt (looked up by revision so a resume does not pay twice)
    if not stored or stored.get("revision_id") != str(revision.id) or stored.get("redo"):
        spec = {"lane": item.lane, "answer_or_message": revision.answer_text,
                "clue_mapping": revision.clue_mapping,
                "intended_viewer_inference": analysis.get("intended_viewer_inference"),
                "ambiguity_risks": analysis.get("ambiguity_risks"),
                "previous_qa_issues": (item.pipeline_input or {}).get("last_qa_issues", [])}
        if (item.pipeline_input or {}).get("owner_instructions"):
            spec["owner_instructions"] = item.pipeline_input["owner_instructions"]
        try:
            image_prompt = await llm_service.call_json(
                session, deps.llm, cfg, purpose="image_prompt",
                system_prompt=deps.prompts.create_image_prompt,
                content=[{"type": "text", "text": json.dumps(spec, ensure_ascii=False)}],
                schema_name="image_prompt", entity_type="content_item", entity_id=str(item.id),
                schema_dir=cfg.schemas_dir)
        except (llm_service.LlmRefused, llm_service.LlmInvalidOutput) as exc:
            return _hold(session, item, CS.HELD_FOR_HUMAN, type(exc).__name__, "generate")
        if image_prompt["status"] != "ok":
            why = "; ".join(image_prompt["revision_reasons"])
            data = copy.deepcopy(item.pipeline_input or {})
            if data.get("concept_repairs", 0) < MAX_CONCEPT_REPAIRS:
                # The concept cannot be drawn as one picture. Redesign it ONCE automatically (a
                # small text-only call) instead of stopping and asking the owner to simplify it.
                data["concept_repairs"] = data.get("concept_repairs", 0) + 1
                data["analysis_feedback"] = why[:600]
                item.pipeline_input = data
                content_service.transition_content(session, item, CS.ANALYZING, actor="system",
                                                   reason="concept_repair")
                session.commit()
                return StageOutcome("generate", CS.ANALYZING.value, "concept_repair",
                                    {"why": why[:200]})
            return _hold(session, item, CS.HELD_FOR_HUMAN,
                         "image_prompt_needs_revision: " + why[:200], "generate")
        stored = {"revision_id": str(revision.id), "flux_prompt": image_prompt["flux_prompt"],
                  "visual_explanation": image_prompt["visual_explanation"], "redo": False,
                  "image_id": None}
        data = copy.deepcopy(item.pipeline_input or {})
        data["image_prompt"] = stored
        item.pipeline_input = data
        session.commit()

    # 2) the master image: the one already bought for THIS stored prompt (resume after a crash),
    # or a new one. A redo after QA always buys a new image (fresh seed), never the rejected one.
    image = None
    if stored.get("image_id"):
        image = session.get(ImageAsset, uuid.UUID(stored["image_id"]))
    if image is None:
        image = await image_service.generate_master(
            session, deps.images, cfg, prompt=stored["flux_prompt"], size=1920,
            seed=secrets.randbelow(2**31) if attempts > 0 else None, content_item_id=item.id)
        data = copy.deepcopy(item.pipeline_input or {})
        data["image_prompt"] = {**stored, "image_id": str(image.id)}
        item.pipeline_input = data
        session.commit()

    # 3) composition check on that image (once per image)
    check = session.scalar(select(CompositionCheck).where(
        CompositionCheck.image_sha256 == image.sha256))
    if check is None:
        _, check = await composition_service.check_composition(
            session, deps.vision, cfg, master_ref=image.storage_ref,
            clues=_clue_names(revision.clue_mapping or []))

    # 4) narration (content-addressed: a repeat is free)
    audio = await narration_service.get_or_create_narration(
        session, deps.speech, cfg, text=revision.narration_text or "",
        voice_id=revision.voice_id or "", model_id=cfg.elevenlabs_model_id,
        license_note="owner-selected ElevenLabs voice (commercial plan)")

    # 5) a new revision that binds the media by hash (approval will name this hash)
    fields = _text_fields(revision)
    fields.update(media_hash=image.sha256, audio_hash=audio.sha256)
    content_service.add_revision(session, None, item, fields)
    content_service.transition_content(session, item, CS.RENDERING, actor="system")
    session.commit()
    return StageOutcome("generate", CS.RENDERING.value, details={
        "image": image.storage_ref, "audio": audio.storage_ref, "modes": check.modes})


# ----------------------------------------------------------------------------- stage: render
async def _render(session: Session, deps: PipelineDeps, item: ContentItem) -> StageOutcome:
    revision = _revision(session, item)
    image = session.scalar(select(ImageAsset).where(
        ImageAsset.content_item_id == item.id, ImageAsset.sha256 == revision.media_hash))
    audio = session.scalar(select(AudioAsset).where(AudioAsset.sha256 == revision.audio_hash))
    check = session.scalar(select(CompositionCheck).where(
        CompositionCheck.image_sha256 == revision.media_hash))
    if image is None or audio is None or check is None:
        return _hold(session, item, CS.HELD_FOR_HUMAN, "assets_missing", "render")
    boxes = [Box(**{k: b[k] for k in ("clue_id", "x0", "y0", "x1", "y1")}) for b in check.boxes]
    try:
        # ffmpeg takes minutes: run it in a thread so the worker's publishing loop, token checks
        # and the API are not frozen while a video renders.
        videos = await asyncio.to_thread(
            deps.render_both, session, deps.cfg, master_ref=image.storage_ref, audio=audio,
            caption=revision.overlay_text or revision.caption or "", boxes=boxes,
            revision_id=revision.id, expected=check.expected_clues)
    except render_service.RenderError as exc:
        return _hold(session, item, CS.HELD_FOR_HUMAN, f"render_failed: {str(exc)[:160]}", "render")
    content_service.transition_content(session, item, CS.QA_PENDING, actor="system")
    session.commit()
    return StageOutcome("render", CS.QA_PENDING.value,
                        details={t: v.mode for t, v in videos.items()})


def _videos_for(session: Session, revision: ContentRevision) -> list[VideoAsset]:
    return list(session.scalars(select(VideoAsset).where(
        VideoAsset.revision_id == revision.id, VideoAsset.status == "valid")
        .order_by(VideoAsset.target)))


def _keyframe_blocks(session: Session, deps: PipelineDeps, revision: ContentRevision,
                     per_video: int = 3) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for video in _videos_for(session, revision):
        frames = deps.keyframes(Path(deps.cfg.media_dir) / video.storage_ref)[:per_video]
        for i, frame in enumerate(frames):
            blocks.append({"type": "text", "text": f"Keyframe {i + 1} of {video.target} "
                                                   f"(mode {video.mode})"})
            blocks.append(_png_block(frame))
    return blocks


# ----------------------------------------------------------------------------- stage: QA
async def _qa(session: Session, deps: PipelineDeps, item: ContentItem) -> StageOutcome:
    cfg = deps.cfg
    revision = _revision(session, item)
    analysis = (item.pipeline_input or {}).get("analysis") or {}
    stored = (item.pipeline_input or {}).get("image_prompt") or {}
    spec = {"lane": item.lane, "answer_or_message": revision.answer_text,
            "clue_mapping": revision.clue_mapping,
            "visual_explanation": stored.get("visual_explanation"),
            "overlay_text": revision.overlay_text, "narration_text": revision.narration_text,
            "intended_viewer_inference": analysis.get("intended_viewer_inference")}
    content = [{"type": "text", "text": json.dumps(spec, ensure_ascii=False)},
               *await asyncio.to_thread(_keyframe_blocks, session, deps, revision)]
    try:
        qa = await llm_service.call_json(
            session, deps.llm, cfg, purpose="quality_review",
            system_prompt=deps.prompts.quality_review, content=content,
            schema_name="quality_review", entity_type="content_item", entity_id=str(item.id),
            schema_dir=cfg.schemas_dir, est_input_tokens=12000)
    except (llm_service.LlmRefused, llm_service.LlmInvalidOutput) as exc:
        return _hold(session, item, CS.HELD_FOR_HUMAN, type(exc).__name__, "qa")

    data = copy.deepcopy(item.pipeline_input or {})
    data.setdefault("qa", []).append({k: qa[k] for k in (
        "score_total", "recommendation", "hard_failures", "missing_clues",
        "crop_or_text_issues", "explanation")})
    issues = [*qa["hard_failures"], *qa["missing_clues"], *qa["crop_or_text_issues"]]
    data["last_qa_issues"] = issues[:8]
    item.pipeline_input = data

    # The backend enforces the thresholds and the hard-failure override itself.
    passed = (qa["recommendation"] == "pass" and qa["score_total"] >= PASS_SCORE
              and not qa["hard_failures"] and not qa["missing_clues"])
    if passed:
        data = copy.deepcopy(item.pipeline_input or {})
        data.pop("qa_flag", None)  # an earlier attempt's warning no longer applies
        item.pipeline_input = data
        content_service.transition_content(session, item, CS.FINAL_SAFETY_PENDING, actor="system")
        session.commit()
        return StageOutcome("qa", CS.FINAL_SAFETY_PENDING.value,
                            details={"score": qa["score_total"]})
    settings_row = get_settings_row(session)
    attempts = session.scalar(select(func.count()).select_from(ImageAsset).where(
        ImageAsset.content_item_id == item.id)) or 0
    unfixable = qa["recommendation"] == "reject" or qa["score_total"] < REJECT_SCORE
    if unfixable or attempts >= settings_row.max_image_regenerations:
        # The automatic check is advisory (the owner approves every post anyway), so a poor result
        # is shown to the owner WITH the warning instead of being thrown away: the money is spent
        # and the owner can see at once whether it is usable. The final safety review still runs.
        data = copy.deepcopy(item.pipeline_input or {})
        data["qa_flag"] = {"score": qa["score_total"], "recommendation": qa["recommendation"],
                           "reason": "unfixable" if unfixable else "regeneration_limit",
                           "issues": issues[:8]}
        item.pipeline_input = data
        content_service.transition_content(session, item, CS.FINAL_SAFETY_PENDING, actor="system",
                                           reason="qa_flagged")
        session.commit()
        return StageOutcome("qa", CS.FINAL_SAFETY_PENDING.value, "qa_flagged",
                            {"score": qa["score_total"]})
    # fixable: back to generation with the issues passed to the next image prompt
    stored = copy.deepcopy(data.get("image_prompt") or {})
    stored["redo"] = True
    data["image_prompt"] = stored
    item.pipeline_input = data
    content_service.transition_content(session, item, CS.NEEDS_REWORK, actor="system",
                                       reason="qa_revise")
    session.commit()
    return StageOutcome("qa", CS.NEEDS_REWORK.value, "qa_revise", {"issues": issues[:5]})


# ----------------------------------------------------------------------------- stage: safety
async def _final_safety(session: Session, deps: PipelineDeps, item: ContentItem) -> StageOutcome:
    cfg = deps.cfg
    revision = _revision(session, item)
    spec = {"lane": item.lane, "caption": revision.caption, "hashtags": revision.hashtags,
            "overlay_text": revision.overlay_text, "narration_text": revision.narration_text,
            "answer_or_message": revision.answer_text, "clue_mapping": revision.clue_mapping,
            "ai_generated": revision.is_ai_generated}
    content = [{"type": "text", "text": json.dumps(spec, ensure_ascii=False)},
               *await asyncio.to_thread(_keyframe_blocks, session, deps, revision, 1)]
    try:
        review = await llm_service.call_json(
            session, deps.llm, cfg, purpose="safety_review",
            system_prompt=deps.prompts.safety_review, content=content,
            schema_name="safety_review", entity_type="content_item", entity_id=str(item.id),
            schema_dir=cfg.schemas_dir, est_input_tokens=8000)
    except (llm_service.LlmRefused, llm_service.LlmInvalidOutput) as exc:
        return _hold(session, item, CS.HELD_FOR_HUMAN, type(exc).__name__, "safety")

    decision, problems = review["decision"], []
    if item.lane == "rebus" and leaks_answer(revision.answer_text, revision.caption,
                                             revision.overlay_text, revision.narration_text,
                                             " ".join(revision.hashtags or [])):
        decision, problems = "block", ["answer_leak"]
    if _blacklisted(cfg, revision.caption, revision.overlay_text, revision.narration_text,
                    " ".join(revision.hashtags or [])):
        decision, problems = "block", [*problems, "blacklisted_words"]
    record = content_service.record_safety_review(
        session, item=item, revision=revision, gate="final", decision=decision,
        risk_level=review["risk_level"], categories=review["categories"],
        reviewer=f"llm:{cfg.claude_model}", policy_version="final-1", findings=review["findings"])
    session.commit()
    if decision == "allow":
        content_service.transition_content(session, item, CS.PENDING_APPROVAL, actor="system")
        session.commit()
        return StageOutcome("safety", CS.PENDING_APPROVAL.value,
                            details={"human_review_required": record.human_review_required})
    if decision == "block":
        return _hold(session, item, CS.BLOCKED, ",".join(problems) or "safety_block", "safety")
    return _hold(session, item, CS.HELD_FOR_HUMAN, "safety_hold", "safety")


# ----------------------------------------------------------------------------- orchestration
async def run_stage(session: Session, deps: PipelineDeps, item_id: uuid.UUID) -> StageOutcome:
    """Perform the one stage that matches the item's status. Budget stops leave the state intact."""
    item = _item(session, item_id)
    status = CS(item.status)
    try:
        if status in {CS.DRAFT, CS.ANALYZING}:
            return await _analyze(session, deps, item)
        if status in {CS.READY_FOR_GENERATION, CS.NEEDS_REWORK, CS.GENERATING}:
            return await _generate(session, deps, item)
        if status == CS.RENDERING:
            return await _render(session, deps, item)
        if status == CS.QA_PENDING:
            return await _qa(session, deps, item)
        if status == CS.FINAL_SAFETY_PENDING:
            return await _final_safety(session, deps, item)
    except ApiError as exc:
        session.rollback()
        if exc.code.startswith("budget_"):  # not an error: the work resumes when money is available
            record_audit(session, actor="system", action="pipeline.waiting_budget", result="denied",
                         entity_type="content_item", entity_id=str(item.id),
                         metadata={"stage": status.value, "reason": exc.code})
            session.commit()
            return StageOutcome(status.value, item.status, f"waiting: {exc.code}")
        raise
    except ProviderError as exc:
        session.rollback()
        record_audit(session, actor="system", action="pipeline.provider_error", result="error",
                     entity_type="content_item", entity_id=str(item.id),
                     metadata={"stage": status.value, "error": type(exc).__name__})
        session.commit()
        return StageOutcome(status.value, item.status, f"provider_error: {type(exc).__name__}")
    return StageOutcome("none", status.value, "nothing to do")


async def run_until_wait(
    session: Session, deps: PipelineDeps, item_id: uuid.UUID, *, max_steps: int = 12
) -> list[StageOutcome]:
    """Run stages until the item needs a human, is finished, or a stage cannot proceed."""
    outcomes: list[StageOutcome] = []
    for _ in range(max_steps):
        item = _item(session, item_id)
        if CS(item.status) in WAITING:
            break
        outcome = await run_stage(session, deps, item_id)
        outcomes.append(outcome)
        if outcome.note.startswith(("waiting", "provider_error", "nothing")):
            break
    return outcomes
