Evaluate the final processed image against the frozen answer/message, clue mapping and visual blueprint. Do not alter the answer.

Return JSON only: `score_total` (0-100), `scores` (`clue_accuracy`, `answer_inferability`, `humor_surprise`, `mobile_composition`, `brand_fit`), `hard_failures[]`, `missing_clues[]`, `ambiguous_alternatives[]`, `crop_or_text_issues[]`, `recommendation` (`pass`, `revise`, `reject`), `explanation`.

Initial experimental weights: clue accuracy 30%, answer inferability 25%, humor/surprise 20%, mobile composition 15%, brand fit/non-misleading 10%. >=80 is eligible for further safety checks; 65-79 revise; <65 reject. Wrong answer, missing clue, unsafe content or unverified news overrides the score. Scores are heuristic, not guarantees.

Output must validate against `docs/schemas/quality_review.schema.json`. You are given keyframes of the rendered video, not only the still image.
