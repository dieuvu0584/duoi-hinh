"""Confine media references to NHD_MEDIA_DIR (no path traversal, no symlink escapes)."""

from pathlib import Path


class UnsafeMediaPath(ValueError):
    pass


def resolve_media_path(media_dir: str | Path, video_ref: str | None) -> Path:
    """Return the absolute file path for `video_ref`, or raise UnsafeMediaPath.

    The reference must be relative, resolve (after following symlinks) to a path inside the media
    directory, and point at an existing regular file.
    """
    if not video_ref or not video_ref.strip():
        raise UnsafeMediaPath("no video reference")
    if Path(video_ref).is_absolute() or "\0" in video_ref:
        raise UnsafeMediaPath("absolute or invalid path")
    root = Path(media_dir).resolve()
    candidate = (root / video_ref).resolve()
    if root != candidate and root not in candidate.parents:
        raise UnsafeMediaPath("path escapes the media directory")
    if not candidate.is_file():
        raise UnsafeMediaPath("video file does not exist")
    return candidate
