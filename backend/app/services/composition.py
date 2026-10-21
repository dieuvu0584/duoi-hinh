"""Crop-safety decision for the square master (docs/video-pipeline.md, decision D-26).

A vision model returns normalised bounding boxes (0..1, origin top-left) for every essential
subject/clue. THIS code, not the model, decides per target whether a centre crop (`fill`) keeps
all of them, or the whole square must be shown (`fit`, blurred backdrop) or the image regenerated.

Geometry on a square master:
  reel 9:16  keeps the full height and the central 56.25% of the width, and the Reels UI covers
             the top 250 px and bottom 400 px of the 1080x1920 frame;
  youtube 16:9 keeps the full width and the central 56.25% of the height.
Unknown or missing boxes fail closed to `fit`.
"""

from dataclasses import dataclass
from typing import Literal

Mode = Literal["fill", "fit"]

CROP_LOW = 0.21875  # (1 - 1080/1920) / 2
CROP_HIGH = 0.78125
REEL_TOP_UI = 250 / 1920  # top margin of the Reels frame
REEL_BOTTOM_UI = 1 - 400 / 1920  # start of the bottom UI zone

# target -> allowed (x0, y0, x1, y1) in master coordinates
SAFE_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "reel_9x16": (CROP_LOW, REEL_TOP_UI, CROP_HIGH, REEL_BOTTOM_UI),
    "youtube_16x9": (0.0, CROP_LOW, 1.0, CROP_HIGH),
}


@dataclass(frozen=True)
class Box:
    clue_id: str
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        for name in ("x0", "y0", "x1", "y1"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{self.clue_id}: {name}={value} is outside 0..1")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError(f"{self.clue_id}: box has no area")


@dataclass(frozen=True)
class Decision:
    modes: dict[str, Mode]
    outside: dict[str, list[str]]  # target -> clue ids that a crop would cut
    reason: str

    @property
    def all_fill(self) -> bool:
        return all(m == "fill" for m in self.modes.values())


def overflow(box: Box, region: tuple[float, float, float, float]) -> float:
    """How far (normalised) the box sticks out of the region; 0 means fully inside."""
    rx0, ry0, rx1, ry1 = region
    return max(rx0 - box.x0, ry0 - box.y0, box.x1 - rx1, box.y1 - ry1, 0.0)


def decide_modes(boxes: list[Box], *, margin: float = 0.01,
                 expected: list[str] | None = None) -> Decision:
    """Per-target crop mode. `margin` shrinks the safe region so near-misses count as cut."""
    if not boxes:
        return Decision({t: "fit" for t in SAFE_REGIONS}, {},
                        "no clue boxes supplied: cannot verify, showing the whole square")
    missing = sorted(set(expected or []) - {b.clue_id for b in boxes})
    if missing:  # an unmeasured clue could be anywhere: never crop on partial information
        return Decision({t: "fit" for t in SAFE_REGIONS}, {t: list(missing) for t in SAFE_REGIONS},
                        f"clues not found by the measurement: {missing}; showing the whole square")
    modes: dict[str, Mode] = {}
    outside: dict[str, list[str]] = {}
    for target, (x0, y0, x1, y1) in SAFE_REGIONS.items():
        region = (x0 + margin if x0 > 0 else 0.0, y0 + margin if y0 > 0 else 0.0,
                  x1 - margin if x1 < 1 else 1.0, y1 - margin if y1 < 1 else 1.0)
        cut = [b.clue_id for b in boxes if overflow(b, region) > 0.0]
        outside[target] = cut
        modes[target] = "fit" if cut else "fill"
    if all(m == "fill" for m in modes.values()):
        reason = "every clue lies inside both crop regions"
    else:
        bad = {t: c for t, c in outside.items() if c}
        reason = f"a centre crop would cut: {bad}"
    return Decision(modes, outside, reason)
