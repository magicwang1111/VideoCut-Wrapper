from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from videocut.errors import RenderError

_BGM_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}


def resolve_bgm_dir(root_dir: str | Path, configured_dir: str | None = None) -> Path:
    env_dir = os.getenv("BGM_DIR")
    raw_dir = env_dir.strip() if env_dir and env_dir.strip() else (configured_dir.strip() if configured_dir and configured_dir.strip() else None)
    if not raw_dir:
        return Path(root_dir).resolve() / "input" / "bgm"
    path_obj = Path(raw_dir).expanduser()
    if path_obj.is_absolute():
        return path_obj.resolve()
    return (Path(root_dir).resolve() / path_obj).resolve()


def scan_bgm_files(bgm_dir: Path) -> list[Path]:
    if not bgm_dir.is_dir():
        raise RenderError(f"BGM directory does not exist: {bgm_dir}")
    files = sorted(
        p for p in bgm_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _BGM_EXTENSIONS
    )
    if not files:
        raise RenderError(f"No audio files found in BGM directory or subdirectories: {bgm_dir}")
    return files


def list_bgm_catalog(bgm_dir: Path) -> dict[str, object]:
    base_dir = bgm_dir.resolve()
    if not base_dir.is_dir():
        raise RenderError(f"BGM directory does not exist: {base_dir}")

    files: list[dict[str, str]] = []
    category_counts: dict[str, int] = {}
    audio_files = sorted(
        (
            p
            for p in base_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _BGM_EXTENSIONS
        ),
        key=lambda p: p.relative_to(base_dir).as_posix(),
    )
    for audio_file in audio_files:
        relative_parent = audio_file.parent.relative_to(base_dir).as_posix()
        category = "" if relative_parent == "." else relative_parent
        files.append({"category": category, "filename": audio_file.name})
        category_counts[category] = category_counts.get(category, 0) + 1

    categories = [
        {"name": category, "count": category_counts[category]}
        for category in sorted(category_counts)
    ]
    return {
        "bgmRoot": str(base_dir),
        "categories": categories,
        "files": files,
    }


def resolve_bgm_category_dir(bgm_dir: Path, configured_category: str) -> Path:
    raw_category = configured_category.strip().replace("\\", "/")
    if not raw_category:
        raise RenderError("BGM category must not be empty.")

    relative_category = Path(raw_category)
    posix_category = PurePosixPath(raw_category)
    windows_category = PureWindowsPath(raw_category)
    category_parts = [part for part in raw_category.split("/") if part]
    if (
        relative_category.is_absolute()
        or posix_category.is_absolute()
        or windows_category.is_absolute()
        or windows_category.drive
        or any(part in {".", ".."} for part in category_parts)
    ):
        raise RenderError(f"BGM category must be a relative directory under BGM directory: {configured_category}")

    base_dir = bgm_dir.resolve()
    category_dir = (base_dir / relative_category).resolve()
    try:
        category_dir.relative_to(base_dir)
    except ValueError as exc:
        raise RenderError(f"BGM category must stay under BGM directory: {configured_category}") from exc
    if not category_dir.is_dir():
        raise RenderError(f"BGM category directory not found: {category_dir}")
    return category_dir


def scan_bgm_category_files(bgm_dir: Path, configured_category: str) -> list[Path]:
    return scan_bgm_files(resolve_bgm_category_dir(bgm_dir, configured_category))


def resolve_bgm_category_file(bgm_dir: Path, configured_category: str, configured_filename: str) -> Path:
    category_dir = resolve_bgm_category_dir(bgm_dir, configured_category)
    raw_filename = configured_filename.strip().replace("\\", "/")
    if not raw_filename:
        raise RenderError("BGM filename must not be empty.")

    relative_filename = Path(raw_filename)
    posix_filename = PurePosixPath(raw_filename)
    windows_filename = PureWindowsPath(raw_filename)
    filename_parts = [part for part in raw_filename.split("/") if part]
    if (
        len(filename_parts) != 1
        or relative_filename.is_absolute()
        or posix_filename.is_absolute()
        or windows_filename.is_absolute()
        or windows_filename.drive
        or any(part in {".", ".."} for part in filename_parts)
    ):
        raise RenderError(f"BGM filename must be a plain file name under BGM category: {configured_filename}")
    if relative_filename.suffix.lower() not in _BGM_EXTENSIONS:
        raise RenderError(f"Unsupported BGM file extension: {configured_filename}")

    candidate = (category_dir / relative_filename).resolve()
    try:
        candidate.relative_to(category_dir)
    except ValueError as exc:
        raise RenderError(f"BGM filename must stay under BGM category: {configured_filename}") from exc
    if not candidate.is_file():
        raise RenderError(f"BGM file not found: {candidate}")
    return candidate


def apply_bgm(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: str,
    bgm_file: Path,
    volume: float,
    fade_out: float,
    task_id: str | None = None,
) -> None:
    video_file = Path(video_path)
    tmp_token = task_id or "bgm"
    tmp_path = video_file.with_name(f"{video_file.name}.{tmp_token}.{uuid4().hex}.bgm_tmp.mp4")
    raw = subprocess.check_output(
        [ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", video_path],
        encoding="utf-8",
        timeout=10,
    )
    video_duration = float(json.loads(raw)["format"]["duration"])

    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2000000000,"
        f"volume={volume:.4f},"
        f"atrim=end={video_duration:.6f}"
    )
    if fade_out > 0:
        fade_start = max(0.0, video_duration - fade_out)
        audio_filter += f",afade=t=out:st={fade_start:.6f}:d={fade_out:.4f}"
    audio_filter += "[bgm]"

    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-i",
                video_path,
                "-i",
                str(bgm_file),
                "-filter_complex",
                audio_filter,
                "-map",
                "0:v",
                "-map",
                "[bgm]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-y",
                str(tmp_path),
            ],
            check=True,
            timeout=600,
        )
        tmp_path.replace(video_file)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
