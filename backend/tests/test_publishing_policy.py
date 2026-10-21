from datetime import UTC, datetime, timedelta

import pytest

from app.services.publishing_policy import PublishFacts, can_publish

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def facts(**overrides):
    base = dict(
        approved=True,
        approved_content_hash="h1",
        reviewed_content_hash="h1",
        video_content_hash="h1",
        safety_decision="allow",
        review_expires_at=NOW + timedelta(hours=1),
        kill_switch_enabled=False,
        already_published=False,
        scheduled_for=NOW - timedelta(minutes=1),
        max_lateness_seconds=1800,
        publishes_in_window=0,
        max_publishes_per_window=3,
        budget_ok=True,
        token_valid=True,
        token_checked_at=NOW - timedelta(minutes=5),
        max_token_age_seconds=3600,
        record_status="READY_TO_PUBLISH",
        revision_is_current=True,
        topic_fresh_until=None,
        media_ready=True,
        copyright_clear=True,
        human_review_required=False,
        human_review_complete=False,
    )
    base.update(overrides)
    return PublishFacts(**base)


def test_allows_when_every_condition_holds():
    assert can_publish(facts(), now=NOW) == (True, "allowed")


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (dict(kill_switch_enabled=True), "publishing_kill_switch_enabled"),
        (dict(already_published=True), "already_published"),
        (dict(approved=False), "content_not_approved"),
        (dict(approved_content_hash=None), "content_not_approved"),
        (dict(video_content_hash="h2"), "approval_does_not_match_media"),
        (dict(video_content_hash=None), "approval_does_not_match_media"),
        (dict(safety_decision=None), "safety_review_missing_or_not_allowed"),
        (dict(safety_decision="hold_for_human"), "safety_review_missing_or_not_allowed"),
        (dict(safety_decision="block"), "safety_review_missing_or_not_allowed"),
        (dict(reviewed_content_hash="old"), "safety_review_does_not_match_media"),
        (dict(review_expires_at=NOW - timedelta(seconds=1)), "safety_review_missing_or_stale"),
        (dict(review_expires_at=None), "safety_review_missing_or_stale"),
        (dict(review_expires_at=NOW), "safety_review_missing_or_stale"),
        (dict(human_review_required=True), "human_review_required"),
        (dict(scheduled_for=None), "not_due"),
        (dict(scheduled_for=NOW + timedelta(seconds=1)), "not_due"),
        (dict(scheduled_for=NOW - timedelta(hours=2)), "skipped_late"),
        (dict(publishes_in_window=3), "publish_rate_limit"),
        (dict(budget_ok=False), "budget_exceeded"),
        (dict(token_valid=False), "token_invalid"),
        (dict(token_checked_at=None), "token_health_stale"),
        (dict(token_checked_at=NOW - timedelta(hours=2)), "token_health_stale"),
        (dict(record_status="PREPARING"), "record_not_ready"),
        (dict(record_status="BLOCKED"), "record_not_ready"),
        (dict(revision_is_current=False), "revision_superseded"),
        (dict(topic_fresh_until=NOW), "topic_stale"),
        (dict(topic_fresh_until=NOW - timedelta(hours=1)), "topic_stale"),
        (dict(media_ready=False), "media_not_ready"),
        (dict(copyright_clear=False), "copyright_check_not_clear"),
    ],
)
def test_blocks_with_reason(override, reason):
    assert can_publish(facts(**override), now=NOW) == (False, reason)


def test_human_review_complete_allows():
    result = can_publish(
        facts(human_review_required=True, human_review_complete=True), now=NOW
    )
    assert result == (True, "allowed")


def test_naive_datetime_is_rejected_not_silently_accepted():
    with pytest.raises(ValueError):
        can_publish(facts(review_expires_at=datetime(2026, 1, 1, 13, 0)), now=NOW)
    with pytest.raises(ValueError):
        can_publish(facts(), now=datetime(2026, 1, 1, 12, 0))


def test_kill_switch_wins_over_everything():
    assert can_publish(facts(kill_switch_enabled=True, already_published=True), now=NOW) == (
        False,
        "publishing_kill_switch_enabled",
    )


def test_topic_fresh_until_in_future_is_allowed():
    result = can_publish(facts(topic_fresh_until=NOW + timedelta(hours=3)), now=NOW)
    assert result == (True, "allowed")
