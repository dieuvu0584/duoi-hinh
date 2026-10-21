import pytest

from app.services.composition import (
    CROP_HIGH,
    CROP_LOW,
    SAFE_REGIONS,
    Box,
    decide_modes,
    overflow,
)


def box(cid="c", x0=0.4, y0=0.4, x1=0.6, y1=0.6):
    return Box(cid, x0, y0, x1, y1)


def test_a_centred_clue_allows_both_crops():
    d = decide_modes([box()])
    assert d.modes == {"reel_9x16": "fill", "youtube_16x9": "fill"} and d.all_fill


def test_central_square_is_the_intersection_of_both_crops():
    full_central = box(x0=CROP_LOW + 0.02, y0=CROP_LOW + 0.02, x1=CROP_HIGH - 0.02,
                       y1=CROP_HIGH - 0.02)
    assert decide_modes([full_central]).all_fill


def test_wide_subject_is_ok_for_youtube_but_cut_by_the_reel_crop():
    wide = box("wide", x0=0.05, y0=0.4, x1=0.95, y1=0.6)  # spans the width, centred vertically
    d = decide_modes([wide])
    assert d.modes["reel_9x16"] == "fit" and d.modes["youtube_16x9"] == "fill"
    assert d.outside["reel_9x16"] == ["wide"]


def test_tall_subject_is_ok_for_the_reel_but_cut_by_the_youtube_crop():
    tall = box("tall", x0=0.4, y0=0.15, x1=0.6, y1=0.75)
    d = decide_modes([tall])
    assert d.modes["reel_9x16"] == "fill" and d.modes["youtube_16x9"] == "fit"


def test_reels_ui_zones_count_as_unsafe_for_the_reel_only():
    low = box("low", x0=0.4, y0=0.7, x1=0.6, y1=0.8)  # touches the bottom 400 px zone
    d = decide_modes([low])
    assert d.modes["reel_9x16"] == "fit"
    assert d.modes["youtube_16x9"] == "fit"  # 0.8 > 0.78125 as well
    high = box("high", x0=0.4, y0=0.05, x1=0.6, y1=0.3)  # inside the top 250 px
    assert decide_modes([high]).modes["reel_9x16"] == "fit"


def test_one_bad_clue_among_good_ones_forces_fit():
    d = decide_modes([box("a"), box("b", x0=0.1, y0=0.4, x1=0.3, y1=0.6)])
    assert d.modes["reel_9x16"] == "fit" and d.outside["reel_9x16"] == ["b"]


def test_no_boxes_fails_closed_to_fit():
    d = decide_modes([])
    assert d.modes == {"reel_9x16": "fit", "youtube_16x9": "fit"}
    assert "cannot verify" in d.reason


def test_margin_makes_near_misses_count_as_cut():
    near = box("near", x0=CROP_LOW + 0.003, y0=0.4, x1=0.6, y1=0.6)
    assert decide_modes([near], margin=0.0).modes["reel_9x16"] == "fill"
    assert decide_modes([near], margin=0.01).modes["reel_9x16"] == "fit"


@pytest.mark.parametrize(
    "args",
    [(-0.1, 0.1, 0.5, 0.5), (0.1, 0.1, 1.2, 0.5), (0.5, 0.1, 0.5, 0.5), (0.2, 0.6, 0.4, 0.4)],
)
def test_invalid_boxes_are_rejected(args):
    with pytest.raises(ValueError):
        Box("bad", *args)


def test_overflow_measures_the_worst_side():
    region = SAFE_REGIONS["youtube_16x9"]
    assert overflow(box(y0=0.2, y1=0.5), region) == pytest.approx(CROP_LOW - 0.2)
    assert overflow(box(), region) == 0.0
