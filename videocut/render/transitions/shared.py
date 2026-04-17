from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from videocut.errors import RenderError
from videocut.ffmpeg_config import FFmpegVideoSettings
from videocut.log import get_logger
from videocut.presets import QualityPreset, ResolutionPreset
from videocut.render.types import VideoClip

logger = get_logger(__name__)


def build_normalize_video_filter(res_preset: ResolutionPreset) -> str:
    return ",".join(
        [
            f"fps={res_preset.fps}",
            (
                f"scale={res_preset.width}:{res_preset.height}:"
                "force_original_aspect_ratio=decrease:flags=lanczos"
            ),
            f"pad={res_preset.width}:{res_preset.height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "format=yuv420p",
        ]
    )


def _run_ffmpeg(args: list[str], timeout: int) -> None:
    try:
        subprocess.run(args, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise RenderError(f"FFmpeg exited with code {exc.returncode}.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"FFmpeg timed out after {timeout} seconds.") from exc


def normalize_clips(
    root_dir: Path,
    ffmpeg_path: str,
    clips: list[VideoClip],
    qual_preset: QualityPreset,
    res_preset: ResolutionPreset,
    video_settings: FFmpegVideoSettings,
) -> tuple[list[VideoClip], Callable[[], None]]:
    temp_dir = root_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    normalize_filter = build_normalize_video_filter(res_preset)
    session_id = f"{os.getpid():x}_{Path.cwd().stat().st_mtime_ns:x}"
    temp_files: list[Path] = []

    def cleanup() -> None:
        for file_path in temp_files:
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass

    try:
        logger.info(
            "  [normalize] all input videos -> %dx%d %dfps (lanczos)",
            res_preset.width, res_preset.height, res_preset.fps,
        )
        normalized: list[VideoClip] = []
        for index, clip in enumerate(clips):
            temp_file = temp_dir / f"normalized_{session_id}_{index}.mp4"
            temp_files.append(temp_file)
            _run_ffmpeg(
                [
                    ffmpeg_path,
                    *video_settings.input_args(),
                    "-i",
                    clip.src,
                    "-vf",
                    normalize_filter,
                    *video_settings.output_args(qual_preset),
                    "-an",
                    "-y",
                    str(temp_file),
                ],
                timeout=600,
            )
            normalized.append(VideoClip(key=clip.key, src=str(temp_file), duration=clip.duration))
        return normalized, cleanup
    except Exception:
        cleanup()
        raise
