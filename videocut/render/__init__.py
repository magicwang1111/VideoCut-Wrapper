from __future__ import annotations

import os
import shutil
from pathlib import Path


def _find_file_recursive(directory: Path, filename: str, max_depth: int = 3) -> Path | None:
    if not directory.exists() or max_depth < 0:
        return None
    direct_match = directory / filename
    if direct_match.exists():
        return direct_match
    for entry in directory.iterdir():
        if not entry.is_dir():
            continue
        found = _find_file_recursive(entry, filename, max_depth - 1)
        if found:
            return found
    return None


def _resolve_binary_path(root_dir: Path, env_name: str, filenames: tuple[str, ...], which_name: str) -> str | None:
    explicit_path = os.getenv(env_name)
    if explicit_path and Path(explicit_path).exists():
        return str(Path(explicit_path).resolve())
    for root in (root_dir / "ffmpeg", root_dir.parent / "ffmpeg"):
        for filename in filenames:
            found = _find_file_recursive(root, filename)
            if found:
                return str(found)
    for name in (which_name, *filenames):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def resolve_ffmpeg_path(root_dir: str | Path) -> str | None:
    return _resolve_binary_path(Path(root_dir).resolve(), "FFMPEG_PATH", ("ffmpeg.exe", "ffmpeg"), "ffmpeg")


def resolve_ffprobe_path(root_dir: str | Path) -> str | None:
    return _resolve_binary_path(Path(root_dir).resolve(), "FFPROBE_PATH", ("ffprobe.exe", "ffprobe"), "ffprobe")


__all__ = ["resolve_ffmpeg_path", "resolve_ffprobe_path"]
