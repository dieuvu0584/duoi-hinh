"""Copy-ready prompts for other AI tools (Gemini, Google Flow/Veo, HeyGen).

Built deterministically from the stored concept: no model call, no cost, nothing is sent anywhere.
The owner copies a prompt into a tool they have their own account for. The rebus answer is never
put into a prompt (the picture must only carry the clues).
"""

from typing import Any

FRAMING = (
    "Full-bleed square 1:1 composition, the scene fills the entire frame edge to edge, no border, "
    "no frame, no white margin. Keep the subject and every clue compact and close together in the "
    "exact centre (middle half of the image); the outer area is simple background."
)
NO_TEXT = "No text, letters, numbers, logos, watermarks or signs anywhere in the picture."


def _clues(clue_mapping: list[Any]) -> list[str]:
    out = []
    for c in clue_mapping or []:
        out.append(str(c.get("visual_clue") or c.get("clue_id") or "") if isinstance(c, dict)
                   else str(c))
    return [c for c in out if c]


def build(*, lane: str, data: dict[str, Any], clue_mapping: list[Any], caption: str | None,
          overlay_text: str | None, narration_text: str | None) -> list[dict[str, str]]:
    """Returns [{"tool", "title", "text"}] for the pieces that can already be written."""
    ip = data.get("image_prompt") or {}
    blueprint = (ip.get("visual_blueprint") or "").strip()
    flux = (ip.get("flux_prompt") or "").strip()
    avoid = [str(x) for x in (ip.get("negative_constraints") or [])]
    clues = _clues(clue_mapping)
    scene = blueprint or flux
    extra = (data.get("owner_instructions") or "").strip()
    prompts: list[dict[str, str]] = []

    if scene or clues:
        lines = ["Create ONE flat, lightly shaded illustration with clear silhouettes and high "
                 "contrast for phones (never photorealistic, never a real person)."]
        if scene:
            lines.append(f"Scene: {scene}")
        if clues:
            lines.append("Every one of these must be visible once, clearly separated, not "
                         "overlapping: " + "; ".join(clues) + ".")
        if extra:
            lines.append(f"Extra requests: {extra}")
        lines += [FRAMING, NO_TEXT]
        if avoid:
            lines.append("Avoid: " + "; ".join(avoid) + ".")
        image = "\n".join(lines)
        prompts.append({"tool": "gemini", "title": "Gemini / Imagen: tạo ảnh vuông 1:1",
                        "text": image})
        prompts.append({
            "tool": "flow", "title": "Google Flow / Veo: tạo video từ ảnh",
            "text": (
                "Animate the supplied still picture into an 8 second vertical-friendly clip. "
                "Camera: very slow push-in (about 5 percent), no cuts. Characters: only tiny "
                "natural motion (blink, breathing, a small head or hand movement). Keep every "
                "object exactly where it is and keep all clues in the centre of the frame "
                "visible the whole time. No new objects, no text, no logos, no morphing. "
                "Keep the flat illustration style. Silent clip (the voiceover is added later)."
                + (f"\nExtra requests: {extra}" if extra else ""))})
    if narration_text:
        prompts.append({
            "tool": "heygen", "title": "HeyGen / giọng đọc: lời đọc tiếng Việt",
            "text": ("Language: Vietnamese. Tone: friendly, playful, medium pace, clear "
                     "pronunciation of tones. Read exactly this text, nothing more:\n"
                     + narration_text.strip())})
    if caption or overlay_text:
        parts = []
        if overlay_text:
            parts.append(f"Chữ trên video: {overlay_text.strip()}")
        if caption:
            parts.append(f"Chú thích đăng kèm: {caption.strip()}")
        prompts.append({"tool": "text", "title": "Chữ và chú thích để dán",
                        "text": "\n".join(parts)})
    return prompts


def build_from_brief(*, lane: str, brief: str, instructions: str | None) -> list[dict[str, str]]:
    """Prompts available as soon as an idea is chosen, before the concept is designed.

    Only the owner's own text is used (brief and requests); the rebus answer is not an input."""
    brief = brief.strip()
    extra = (instructions or "").strip()
    if not brief:
        return []
    image = [
        "Create ONE flat, lightly shaded illustration with clear silhouettes and high contrast "
        "for phones (never photorealistic, never a real person).",
        f"Scene: {brief}",
    ]
    if extra:
        image.append(f"Extra requests: {extra}")
    if lane == "rebus":
        image.append("This is a visual puzzle: draw only the clues described, never the answer, "
                     "and no text.")
    image += [FRAMING, NO_TEXT]
    video = (
        "Animate the supplied still picture into an 8 second vertical-friendly clip. Camera: very "
        "slow push-in (about 5 percent), no cuts. Characters: only tiny natural motion (blink, "
        "breathing, a small head or hand movement). Keep every object exactly where it is and "
        "keep all clues in the centre of the frame visible the whole time. No new objects, no "
        "text, no logos, no morphing. Keep the flat illustration style. Silent clip (the "
        "voiceover is added later)." + (f"\nExtra requests: {extra}" if extra else ""))
    script = (
        "Viết bằng tiếng Việt, giọng thân thiện, vui. Trả về 2 phần: (1) CHÚ THÍCH đăng kèm 15-40 "
        "từ, câu đơn giản, không emoji, không hashtag, số viết bằng chữ; (2) CHỮ TRÊN VIDEO tối "
        "đa 6 từ gợi tình huống nhưng không nói ra điểm hài. "
        + ("Đây là câu đố hình: KHÔNG được nói hay gợi ra đáp án. " if lane == "rebus" else "")
        + f"\nÝ tưởng: {brief}" + (f"\nYêu cầu thêm: {extra}" if extra else ""))
    return [
        {"tool": "gemini", "title": "Gemini / Imagen: tạo ảnh vuông 1:1",
         "text": "\n".join(image)},
        {"tool": "flow", "title": "Google Flow / Veo: tạo video từ ảnh", "text": video},
        {"tool": "gemini_text", "title": "Gemini: viết chú thích và lời đọc", "text": script},
        {"tool": "heygen", "title": "HeyGen: đọc lời (dán chú thích đã duyệt vào)",
         "text": "Language: Vietnamese. Tone: friendly, playful, medium pace, clear pronunciation "
                 "of tones. Read exactly the text I paste next, nothing more."},
    ]
