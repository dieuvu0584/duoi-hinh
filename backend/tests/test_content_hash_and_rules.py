import unicodedata
from datetime import UTC, datetime, timedelta

import pytest

from app.services import safety_rules
from app.services.content_hash import HASH_VERSION, compute_content_hash

BASE = {
    "answer_text": "Bánh chưng",
    "clue_mapping": [{"clue_id": "c1", "maps_to": "bánh"}],
    "caption": "Đoán thử nhé!",
    "hashtags": ["#doan", "#hinh"],
    "media_hash": "a" * 64,
    "is_ai_generated": True,
}


def test_golden_hash_is_stable():
    # If this changes, HASH_VERSION must be bumped: published approvals depend on it.
    assert HASH_VERSION == 1
    assert compute_content_hash(BASE) == (
        "1137f296b6819e860719f50c85e0cc8f0f111359f2b779cc5a2d935beafde34b"
    )


def test_key_order_does_not_matter_but_list_order_does():
    reordered = dict(reversed(list(BASE.items())))
    assert compute_content_hash(reordered) == compute_content_hash(BASE)
    swapped = {**BASE, "hashtags": ["#hinh", "#doan"]}
    assert compute_content_hash(swapped) != compute_content_hash(BASE)


def test_unicode_normalisation_is_irrelevant():
    nfd = {**BASE, "answer_text": unicodedata.normalize("NFD", BASE["answer_text"])}
    assert nfd["answer_text"] != BASE["answer_text"]
    assert compute_content_hash(nfd) == compute_content_hash(BASE)


@pytest.mark.parametrize(
    "change",
    [{"answer_text": "Bánh chưng "}, {"caption": "Đoán thử nhé"}, {"media_hash": "b" * 64},
     {"is_ai_generated": False}, {"overlay_text": "x"}, {"template_id": "t2"}],
)
def test_any_content_change_changes_the_hash(change):
    assert compute_content_hash({**BASE, **change}) != compute_content_hash(BASE)


def test_missing_and_none_are_the_same_and_unknown_fields_are_rejected():
    assert compute_content_hash({**BASE, "overlay_text": None}) == compute_content_hash(BASE)
    with pytest.raises(ValueError, match="not part of the content hash"):
        compute_content_hash({**BASE, "secret_note": "x"})


@pytest.mark.parametrize(
    ("lane", "risk", "cats", "expected"),
    [
        ("daily_life", "low", [], False),
        ("rebus", "medium", [], False),
        ("daily_life", "high", [], True),
        ("daily_life", "critical", [], True),
        ("daily_life", "low", ["children"], True),
        ("rebus", "low", ["politics_elections"], True),
        ("daily_life", "low", ["harmless_topic"], False),
        ("news", "low", [], True),  # every news post needs a human during the pilot
        ("daily_life", "unknown-level", [], True),  # fail closed
    ],
)
def test_human_review_is_derived_deterministically(lane, risk, cats, expected):
    assert safety_rules.requires_human_review(lane, risk, cats) is expected


def test_review_expiry_uses_lane_ttl_capped_by_topic_freshness():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert safety_rules.review_expiry("daily_life", t, None) == t + timedelta(hours=24)
    assert safety_rules.review_expiry("news", t, None) == t + timedelta(hours=6)
    soon = t + timedelta(hours=1)
    assert safety_rules.review_expiry("news", t, soon) == soon
    far = t + timedelta(days=9)
    assert safety_rules.review_expiry("rebus", t, far) == t + timedelta(hours=24)
    assert safety_rules.review_expiry("mystery", t, None) == t + timedelta(hours=6)
