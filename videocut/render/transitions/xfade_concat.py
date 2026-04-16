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


def ffmpeg_xfade_concat(
    ffmpeg_path: str,
    clips: list[VideoClip],
    output_path: str,
    qual_preset: QualityPreset,
    video_settings: FFmpegVideoSettings,
    task: RenderTask,
    res_preset: ResolutionPreset,
    transition_duration: float,
    trim_start: float = 0.0,
) -> None:
    clip_count = len(clips)
    duration = transition_duration
    fps = res_preset.fps
    frame_duration = 1 / fps
    min_segment_duration = frame_duration / 2
    def format_seconds(value: float) -> str:
        return f"{value:.6f}"

    input_args: list[str] = []
    clip_meta: list[dict[str, float | int]] = []
    input_counter = 0
    for clip in clips:
        if trim_start > 0:
            input_args.extend(["-ss", str(trim_start)])
        input_args.extend(["-i", clip.src])
        clip_meta.append({"video_idx": input_counter, "duration": max(0.0, clip.duration - trim_start)})
        input_counter += 1

    filter_parts: list[str] = []
    segment_labels: list[str] = []

    if clip_count == 1:
        filter_parts.append(f"[{int(clip_meta[0]['video_idx'])}:v]setpts=PTS-STARTPTS,fps=fps={fps}[vout]")
    else:
        for index in range(clip_count - 1):
            current = clip_meta[index]
            next_clip = clip_meta[index + 1]
            current_video_idx = int(current["video_idx"])
            next_video_idx = int(next_clip["video_idx"])
            current_duration = float(current["duration"])
            body_duration = max(0.0, current_duration - duration)
            available_tail_duration = min(current_duration, duration)
            tail_start = max(0.0, current_duration - duration)
            tail_pad_duration = max(0.0, duration - available_tail_duration)
            still_pad_duration = max(0.0, duration - frame_duration)

            if tail_pad_duration > min_segment_duration:
                logger.warning(
                    "%s is too short for %.1fs fade, extending with cloned last frame",
                    clips[index].key, duration,
                )

            if body_duration > min_segment_duration:
                filter_parts.append(
                    f"[{current_video_idx}:v]trim=duration={format_seconds(body_duration)},"
                    f"setpts=PTS-STARTPTS,fps=fps={fps}[body{index}]"
                )
                segment_labels.append(f"[body{index}]")

            filter_parts.append(
                f"[{current_video_idx}:v]trim=start={format_seconds(tail_start)}:"
                f"duration={format_seconds(available_tail_duration)},setpts=PTS-STARTPTS"
                + (
                    f",tpad=stop_mode=clone:stop_duration={format_seconds(tail_pad_duration)}"
                    if tail_pad_duration > 0
                    else ""
                )
                + f",format=rgba,fade=t=out:st=0:d={format_seconds(duration)}:alpha=1[fade{index}]"
            )
            filter_parts.append(
                f"[{next_video_idx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={format_seconds(still_pad_duration)},"
                f"fps=fps={fps}[still{index}]"
            )
            filter_parts.append(
                f"[still{index}][fade{index}]overlay=eof_action=pass:shortest=1,"
                f"format=yuv420p,fps=fps={fps}[transition{index}]"
            )
            segment_labels.append(f"[transition{index}]")

        last_index = clip_count - 1
        filter_parts.append(
            f"[{int(clip_meta[last_index]['video_idx'])}:v]setpts=PTS-STARTPTS,fps=fps={fps}[last]"
        )
        segment_labels.append("[last]")
        filter_parts.append(f"{''.join(segment_labels)}concat=n={len(segment_labels)}:v=1:a=0[vout]")

    logger.info("[2/3] FFmpeg front-fade concat %d clips (transition %.1fs)...", clip_count, duration)
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


def handle_xfade_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    logger.info("[1/3] Collecting input clips...")
    transition_duration = (
        float(args.request.variables["transition_duration"])
        if isinstance(args.request.variables.get("transition_duration"), (int, float))
        else 0.5
    )
    clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    if not clips:
        raise RenderError("No usable video clips were provided.")
    logger.info("  %d clip(s), front fade transition %.1fs", len(clips), transition_duration)
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
        args.video_settings,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_xfade_concat(
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
