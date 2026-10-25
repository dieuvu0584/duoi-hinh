# Content and brand guidelines (owner-approved defaults, 2026-10-21)

The v0.1 kit had no editorial definition, so the LLM would have to invent tone and rules. This file is the editorial source of truth passed to analysis and caption prompts. The owner approved the defaults below as-is (decision D-22); change them here when the brand evolves, and bump `policy_version` for the prompts that use this file.

## Brand
- Name: Memes On Air. Language: Vietnamese (standard orthography, NFC). Tone: playful, warm, never mocking real individuals. Recurring format: intro hook in the first 1-2 s, puzzle held >= 5 s, "đoán thử" call to comment.
- Series naming and numbering: e.g. "Câu đố số N" so the audience learns the format.

## Enabling the news lane (decision D-16)
The news lane ships **disabled**. The owner enables it by switching `news_lane_enabled` after personally completing this checklist (the system cannot verify legal compliance): (1) read the platform's and applicable Vietnamese rules on publishing news and on false information and keep notes of what you relied on; (2) accept that every news post is manually reviewed; (3) decide the correction/retraction practice; (4) confirm sources and their terms. Consider a professional legal opinion before scaling.

## Per-lane rules
- **daily_life:** relatable, harmless situations; no stereotyping groups; no humiliation of identifiable people; avoid health, money-scam and workplace-conflict stunts that could be mistaken for advice.
- **rebus:** one intended answer, one acceptable answer set (`alternate_answers` reviewed), a difficulty label, answer not shown in the public Reel. Reveal per decision D-12. Avoid phonetic jokes that become vulgar or demeaning.
- **news:** only verified facts; label as illustration/meme; never show the generated image as documentary evidence; no tragedies, deaths, active emergencies, elections/politicians, identifiable private persons; include source credit in the caption where appropriate; apply the correction policy (docs/operations.md).

## Caption and hashtag rules
Caption short (target <= 150 characters, hard cap 300); 3-5 hashtags; no clickbait that misstates the content; no fake urgency; no misleading engagement bait beyond asking people to guess; no external links unless reviewed; hashtags relevant and few.

## Words and topics blacklist
Maintained list in configuration (initial categories: profanity, slurs, sexual content, self-harm, hate, named private persons, minors). The list is checked deterministically by backend code on caption, overlay text and rebus answers in addition to LLM review.

## Originality and variety
Avoid repeating the same format/concept back to back; rotate templates and hooks; track repetition with `experiments` tags. Copy-paste of other creators' puzzles or text is not allowed; every symbol and font must have a recorded license.

## Accessibility and audience
Large readable overlay text inside the safe zone, sufficient contrast, no flashing; difficulty suited to a general Vietnamese audience; offer the answer reveal reliably.

## Review checklist for the owner (shown in the approval card)
1. Is the intended answer the most natural reading? 2. Could any element offend, mislead or identify a real person? 3. Is anything copied or unlicensed (image, symbol, font, audio)? 4. Does the caption match the content? 5. For news: are facts verifiable from the listed sources and is the framing fair?
