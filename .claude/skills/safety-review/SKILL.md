---
name: safety-review
description: Identify policy, factual, privacy, copyright and publishing risks; provide structured findings for backend policy evaluation and human review.
---
# Safety review skill

Review the exact topic, source evidence, image, overlay text, caption, hashtags and planned publication context.

Check at least:
- misinformation, unsupported causal claims, fake quotes/numbers and stale news;
- defamation, harassment, private-person targeting and personal data;
- politics/elections, health, law, children, tragedy, accidents, disasters and armed conflict;
- copyright, trademark/logo use and source-media licensing;
- synthetic image misrepresentation, impersonation and deceptive context;
- platform rules, spam, repetitive content and audience harm.

Return structured output: `decision` (`allow`, `hold_for_human`, `block`), `risk_level`, `categories[]`, `findings[]`, `required_changes[]`, `human_review_required`, `evidence_refs[]`, `policy_version`, `reviewed_at`, `expires_at`.

Output must validate against `docs/schemas/safety_review.schema.json`. The model does not make the final backend decision. The application must fail closed if required evidence/review is absent, invalid or stale. Do not turn uncertain claims into facts.
