from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PureWindowsPath
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


def resolve_bgm_file(bgm_dir: Path, configured_file: str) -> Path:
    raw_file = configured_file.strip().replace("\\", "/")
    if not raw_file:
        raise RenderError("BGM file must not be empty.")

    relative_file = Path(raw_file)
    windows_file = PureWindowsPath(raw_file)
    if relative_file.is_absolute() or windows_file.is_absolute() or windows_file.drive or ".." in relative_file.parts:
        raise RenderError(f"BGM file must be a relative path under BGM directory: {configured_file}")
    if relative_file.suffix.lower() not in _BGM_EXTENSIONS:
        raise RenderError(f"Unsupported BGM file extension: {configured_file}")

    base_dir = bgm_dir.resolve()
    candidate = (base_dir / relative_file).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise RenderError(f"BGM file must stay under BGM directory: {configured_file}") from exc
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
