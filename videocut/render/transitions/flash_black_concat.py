from __future__ import annotations

from pathlib import Path

from videocut.errors import RenderError
from videocut.ffmpeg_config import FFmpegVideoSettings
from videocut.log import get_logger
from videocut.presets import QualityPreset, ResolutionPreset
from videocut.render.task import RenderTask, update_progress
from videocut.render.transitions.shared import (
    TransitionHandlerArgs,
    TransitionHandlerResult,
    _run_ffmpeg,
    collect_clips,
    normalize_clips,
)
from videocut.render.types import VideoClip


def ffmpeg_flash_black_concat(
    ffmpeg_path: str,
    clips: list[VideoClip],
    output_path: str,
    qual_preset: QualityPreset,
    video_settings: FFmpegVideoSettings,
    task: RenderTask,
    res_preset: ResolutionPreset,
    transition_duration: float,
) -> None:
    clip_count = len(clips)
    half_duration = transition_duration / 2
    fps = res_preset.fps
    def format_seconds(value: float) -> str:
        return f"{value:.6f}"
    input_args: list[str] = []
    for clip in clips:
        input_args.extend(["-i", clip.src])

    if clip_count == 1:
        _run_ffmpeg(
            [
                ffmpeg_path,
                *video_settings.input_args(),
                *input_args,
                "-filter_complex",
                "[0:v]setpts=PTS-STARTPTS[vout]",
                "-map",
                "[vout]",
                *video_settings.output_args(qual_preset),
                "-an",
                "-y",
                output_path,
            ],
            timeout=600,
        )
        update_progress(task, 1.0)
        return

    filter_parts: list[str] = []
    for index, clip in enumerate(clips):
        duration = clip.duration
        is_first = index == 0
        is_last = index == clip_count - 1
        effective_half = min(half_duration, duration / 2)
        if effective_half < half_duration:
            logger.warning(
                "%s duration %.2fs too short, shrink flash-black to %.2fs",
                clip.key, duration, effective_half * 2,
            )
        fade_out_start = max(0.0, duration - effective_half)
        chain = f"[{index}:v]setpts=PTS-STARTPTS"
        if not is_first:
            chain += f",fade=t=in:st=0:d={format_seconds(effective_half)}"
        if not is_last:
            chain += f",fade=t=out:st={format_seconds(fade_out_start)}:d={format_seconds(effective_half)}"
        chain += f"[v{index}]"
        filter_parts.append(chain)

    concat_inputs = "".join(f"[v{index}]" for index in range(clip_count))
    filter_parts.append(f"{concat_inputs}concat=n={clip_count}:v=1:a=0[vout]")
    logger.info("[2/3] FFmpeg flash-black concat %d clips (transition %.1fs, half %.2fs)...", clip_count, transition_duration, half_duration)
    _run_ffmpeg(
        [
            ffmpeg_path,
            *video_settings.input_args(),
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            *video_settings.output_args(qual_preset),
            "-an",
            "-y",
            output_path,
        ],
        timeout=600,
    )
    update_progress(task, 1.0)


logger = get_logger(__name__)


def handle_flash_black_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    logger.info("[1/3] Collecting input clips...")
    transition_duration = (
        float(args.request.variables["transition_duration"])
        if isinstance(args.request.variables.get("transition_duration"), (int, float))
        else 0.5
    )
    clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    if not clips:
        raise RenderError("No usable video clips were provided.")
    logger.info("  %d clip(s), flash-black transition %.1fs (each half %.2fs)", len(clips), transition_duration, transition_duration / 2)
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
        args.video_settings,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_flash_black_concat(
        args.ffmpeg_path,
        normalized_clips,
        output_path,
        args.qual_preset,
        args.video_settings,
        args.task,
        args.res_preset,
        transition_duration,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)
