"""Table-driven state machines (source of truth: docs/workflow.md).

Services must change a status only through `check_transition`; an invalid change raises
`InvalidTransition`, which the API maps to 409 `invalid_state_transition`.
"""

from enum import StrEnum

from app.core.errors import ApiError


class InvalidTransition(Exception):
    def __init__(self, machine: str, source: str, target: str) -> None:
        super().__init__(f"{machine}: {source} -> {target} is not allowed")
        self.machine, self.source, self.target = machine, source, target

    def to_api_error(self) -> ApiError:
        return ApiError(409, "invalid_state_transition", str(self))


class ContentState(StrEnum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    HELD_FOR_HUMAN = "HELD_FOR_HUMAN"
    GENERATING = "GENERATING"
    RENDERING = "RENDERING"
    NEEDS_REWORK = "NEEDS_REWORK"
    QA_PENDING = "QA_PENDING"
    FINAL_SAFETY_PENDING = "FINAL_SAFETY_PENDING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class PublishState(StrEnum):
    NOT_SCHEDULED = "NOT_SCHEDULED"
    SCHEDULED = "SCHEDULED"
    PREPARING = "PREPARING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED_LATE = "SKIPPED_LATE"
    RETRACTION_REQUESTED = "RETRACTION_REQUESTED"
    RETRACTED = "RETRACTED"
    RETRACTION_FAILED = "RETRACTION_FAILED"


C, J, P = ContentState, JobState, PublishState

CONTENT_TRANSITIONS: dict[ContentState, frozenset[ContentState]] = {
    C.DRAFT: frozenset({C.ANALYZING}),
    C.ANALYZING: frozenset({C.READY_FOR_GENERATION, C.HELD_FOR_HUMAN, C.REJECTED}),
    # A hold can come from the intake gate or the final gate; the service must check which one
    # before choosing READY_FOR_GENERATION (intake) or PENDING_APPROVAL (final).
    C.HELD_FOR_HUMAN: frozenset({C.READY_FOR_GENERATION, C.PENDING_APPROVAL, C.REJECTED}),
    C.READY_FOR_GENERATION: frozenset({C.GENERATING}),
    C.GENERATING: frozenset({C.RENDERING, C.NEEDS_REWORK}),
    C.RENDERING: frozenset({C.QA_PENDING, C.NEEDS_REWORK}),
    C.QA_PENDING: frozenset({C.FINAL_SAFETY_PENDING, C.NEEDS_REWORK, C.REJECTED}),
    C.NEEDS_REWORK: frozenset({C.GENERATING, C.REJECTED}),
    C.FINAL_SAFETY_PENDING: frozenset({C.PENDING_APPROVAL, C.HELD_FOR_HUMAN, C.BLOCKED}),
    C.PENDING_APPROVAL: frozenset({C.APPROVED, C.REJECTED, C.NEEDS_REWORK}),
    C.APPROVED: frozenset({C.SCHEDULED}),
    C.SCHEDULED: frozenset({C.APPROVED, C.PUBLISHED}),
    C.PUBLISHED: frozenset({C.ARCHIVED}),
}
CONTENT_TERMINAL = frozenset({C.REJECTED, C.BLOCKED, C.SUPERSEDED, C.ARCHIVED})

JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    J.QUEUED: frozenset({J.RUNNING, J.SKIPPED, J.CANCEL_REQUESTED}),
    # RUNNING -> QUEUED only when a lease expires after a worker crash.
    J.RUNNING: frozenset({J.SUCCEEDED, J.RETRY_WAIT, J.FAILED, J.CANCEL_REQUESTED, J.QUEUED}),
    J.RETRY_WAIT: frozenset({J.QUEUED, J.CANCEL_REQUESTED}),
    J.CANCEL_REQUESTED: frozenset({J.CANCELLED}),
}
JOB_TERMINAL = frozenset({J.SUCCEEDED, J.FAILED, J.CANCELLED, J.SKIPPED})

PUBLISH_TRANSITIONS: dict[PublishState, frozenset[PublishState]] = {
    P.NOT_SCHEDULED: frozenset({P.SCHEDULED}),
    P.SCHEDULED: frozenset({P.PREPARING, P.BLOCKED, P.CANCELLED, P.SKIPPED_LATE}),
    P.PREPARING: frozenset({P.READY_TO_PUBLISH, P.FAILED, P.EXPIRED, P.BLOCKED, P.CANCELLED}),
    P.EXPIRED: frozenset({P.PREPARING}),
    P.READY_TO_PUBLISH: frozenset({P.PUBLISHING, P.BLOCKED, P.CANCELLED, P.EXPIRED}),
    # PUBLISHING -> FAILED only for a definite provider rejection; ambiguity is UNKNOWN_RESULT.
    P.PUBLISHING: frozenset({P.PUBLISHED, P.UNKNOWN_RESULT, P.FAILED}),
    # UNKNOWN_RESULT -> PUBLISHING only after reconciliation proves FINISH had no effect.
    P.UNKNOWN_RESULT: frozenset({P.PUBLISHED, P.FAILED, P.PUBLISHING}),
    P.BLOCKED: frozenset({P.SCHEDULED}),
    P.PUBLISHED: frozenset({P.RETRACTION_REQUESTED}),
    P.RETRACTION_REQUESTED: frozenset({P.RETRACTED, P.RETRACTION_FAILED}),
    # After a failed API retraction the owner can retry or record a manual retraction.
    P.RETRACTION_FAILED: frozenset({P.RETRACTION_REQUESTED, P.RETRACTED}),
}
PUBLISH_TERMINAL = frozenset({P.FAILED, P.CANCELLED, P.SKIPPED_LATE, P.RETRACTED})


def _check(machine, table, terminal, source, target, *, allow_supersede=False) -> None:
    src, dst = str(source), str(target)
    if source in terminal:
        raise InvalidTransition(machine, src, dst)
    if allow_supersede and target == ContentState.SUPERSEDED:
        return
    if target not in table.get(source, frozenset()):
        raise InvalidTransition(machine, src, dst)


def check_content_transition(source: ContentState, target: ContentState) -> None:
    _check("content", CONTENT_TRANSITIONS, CONTENT_TERMINAL, source, target, allow_supersede=True)


def check_job_transition(source: JobState, target: JobState) -> None:
    _check("job", JOB_TRANSITIONS, JOB_TERMINAL, source, target)


def check_publish_transition(source: PublishState, target: PublishState) -> None:
    _check("publishing", PUBLISH_TRANSITIONS, PUBLISH_TERMINAL, source, target)
