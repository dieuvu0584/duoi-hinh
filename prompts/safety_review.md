Review the exact candidate topic, evidence, final selected image, overlays, caption, hashtags and intended publication context for “Nhìn Hình Đoán Chuyện”.

Return JSON only with: `decision` (`allow`, `hold_for_human`, `block`), `risk_level` (`low`, `medium`, `high`, `critical`), `categories`, `findings`, `required_changes`, `human_review_required`, `evidence_refs`, `policy_version`, `reviewed_at`, `expires_at`.

Check factual support, source quality, event/publication date separation, defamation, harassment, personal data, copyright, synthetic-media deception, political/election topics, health/legal advice, children, tragedies, disasters, accidents and conflict. High-risk content requires human review. Never approve based solely on a numerical score. Never invent evidence. The backend is the final policy authority and must fail closed on missing/invalid/stale reviews.

Output must validate against `docs/schemas/safety_review.schema.json`. The backend, not the model, fills `reviewed_content_hash`.
