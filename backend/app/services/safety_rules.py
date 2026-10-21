"""Deterministic safety rules decided by backend code, never by model output alone.

docs: .claude/rules/safety-and-publishing.md ("human_review_required is derived ...").
"""

from datetime import datetime, timedelta

HIGH_RISK_LEVELS = frozenset({"high", "critical"})

# Categories that always need a human (rules file, "Human review required").
SENSITIVE_CATEGORIES = frozenset({
    "identifiable_person_allegation", "politics_elections", "health_advice", "legal_claim",
    "children", "private_individual", "personal_data", "copyright_concern", "war_conflict",
    "death_injury", "disaster", "active_emergency", "uncertain_breaking_news", "tragedy",
})

# Review time-to-live per lane. News goes stale faster (hypotheses, tune after the pilot).
REVIEW_TTL = {
    "daily_life": timedelta(hours=24),
    "rebus": timedelta(hours=24),
    "news": timedelta(hours=6),
}


def requires_human_review(lane: str, risk_level: str, categories: list[str] | set[str]) -> bool:
    """True if a person must review before this content can ever publish.

    Unknown risk levels fail closed (treated as needing review). Every news post needs a human
    during the pilot (decision D-8 / D-16).
    """
    if lane == "news":
        return True
    if risk_level not in {"low", "medium", "high", "critical"}:
        return True
    if risk_level in HIGH_RISK_LEVELS:
        return True
    return bool(set(categories) & SENSITIVE_CATEGORIES)


def review_expiry(lane: str, reviewed_at: datetime, fresh_until: datetime | None) -> datetime:
    """Expiry = min(lane TTL, topic freshness window). Unknown lanes get the shortest TTL."""
    ttl = REVIEW_TTL.get(lane, min(REVIEW_TTL.values()))
    expires = reviewed_at + ttl
    return min(expires, fresh_until) if fresh_until is not None else expires
