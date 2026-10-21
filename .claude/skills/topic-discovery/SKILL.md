---
name: topic-discovery
description: Discover, normalize, verify and rank candidate topics for Vietnamese Facebook visual puzzle/meme content using supplied source evidence.
---
# Topic discovery skill

When invoked:
1. Inspect configured sources and permitted access methods. Use RSS/API/official pages only where terms and technical access permit. Do not bypass paywalls, logins, rate limits or platform controls.
2. Normalize title, publisher, canonical URL, publication timestamp, retrieval timestamp and event date as separate fields.
3. Deduplicate by canonical URL, content hash and semantic similarity. Reposts are not independent corroboration.
4. Separate verified facts, unverified claims, interpretation and opinion.
5. Rank by recency, relevance to Vietnam/audience, visualizability, clarity, source quality, sensitivity and duplication risk.
6. For sensitive factual claims, require authoritative primary evidence or multiple independent reputable sources and mark human review required.
7. Return a structured candidate list with provenance. If evidence is missing, say so; do not fill gaps from imagination.

## Output fields
`topic_id`, `title`, `content_type`, `source_urls[]`, `publisher`, `published_at`, `event_date`, `retrieved_at`, `verified_facts[]`, `unverified_claims[]`, `opinion_seed`, `novelty_score`, `visualizability_score`, `risk_level`, `review_required`, `reasoning_summary`.
