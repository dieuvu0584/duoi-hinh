"""Package 0 spike: one square master image + one narration -> two videos (decision D-26).

    9:16  1080x1920  Facebook Reel
    16:9  1920x1080  YouTube channel

Modes:
  fill (default)  centre-CROP the square so the picture fills the whole frame: no black bars and
                  no extra backdrop. A 1920x1920 master crops to exactly 1080x1920 and 1920x1080
                  with no upscaling. Everything essential must sit inside the central square
                  (about 56% of each side), the only area both crops share.
  fit             keep the whole square and fill the rest with a blurred copy (fallback when the
                  subject or clues do not fit in the central square).
Narration is delayed by a lead-in, followed by a hold (think time), loudness-normalised and
encoded AAC 48 kHz stereo. Both outputs are validated with ffprobe.

    python spikes/render_renditions_spike.py --image spikes/output/fal/square_cat_alarm__fal-ai_flux-2.png \
        --audio spikes/output/tts/daily_meme__eleven_v3.mp3 --text "Sáng thứ Hai, ..."
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FPS = 30
LEAD_IN_S = 0.8
HOLD_S = 2.0

# target -> size, fit-mode image box (x, y, size), caption box per mode (x, y, w, h), font size.
# fill mode: the 9:16 caption sits in the top safe band (above the central square, below the
# 250 px Reels top margin); the 16:9 caption sits in the right side band.
TARGETS: dict[str, dict[str, Any]] = {
    "reel_9x16": {"w": 1080, "h": 1920, "img": (0, 300, 1080), "font": 40,
                  "caption": {"fill": (60, 255, 960, 140), "fit": (60, 1400, 960, 110)}},
    "youtube_16x9": {"w": 1920, "h": 1080, "img": (420, 0, 1080), "font": 36,
                     "caption": {"fill": (1520, 300, 360, 480), "fit": (1520, 300, 360, 480)}},
}


def run(cmd: list[str]) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {done.stderr[-600:]}")
    return done.stderr + done.stdout


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def audio_seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def caption_png(path: Path, text: str, size: tuple[int, int], box: tuple[int, int, int, int],
                font_px: int) -> None:
    """Transparent full-frame PNG with the wrapped caption (Pillow handles Vietnamese)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype("arial.ttf", font_px) if sys.platform == "win32" \
        else ImageFont.load_default()
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
    lines.append(line)
    height = min(h, len(lines) * int(font_px * 1.3) + 30)
    draw.rounded_rectangle((x, y, x + w, y + height), radius=18, fill=(0, 0, 0, 150))
    for i, ln in enumerate(lines):
        draw.text((x + 20, y + 15 + i * int(font_px * 1.3)), ln, font=font, fill=(255, 255, 255, 255))
    img.save(path)


def render(target: str, image: Path, audio: Path, text: str, out_dir: Path,
           mode: str = "fill") -> Path:
    cfg = TARGETS[target]
    w, h = cfg["w"], cfg["h"]
    ix, iy, isz = cfg["img"]
    narration = audio_seconds(audio)
    total = round(LEAD_IN_S + narration + HOLD_S, 2)
    cap = out_dir / f"caption_{target}.png"
    caption_png(cap, text, (w, h), cfg["caption"][mode], cfg["font"])
    dst = out_dir / f"{target}.mp4"
    if mode == "fill":
        picture = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[comp];"
    else:
        picture = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"gblur=sigma=40,eq=brightness=-0.08[bgb];"
            f"[fg]scale={isz}:{isz}[fgs];"
            f"[bgb][fgs]overlay={ix}:{iy}[comp];"
        )
    fc = (
        picture +
        f"[comp][1:v]overlay=0:0,format=yuv420p[v];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"adelay={int(LEAD_IN_S * 1000)}|{int(LEAD_IN_S * 1000)},apad,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", str(total), "-i", str(image),
         "-loop", "1", "-framerate", str(FPS), "-t", str(total), "-i", str(cap),
         "-i", str(audio), "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", str(total),
         "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-b:v", "8M", "-r", str(FPS),
         "-g", str(FPS * 2), "-keyint_min", str(FPS * 2), "-sc_threshold", "0", "-flags", "+cgop",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
         str(dst)])
    return dst


def measure_loudness(path: Path) -> dict[str, float | None]:
    text = run(["ffmpeg", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text
    def grab(pattern: str) -> float | None:
        m = re.search(pattern, summary)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab(r"I:\s+(-?\d+\.?\d*) LUFS"),
            "true_peak_dbtp": grab(r"Peak:\s+(-?\d+\.?\d*) dBFS")}


def validate(info: dict[str, Any], w: int, h: int, expected_s: float) -> list[str]:
    errors: list[str] = []
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        return ["no video stream"]
    if (v["width"], v["height"]) != (w, h):
        errors.append(f"resolution {v['width']}x{v['height']}")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    if not 24 <= num / den <= 60 or v.get("avg_frame_rate") != v.get("r_frame_rate"):
        errors.append(f"frame rate {v['r_frame_rate']} / avg {v.get('avg_frame_rate')}")
    if v["codec_name"] != "h264" or v.get("pix_fmt") != "yuv420p":
        errors.append(f"codec {v['codec_name']} {v.get('pix_fmt')}")
    duration = float(info["format"]["duration"])
    if not 3 <= duration <= 90 or abs(duration - expected_s) > 0.3:
        errors.append(f"duration {duration} (expected about {expected_s})")
    if a is None:
        errors.append("no audio stream")
    elif a["codec_name"] != "aac" or int(a["sample_rate"]) != 48000 or a["channels"] != 2:
        errors.append(f"audio {a['codec_name']} {a['sample_rate']} {a['channels']}ch")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", required=True)
    p.add_argument("--audio", required=True)
    p.add_argument("--text", default="")
    p.add_argument("--mode", choices=["fill", "fit"], default="fill")
    p.add_argument("--out-dir", default="spikes/output/renditions")
    args = p.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    failed = False
    for target, cfg in TARGETS.items():
        dst = render(target, Path(args.image), Path(args.audio), args.text, out_dir, args.mode)
        info = probe(dst)
        expected = round(LEAD_IN_S + audio_seconds(Path(args.audio)) + HOLD_S, 2)
        errors = validate(info, cfg["w"], cfg["h"], expected)
        frame = out_dir / f"{target}_frame.jpg"
        run(["ffmpeg", "-y", "-ss", "2", "-i", str(dst), "-frames:v", "1", str(frame)])
        report[target] = {"file": str(dst), "bytes": dst.stat().st_size,
                          "duration_s": float(info["format"]["duration"]),
                          "loudness": measure_loudness(dst), "errors": errors}
        failed |= bool(errors)
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
