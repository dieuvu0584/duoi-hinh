import hashlib
import shutil
import subprocess
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import AudioAsset, VideoAsset
from app.services import render_service as rs
from app.services.composition import Box

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture()
def media(tmp_path):
    (tmp_path / "img").mkdir()
    (tmp_path / "audio").mkdir()
    img = Image.new("RGB", (1920, 1920), (230, 190, 120))
    d = ImageDraw.Draw(img)
    d.ellipse((760, 760, 1160, 1160), fill=(200, 30, 30))
    img.save(tmp_path / "img" / "master.png")
    return tmp_path


def make_audio(media, name="tone.mp3", seconds=2.0, silent=False):
    src = "anullsrc=r=48000:cl=stereo" if silent else f"sine=frequency=440:duration={seconds}"
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", str(seconds), "-ac", "1",
           str(media / "audio" / name)]
    subprocess.run(cmd, check=True, capture_output=True)
    return f"audio/{name}"


def audio_asset(db, media, **kw):
    ref = make_audio(media, **kw)
    data = (media / ref).read_bytes()
    asset = AudioAsset(
        kind="narration", provider="test", model_id="m", voice_id="v", synth_settings={},
        text_hash=hashlib.sha256(data + ref.encode()).hexdigest(), normalized_text="x",
        normalizer_version=1, sha256=hashlib.sha256(data).hexdigest(), storage_ref=ref,
        duration_s=Decimal(str(kw.get("seconds", 2.0))), characters=1)
    db.add(asset)
    db.commit()
    return asset


def cfg(media):
    return Settings(_env_file=None, media_dir=str(media), x264_preset="ultrafast")


def render(db, media, audio, *, target="reel_9x16", mode="fill", caption="Xin chào Việt Nam"):
    return rs.render_rendition(db, cfg(media), master_ref="img/master.png", audio=audio,
                               caption=caption, target=target, mode=mode)


def probe(media, asset):
    return rs.probe(media / asset.storage_ref)


def test_reel_is_rendered_to_the_exact_spec_and_validated(db, media):
    asset = render(db, media, audio_asset(db, media))
    info = probe(media, asset)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (1080, 1920) and v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1" and a["sample_rate"] == "48000" and a["channels"] == 2
    assert asset.status == "valid" and asset.target == "reel_9x16" and asset.mode == "fill"
    assert float(asset.duration_s) == pytest.approx(0.8 + 2.0 + 2.0, abs=0.3)
    assert asset.probe["loudness"]["integrated_lufs"] > -25  # audible, loudness-normalised
    assert asset.sha256 == hashlib.sha256((media / asset.storage_ref).read_bytes()).hexdigest()


def test_youtube_rendition_is_1920_by_1080(db, media):
    asset = render(db, media, audio_asset(db, media), target="youtube_16x9")
    v = next(s for s in probe(media, asset)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1920, 1080)


def test_fit_mode_is_recorded_and_renders(db, media):
    asset = render(db, media, audio_asset(db, media), mode="fit")
    assert asset.mode == "fit" and asset.status == "valid"


def test_an_identical_request_is_a_cache_hit_without_running_ffmpeg(db, media, monkeypatch):
    audio = audio_asset(db, media)
    first = render(db, media, audio)

    def boom(*_a, **_k):
        raise AssertionError("ffmpeg must not run for a cache hit")

    monkeypatch.setattr(rs, "_run", boom)
    again = render(db, media, audio)
    assert again.id == first.id


def test_a_different_caption_or_mode_is_a_different_asset(db, media):
    audio = audio_asset(db, media)
    a = render(db, media, audio)
    b = render(db, media, audio, caption="Một chú thích khác")
    c = render(db, media, audio, mode="fit")
    assert len({a.id, b.id, c.id}) == 3 and len({a.spec_hash, b.spec_hash, c.spec_hash}) == 3


def test_render_both_picks_the_mode_per_target_from_the_composition_check(db, media):
    audio = audio_asset(db, media)
    centred = [Box("c", 0.45, 0.45, 0.55, 0.55)]
    both = rs.render_both(db, cfg(media), master_ref="img/master.png", audio=audio,
                          caption="ok", boxes=centred)
    assert {t: a.mode for t, a in both.items()} == {"reel_9x16": "fill", "youtube_16x9": "fill"}

    off_centre = [Box("c", 0.05, 0.45, 0.4, 0.55)]  # cut by the 9:16 crop, fine for 16:9
    mixed = rs.render_both(db, cfg(media), master_ref="img/master.png", audio=audio,
                           caption="ok", boxes=off_centre)
    assert mixed["reel_9x16"].mode == "fit" and mixed["youtube_16x9"].mode == "fill"


def test_without_boxes_nothing_is_cropped(db, media):
    both = rs.render_both(db, cfg(media), master_ref="img/master.png",
                          audio=audio_asset(db, media), caption="ok", boxes=[])
    assert all(a.mode == "fit" for a in both.values())


@pytest.mark.parametrize("ref", ["../outside.png", "/etc/passwd", "img/missing.png", ""])
def test_unsafe_or_missing_master_references_are_refused(db, media, ref):
    audio = audio_asset(db, media)
    with pytest.raises(ApiError) as exc:
        rs.render_rendition(db, cfg(media), master_ref=ref, audio=audio, caption="x",
                            target="reel_9x16")
    assert exc.value.code == "unsafe_media"


def test_an_audio_reference_outside_the_media_dir_is_refused(db, media):
    audio = audio_asset(db, media)
    audio.storage_ref = "../../secret.mp3"
    with pytest.raises(ApiError) as exc:
        render(db, media, audio)
    assert exc.value.code == "unsafe_media"


@pytest.mark.parametrize(("kw", "code"), [
    ({"target": "tiktok"}, "unknown_target"),
    ({"mode": "stretch"}, "unknown_mode"),
    ({"caption": "x" * 400}, "caption_too_long"),
])
def test_invalid_parameters_are_rejected(db, media, kw, code):
    with pytest.raises(ApiError) as exc:
        render(db, media, audio_asset(db, media), **kw)
    assert exc.value.code == code


def test_narration_without_a_measured_duration_is_refused(db, media):
    audio = audio_asset(db, media)
    audio.duration_s = None
    with pytest.raises(ApiError) as exc:
        render(db, media, audio)
    assert exc.value.code == "audio_duration_unknown"


def test_a_silent_audio_track_fails_validation_and_leaves_no_usable_file(db, media):
    audio = audio_asset(db, media, name="silent.mp3", silent=True)
    with pytest.raises(rs.RenderError, match="silent"):
        render(db, media, audio)
    asset = db.query(VideoAsset).one()
    assert asset.status == "invalid" and asset.storage_ref == ""
    assert not list((media / "video").glob("*.mp4")) and not list((media / "video").glob("*.part*"))


def test_work_files_are_cleaned_up_after_success(db, media):
    render(db, media, audio_asset(db, media))
    assert [p.name for p in (media / "video").iterdir()] and not list(
        (media / "video").glob("*.png")) and not list((media / "video").glob("*.part*"))


# ----------------------------------------------------------------------------- pure functions
def good_info(**over):
    info = {
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1",
             "avg_frame_rate": "30/1", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
        ],
        "format": {"duration": "7.6"},
    }
    info.update(over)
    return info


def check(info, lufs=-15.5, expected=7.6):
    return rs.validate(info, {"integrated_lufs": lufs}, width=1080, height=1920,
                       expected_s=expected)


def test_validate_accepts_a_good_file():
    assert check(good_info()) == []


@pytest.mark.parametrize(("mutate", "needle"), [
    (lambda i: i["streams"][0].update(width=1000), "resolution"),
    (lambda i: i["streams"][0].update(avg_frame_rate="29.97/1"), "frame rate"),
    (lambda i: i["streams"][0].update(r_frame_rate="12/1", avg_frame_rate="12/1"), "frame rate"),
    (lambda i: i["streams"][0].update(pix_fmt="yuv444p"), "codec"),
    (lambda i: i["streams"][0].update(codec_name="mpeg4"), "codec"),
    (lambda i: i["streams"][1].update(sample_rate="44100"), "audio"),
    (lambda i: i["streams"][1].update(channels=1), "audio"),
    (lambda i: i["format"].update(duration="2.0"), "duration"),
    (lambda i: i["format"].update(duration="95"), "duration"),
    (lambda i: i["format"].update(duration="9.5"), "duration"),
    (lambda i: i["streams"].pop(1), "no audio"),
])
def test_validate_rejects_each_kind_of_bad_file(mutate, needle):
    info = good_info()
    mutate(info)
    assert any(needle in e for e in check(info)), check(info)


def test_validate_rejects_silence_and_unmeasurable_audio():
    assert any("silent" in e for e in check(good_info(), lufs=-70))
    assert any("silent" in e for e in check(good_info(), lufs=None))


def test_spec_hash_is_stable_and_sensitive():
    base = {"target": "reel_9x16", "caption": "a"}
    assert rs.spec_hash(base) == rs.spec_hash(dict(reversed(list(base.items()))))
    assert rs.spec_hash(base) != rs.spec_hash({**base, "caption": "b"})


def test_a_font_with_vietnamese_glyphs_is_found_or_rendering_fails_closed(monkeypatch):
    assert rs.find_font()  # this machine has one
    monkeypatch.setattr(rs, "_FONT_CANDIDATES", [])
    with pytest.raises(rs.RenderError, match="font"):
        rs.find_font()
    with pytest.raises(rs.RenderError):
        rs.find_font("definitely-not-a-font.ttf")
