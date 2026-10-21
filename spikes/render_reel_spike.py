"""Package 0 spike: still image -> Meta-compatible Reel mp4, then validate with ffprobe.

Throwaway script, not production code. Usage:
    python spikes/render_reel_spike.py [input.png] [output.mp4]
Without an input image it draws a Vietnamese test card with Pillow (diacritics check).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

W, H, FPS, DURATION = 1080, 1920, 30, 10
MAX_ZOOM = 1.06


def make_test_card(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (W, H), (24, 28, 48))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 84) if sys.platform == "win32" else ImageFont.load_default()
    draw.rectangle((60, 250, W - 60, H - 400), outline=(255, 255, 255), width=6)  # safe zone box
    draw.text((100, 900), "Nhìn hình đoán chuyện", fill=(255, 220, 90), font=font)
    img.save(path)


def render(src: Path, dst: Path) -> None:
    frames = FPS * DURATION
    zoom = f"min(1+{MAX_ZOOM - 1}*on/{frames},{MAX_ZOOM})"
    vf = (
        f"scale=2160:3840,zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={W}x{H}:fps={FPS},format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(src),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", vf, "-t", str(DURATION),
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-b:v", "8M",
        "-r", str(FPS), "-g", str(FPS * 2), "-keyint_min", str(FPS * 2),
        "-sc_threshold", "0", "-flags", "+cgop",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def validate(info: dict) -> list[str]:
    errors: list[str] = []
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        return ["no video stream"]
    if (v["width"], v["height"]) != (W, H):
        errors.append(f"resolution {v['width']}x{v['height']}")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    fps = num / den
    if not 24 <= fps <= 60:
        errors.append(f"fps {fps}")
    if v.get("avg_frame_rate") != v.get("r_frame_rate"):
        errors.append("not constant frame rate")
    if v["codec_name"] not in {"h264", "hevc"}:
        errors.append(f"codec {v['codec_name']}")
    if v.get("pix_fmt") != "yuv420p":
        errors.append(f"pix_fmt {v.get('pix_fmt')}")
    duration = float(info["format"]["duration"])
    if not 3 <= duration <= 90:
        errors.append(f"duration {duration}")
    if a is None:
        errors.append("no audio stream")
    else:
        if a["codec_name"] != "aac" or int(a["sample_rate"]) != 48000 or a["channels"] != 2:
            errors.append(f"audio {a['codec_name']} {a['sample_rate']} {a['channels']}ch")
    return errors


def main() -> int:
    out_dir = Path("spikes/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else out_dir / "test_card.png"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else out_dir / "reel.mp4"
    if len(sys.argv) <= 1:
        make_test_card(src)
    render(src, dst)
    info = probe(dst)
    errors = validate(info)
    print(json.dumps({"file": str(dst), "bytes": dst.stat().st_size, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
