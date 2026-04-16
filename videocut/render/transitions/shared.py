from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from videocut.errors import RenderError
from videocut.ffmpeg_config import FFmpegVideoSettings
from videocut.log import get_logger
from videocut.presets import QualityPreset, ResolutionPreset
from videocut.render.task import RenderTask, update_progress
from videocut.render.types import ProbedVideoInfo, RenderRequest, VideoClip

logger = get_logger(__name__)


@dataclass(slots=True)
class TransitionHandlerArgs:
    root_dir: Path
    ffmpeg_path: str
    ffprobe_path: str
    video_settings: FFmpegVideoSettings
    request: RenderRequest
    video_info: dict[str, ProbedVideoInfo]
    qual_preset: QualityPreset
    res_preset: ResolutionPreset
    task: RenderTask
    out_dir: Path
    out_file: str


@dataclass(slots=True)
class TransitionHandlerResult:
    cleanup: Callable[[], None]
    output_path: str


def collect_clips(
    variables: dict[str, object],
    template_info,
    video_info: dict[str, ProbedVideoInfo],
) -> list[VideoClip]:
    for key, definition in template_info.manifest.variables.items():
        if definition.type != "video_list":
            continue
        clip_list = variables.get(key)
        if not isinstance(clip_list, list):
            continue
        durations = variables.get(f"{key}_source_durations", [])
        if not isinstance(durations, list):
            durations = []
        return [
            VideoClip(
                key=f"clip_{index + 1}",
                src=str(src),
                duration=float(durations[index]) if index < len(durations) else 0.0,
            )
            for index, src in enumerate(clip_list)
        ]

    clips: list[VideoClip] = []
    for key, definition in template_info.manifest.variables.items():
        if definition.type != "video":
            continue
        src = variables.get(key)
        if not isinstance(src, str) or not src.strip():
            continue
        info = video_info.get(key)
        clips.append(VideoClip(key=key, src=src, duration=info.duration if info else 0.0))
    return clips


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


def ffmpeg_trim_concat(
    root_dir: Path,
    ffmpeg_path: str,
    clips: list[VideoClip],
    output_path: str,
    qual_preset: QualityPreset,
    video_settings: FFmpegVideoSettings,
    task: RenderTask,
    trim_start: float,
) -> None:
    temp_dir = root_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"{os.getpid():x}"
    temp_files: list[Path] = []
    list_file = temp_dir / f"concat_{session_id}.txt"

    try:
        logger.info("[2/3] FFmpeg trimming %d clips...", len(clips))
        for index, clip in enumerate(clips):
            temp_file = temp_dir / f"trim_{session_id}_{index}.mp4"
            temp_files.append(temp_file)
            logger.info("  [%d/%d] trimming %s, skip first %.1fs", index + 1, len(clips), Path(clip.src).name, trim_start)
            _run_ffmpeg(
                [
                    ffmpeg_path,
                    *video_settings.input_args(),
                    "-ss",
                    str(trim_start),
                    "-i",
                    clip.src,
                    *video_settings.output_args(qual_preset, pix_fmt=""),
                    "-an",
                    "-y",
                    str(temp_file),
                ],
                timeout=300,
            )
            update_progress(task, (index + 1) / (len(clips) + 1))

        list_file.write_text(
            "\n".join(f"file '{str(file_path).replace(chr(92), '/')}'" for file_path in temp_files),
            encoding="utf-8",
        )
        logger.info("  concatenating %d clips -> %s", len(clips), Path(output_path).name)
        _run_ffmpeg(
            [
                ffmpeg_path,
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                "-y",
                output_path,
            ],
            timeout=600,
        )
    finally:
        if list_file.exists():
            list_file.unlink()
        for file_path in temp_files:
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass
