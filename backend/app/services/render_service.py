"""Render the two videos (Reel 9:16, YouTube 16:9) from one square master (decision D-26).

Inputs are media references confined to NHD_MEDIA_DIR; the output is content-addressed by the
hash of the full render spec, so an identical request is a free cache hit. Every file is checked
with ffprobe before it is accepted (resolution, constant frame rate, codec, pixel format, duration,
AAC 48 kHz stereo, not silent); a file that fails is recorded INVALID and never returned as usable.
Captions use a font with Vietnamese coverage; if none is found rendering fails (never a tofu box).

Modes: `fill` centre-crops the square (no bars, nothing added); `fit` shows the whole square over a
blurred copy. The caller decides the mode per target from the composition check.
"""

import hashlib
import json
import re
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import AudioAsset, VideoAsset
from app.services.composition import SAFE_REGIONS, Box, decide_modes
from app.services.media_paths import UnsafeMediaPath, resolve_media_path

RENDERER_VERSION = 1
FPS = 30
LEAD_IN_S = 0.8
HOLD_S = 2.0
MAX_CAPTION_CHARS = 300

TARGETS: dict[str, dict[str, Any]] = {
    "reel_9x16": {"w": 1080, "h": 1920, "img": (0, 300, 1080), "font": 40,
                  "caption": {"fill": (60, 255, 960, 140), "fit": (60, 1400, 960, 110)}},
    "youtube_16x9": {"w": 1920, "h": 1080, "img": (420, 0, 1080), "font": 36,
                     "caption": {"fill": (1520, 300, 360, 480), "fit": (1520, 300, 360, 480)}},
}
_FONT_CANDIDATES = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/dejavu/DejaVuSans.ttf"]
_VIETNAMESE_SAMPLE = "ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"


class RenderError(Exception):
    """A render or validation failure that must not produce a usable asset."""


def _run(cmd: list[str], timeout: int = 600) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenderError(f"{cmd[0]} could not run: {type(exc).__name__}") from None
    if done.returncode != 0:
        raise RenderError(f"{cmd[0]} failed: {done.stderr[-400:]}")
    return done.stderr + done.stdout


def find_font(configured: str | None = None) -> str:
    """A font that really has Vietnamese glyphs, or RenderError (fail closed)."""
    from PIL import ImageFont

    for candidate in ([configured] if configured else []) + _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, 20)
        except OSError:
            continue
        # A font without the glyphs renders them all as the same .notdef box.
        boxes = {font.getmask(ch).getbbox() for ch in "ăơưđếộ"}
        notdef = font.getmask("\ufffe").getbbox()
        if len(boxes) > 1 or (boxes and None not in boxes and boxes != {notdef}):
            return candidate
    raise RenderError("no font with Vietnamese coverage found (set NHD_FONT_PATH)")


def spec_hash(spec: dict[str, Any]) -> str:
    payload = json.dumps({"renderer": RENDERER_VERSION, **spec}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _caption_png(path: Path, text: str, size: tuple[int, int], box: tuple[int, int, int, int],
                 font_px: int, font_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, font_px)
    x, y, w, h = box
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= w - 40:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    step = int(font_px * 1.3)
    height = min(h, len(lines) * step + 30)
    draw.rounded_rectangle((x, y, x + w, y + height), radius=18, fill=(0, 0, 0, 150))
    for i, ln in enumerate(lines):
        draw.text((x + 20, y + 15 + i * step), ln, font=font, fill=(255, 255, 255, 255))
    img.save(path)


def probe(path: Path) -> dict[str, Any]:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", str(path)], timeout=60)
    return json.loads(out)


def measure_loudness(path: Path) -> dict[str, float | None]:
    text = _run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null",
                 "-"], timeout=120)
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text

    def grab(pattern: str) -> float | None:
        m = re.search(pattern, summary)
        return float(m.group(1)) if m else None

    return {"integrated_lufs": grab(r"I:\s+(-?\d+\.?\d*) LUFS"),
            "true_peak_dbtp": grab(r"Peak:\s+(-?\d+\.?\d*) dBFS")}


def validate(info: dict[str, Any], loudness: dict[str, float | None], *, width: int,
             height: int, expected_s: float) -> list[str]:
    errors: list[str] = []
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        return ["no video stream"]
    if (v["width"], v["height"]) != (width, height):
        errors.append(f"resolution {v['width']}x{v['height']}")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    if not 24 <= num / den <= 60 or v.get("avg_frame_rate") != v.get("r_frame_rate"):
        errors.append(f"frame rate {v['r_frame_rate']} / {v.get('avg_frame_rate')}")
    if v["codec_name"] not in {"h264", "hevc"} or v.get("pix_fmt") != "yuv420p":
        errors.append(f"codec {v['codec_name']} {v.get('pix_fmt')}")
    duration = float(info["format"]["duration"])
    if not 3 <= duration <= 90 or abs(duration - expected_s) > 0.35:
        errors.append(f"duration {duration} (expected about {expected_s})")
    if a is None:
        errors.append("no audio stream")
    else:
        if a["codec_name"] != "aac" or int(a["sample_rate"]) != 48000 or a["channels"] != 2:
            errors.append(f"audio {a['codec_name']} {a['sample_rate']} {a['channels']}ch")
        lufs = loudness.get("integrated_lufs")
        if lufs is None or lufs < -45:
            errors.append(f"audio is silent or unmeasurable ({lufs} LUFS)")
    return errors


def render_rendition(
    session: Session, cfg: Settings, *, master_ref: str, audio: AudioAsset, caption: str,
    target: str, mode: str = "fill", preset: str | None = None,
    revision_id: uuid.UUID | None = None,
) -> VideoAsset:
    if target not in TARGETS:
        raise ApiError(422, "unknown_target", "Unknown render target")
    if mode not in {"fill", "fit"}:
        raise ApiError(422, "unknown_mode", "Mode must be fill or fit")
    if len(caption) > MAX_CAPTION_CHARS:
        raise ApiError(422, "caption_too_long", "Caption is too long for the video")
    try:
        master = resolve_media_path(cfg.media_dir, master_ref)
        audio_path = resolve_media_path(cfg.media_dir, audio.storage_ref)
    except UnsafeMediaPath as exc:
        raise ApiError(422, "unsafe_media", f"Media reference rejected: {exc}") from None

    cfgt = TARGETS[target]
    w, h = cfgt["w"], cfgt["h"]
    duration_audio = float(audio.duration_s) if audio.duration_s is not None else None
    if duration_audio is None:
        raise ApiError(422, "audio_duration_unknown", "The narration has no measured duration")
    total = round(LEAD_IN_S + duration_audio + HOLD_S, 2)
    preset = preset or cfg.x264_preset
    spec = {
        "target": target, "mode": mode, "size": [w, h], "fps": FPS, "lead_in_s": LEAD_IN_S,
        "hold_s": HOLD_S, "caption": caption,
        "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        "audio_sha256": audio.sha256, "preset": preset,
    }
    key = spec_hash(spec)
    existing = session.scalar(select(VideoAsset).where(VideoAsset.spec_hash == key))
    if existing is not None and existing.status == "valid":
        return existing  # identical request: no new render

    ref = f"video/{key}.mp4"
    out = Path(cfg.media_dir) / ref
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.with_suffix(".part.mp4")
    cap = out.with_suffix(".caption.png")
    try:
        _caption_png(cap, caption, (w, h), cfgt["caption"][mode], cfgt["font"],
                     find_font(cfg.font_path))
        ix, iy, isz = cfgt["img"]
        if mode == "fill":
            picture = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[comp];"
        else:
            picture = (
                f"[0:v]split=2[bg][fg];"
                f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"gblur=sigma=40,eq=brightness=-0.08[bgb];[fg]scale={isz}:{isz}[fgs];"
                f"[bgb][fgs]overlay={ix}:{iy}[comp];")
        delay = int(LEAD_IN_S * 1000)
        fc = (picture + "[comp][1:v]overlay=0:0,format=yuv420p[v];"
              f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={delay}|{delay},"
              "apad,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
        _run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", str(total),
              "-i", str(master), "-loop", "1", "-framerate", str(FPS), "-t", str(total),
              "-i", str(cap), "-i", str(audio_path), "-filter_complex", fc,
              "-map", "[v]", "-map", "[a]", "-t", str(total), "-c:v", "libx264",
              "-profile:v", "high", "-preset", preset, "-b:v", "8M", "-r", str(FPS),
              "-g", str(FPS * 2), "-keyint_min", str(FPS * 2), "-sc_threshold", "0",
              "-flags", "+cgop", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
              "-movflags", "+faststart", str(work)])
        info = probe(work)
        loud = measure_loudness(work)
        errors = validate(info, loud, width=w, height=h, expected_s=total)
        if errors:
            raise RenderError("; ".join(errors))
        work.replace(out)
    except RenderError as exc:
        work.unlink(missing_ok=True)
        invalid = VideoAsset(target=target, mode=mode, spec=spec, spec_hash=key,
                             storage_ref="", sha256="", probe={"error": str(exc)[:500]},
                             width=w, height=h, status="invalid", audio_asset_id=audio.id,
                             master_ref=master_ref)
        if existing is None:
            session.add(invalid)
            session.commit()
        raise
    finally:
        cap.unlink(missing_ok=True)

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    asset = existing or VideoAsset(target=target, mode=mode, spec=spec, spec_hash=key,
                                   audio_asset_id=audio.id, master_ref=master_ref)
    asset.storage_ref, asset.sha256, asset.status = ref, sha, "valid"
    asset.revision_id = revision_id or asset.revision_id
    asset.probe = {"format": info["format"], "loudness": loud}
    asset.duration_s = Decimal(str(round(float(info["format"]["duration"]), 3)))
    asset.width, asset.height = w, h
    if existing is None:
        session.add(asset)
    session.commit()
    return asset


def render_both(
    session: Session, cfg: Settings, *, master_ref: str, audio: AudioAsset, caption: str,
    boxes: list[Box], preset: str | None = None, revision_id: uuid.UUID | None = None,
    expected: list[str] | None = None,
) -> dict[str, VideoAsset]:
    """Both renditions, each in the mode the composition check allows (never an unchecked crop)."""
    decision = decide_modes(boxes, expected=expected)
    return {target: render_rendition(session, cfg, master_ref=master_ref, audio=audio,
                                     caption=caption, target=target, mode=decision.modes[target],
                                     preset=preset, revision_id=revision_id)
            for target in SAFE_REGIONS}

