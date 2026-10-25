"""The on-screen text sits in the middle of its band in both video formats."""

import pytest
from PIL import Image

from app.services import render_service as rs

try:
    FONT = rs.find_font()
except Exception:  # noqa: BLE001 - no usable font on this machine
    FONT = None

pytestmark = pytest.mark.skipif(FONT is None, reason="no Vietnamese-capable font available")


def ink_box(path):
    return Image.open(path).getchannel("A").getbbox()


@pytest.mark.parametrize("target, mode", [("reel_9x16", "fill"), ("reel_9x16", "fit"),
                                          ("youtube_16x9", "fill")])
def test_the_caption_pill_is_centred_in_its_region(tmp_path, target, mode):
    cfg = rs.TARGETS[target]
    x, y, w, h = cfg["caption"][mode]
    out = tmp_path / "cap.png"
    rs._caption_png(out, "Vết nứt hay sợi tóc?", (cfg["w"], cfg["h"]), (x, y, w, h),
                    cfg["font"], FONT)
    left, top, right, bottom = ink_box(out)
    assert abs((left + right) / 2 - (x + w / 2)) <= 2  # centred horizontally in its region
    assert abs((top + bottom) / 2 - (y + h / 2)) <= 2  # and vertically


def test_a_short_text_gets_a_small_pill_not_a_full_width_bar(tmp_path):
    cfg = rs.TARGETS["reel_9x16"]
    out = tmp_path / "cap.png"
    rs._caption_png(out, "Ôi", (cfg["w"], cfg["h"]), cfg["caption"]["fill"], cfg["font"], FONT)
    left, _, right, _ = ink_box(out)
    assert right - left < 300
