from itertools import product

import pytest

from app.services import state_machine as sm

MACHINES = [
    ("content", sm.ContentState, sm.CONTENT_TRANSITIONS, sm.CONTENT_TERMINAL,
     sm.check_content_transition, sm.ContentState.DRAFT),
    ("job", sm.JobState, sm.JOB_TRANSITIONS, sm.JOB_TERMINAL,
     sm.check_job_transition, sm.JobState.QUEUED),
    ("publishing", sm.PublishState, sm.PUBLISH_TRANSITIONS, sm.PUBLISH_TERMINAL,
     sm.check_publish_transition, sm.PublishState.NOT_SCHEDULED),
]


def allowed(machine, source, target, table, terminal):
    if source in terminal:
        return False
    if machine == "content" and target == sm.ContentState.SUPERSEDED:
        return True  # any non-terminal state can be superseded by a newer revision
    return target in table.get(source, frozenset())


@pytest.mark.parametrize(("name", "enum", "table", "terminal", "check", "start"), MACHINES)
def test_every_state_pair_matches_the_table(name, enum, table, terminal, check, start):
    for source, target in product(enum, enum):
        if allowed(name, source, target, table, terminal):
            check(source, target)
        else:
            with pytest.raises(sm.InvalidTransition):
                check(source, target)


@pytest.mark.parametrize(("name", "enum", "table", "terminal", "check", "start"), MACHINES)
def test_terminal_states_have_no_exits_and_others_do(name, enum, table, terminal, check, start):
    for state in enum:
        exits = table.get(state, frozenset())
        if state in terminal:
            assert not exits, f"{name}: terminal {state} has exits"
        else:
            assert exits, f"{name}: non-terminal {state} is a dead end"


@pytest.mark.parametrize(("name", "enum", "table", "terminal", "check", "start"), MACHINES)
def test_every_state_is_reachable_from_start(name, enum, table, terminal, check, start):
    seen, stack = {start}, [start]
    while stack:
        for nxt in table.get(stack.pop(), frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    if name == "content":
        seen.add(sm.ContentState.SUPERSEDED)  # reachable through the supersede rule
    assert set(enum) <= seen, f"{name}: unreachable {set(enum) - seen}"


@pytest.mark.parametrize(("name", "enum", "table", "terminal", "check", "start"), MACHINES)
def test_table_targets_are_valid_states(name, enum, table, terminal, check, start):
    for source, targets in table.items():
        assert source in enum and set(targets) <= set(enum)


def test_publish_safety_edges():
    P = sm.PublishState
    # Publishing needs a prepared video; it cannot skip PREPARING or leave PUBLISHED backwards.
    with pytest.raises(sm.InvalidTransition):
        sm.check_publish_transition(P.SCHEDULED, P.PUBLISHING)
    with pytest.raises(sm.InvalidTransition):
        sm.check_publish_transition(P.PUBLISHED, P.SCHEDULED)
    with pytest.raises(sm.InvalidTransition):
        sm.check_publish_transition(P.PUBLISHED, P.PUBLISHING)
    # An ambiguous FINISH can never be recorded as a clean failure by skipping reconciliation.
    sm.check_publish_transition(P.PUBLISHING, P.UNKNOWN_RESULT)
    sm.check_publish_transition(P.UNKNOWN_RESULT, P.PUBLISHED)
    sm.check_publish_transition(P.UNKNOWN_RESULT, P.FAILED)
    # A retracted post stays retracted.
    with pytest.raises(sm.InvalidTransition):
        sm.check_publish_transition(P.RETRACTED, P.PUBLISHED)


def test_content_cannot_skip_the_safety_gates():
    C = sm.ContentState
    for target in (C.APPROVED, C.SCHEDULED, C.PUBLISHED):
        with pytest.raises(sm.InvalidTransition):
            sm.check_content_transition(C.QA_PENDING, target)
        with pytest.raises(sm.InvalidTransition):
            sm.check_content_transition(C.FINAL_SAFETY_PENDING, target)
    # Blocked or rejected content stays that way until a new revision exists.
    with pytest.raises(sm.InvalidTransition):
        sm.check_content_transition(C.BLOCKED, C.PENDING_APPROVAL)
    with pytest.raises(sm.InvalidTransition):
        sm.check_content_transition(C.REJECTED, C.GENERATING)


def test_invalid_transition_maps_to_409():
    err = sm.InvalidTransition("content", "DRAFT", "PUBLISHED").to_api_error()
    assert err.status_code == 409 and err.code == "invalid_state_transition"
