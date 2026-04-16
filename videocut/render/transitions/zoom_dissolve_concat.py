from __future__ import annotations

from pathlib import Path

from videocut.errors import RenderError
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


def ffmpeg_zoom_dissolve_concat(
    ffmpeg_path: str,
    clips: list[VideoClip],
    output_path: str,
    qual_preset: QualityPreset,
    task: RenderTask,
    res_preset: ResolutionPreset,
    transition_duration: float,
    zoom_scale: float,
) -> None:
    clip_count = len(clips)
    duration = transition_duration
    width = res_preset.width
    height = res_preset.height
    fps = res_preset.fps
    frame_duration = 1 / fps
    min_segment_duration = frame_duration / 2
    def format_seconds(value: float) -> str:
        return f"{value:.6f}"

    oversample = 8
    fps_hi = fps * oversample
    frames_hi = max(2, round(fps_hi * duration))
    z_delta = f"{zoom_scale - 1:.6f}"
    frame_progress = f"(n/{frames_hi - 1})"
    eased_progress = f"({frame_progress}*{frame_progress}*{frame_progress}*{frame_progress})"
    zoom_expr = f"(1+{z_delta}*{eased_progress})"
    scale_w = f"floor({width}*{zoom_expr}/2)*2"
    scale_h = f"floor({height}*{zoom_expr}/2)*2"
    crop_x = f"{width}*{z_delta}*{eased_progress}/2"
    crop_y = f"{height}*{z_delta}*{eased_progress}/2"

    input_args: list[str] = []
    clip_meta: list[dict[str, float | int]] = []
    for index, clip in enumerate(clips):
        input_args.extend(["-i", clip.src])
        clip_meta.append({"video_idx": index, "duration": clip.duration})

    filter_parts: list[str] = []
    segment_labels: list[str] = []
    if clip_count == 1:
        filter_parts.append("[0:v]setpts=PTS-STARTPTS[vout]")
    else:
        for index in range(clip_count - 1):
            current = clip_meta[index]
            next_clip = clip_meta[index + 1]
            current_duration = float(current["duration"])
            body_duration = max(0.0, current_duration - duration)
            available_tail = min(current_duration, duration)
            tail_start = max(0.0, current_duration - duration)
            tail_pad = max(0.0, duration - available_tail)
            still_pad = max(0.0, duration - frame_duration)
            if tail_pad > min_segment_duration:
                logger.warning(
                    "%s too short for %.1fs zoom dissolve, extending with cloned last frame",
                    clips[index].key, duration,
                )
            if body_duration > min_segment_duration:
                filter_parts.append(
                    f"[{int(current['video_idx'])}:v]trim=duration={format_seconds(body_duration)},"
                    f"setpts=PTS-STARTPTS,fps=fps={fps}[body{index}]"
                )
                segment_labels.append(f"[body{index}]")
            filter_parts.append(
                f"[{int(current['video_idx'])}:v]trim=start={format_seconds(tail_start)}:"
                f"duration={format_seconds(available_tail)},setpts=PTS-STARTPTS"
                + (
                    f",tpad=stop_mode=clone:stop_duration={format_seconds(tail_pad)}"
                    if tail_pad > 0
                    else ""
                )
                + f",fps=fps={fps_hi}"
                + f",scale='{scale_w}':'{scale_h}':eval=frame"
                + f",crop={width}:{height}:x='{crop_x}':y='{crop_y}'"
                + f",tmix=frames={oversample}"
                + f",fps=fps={fps}"
                + f",format=rgba,fade=t=out:st={format_seconds(duration * 0.65)}:"
                + f"d={format_seconds(duration * 0.35)}:alpha=1[zoomfade{index}]"
            )
            filter_parts.append(
                f"[{int(next_clip['video_idx'])}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={format_seconds(still_pad)},"
                f"fps=fps={fps}[still{index}]"
            )
            filter_parts.append(
                f"[still{index}][zoomfade{index}]overlay=eof_action=pass:shortest=1,"
                f"format=yuv420p,fps=fps={fps}[transition{index}]"
            )
            segment_labels.append(f"[transition{index}]")

        filter_parts.append(
            f"[{int(clip_meta[clip_count - 1]['video_idx'])}:v]setpts=PTS-STARTPTS,fps=fps={fps}[last]"
        )
        segment_labels.append("[last]")
        filter_parts.append(f"{''.join(segment_labels)}concat=n={len(segment_labels)}:v=1:a=0[vout]")

    logger.info("[2/3] FFmpeg center-zoom dissolve concat %d clips (zoom %.1fs, scale %.2fx)...", clip_count, duration, zoom_scale)
    _run_ffmpeg(
        [
            ffmpeg_path,
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            qual_preset.ffmpeg_preset,
            "-crf",
            str(qual_preset.crf),
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            output_path,
        ],
        timeout=600,
    )
    update_progress(task, 1.0)


logger = get_logger(__name__)


def handle_zoom_dissolve_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    logger.info("[1/3] Collecting input clips...")
    transition_duration = (
        float(args.request.variables["transition_duration"])
        if isinstance(args.request.variables.get("transition_duration"), (int, float))
        else 0.4
    )
    zoom_scale = (
        float(args.request.variables["zoom_scale"])
        if isinstance(args.request.variables.get("zoom_scale"), (int, float))
        else 1.18
    )
    clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    if not clips:
        raise RenderError("No usable video clips were provided.")
    logger.info("  %d clip(s), zoom duration %.1fs, scale %.2fx", len(clips), transition_duration, zoom_scale)
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_zoom_dissolve_concat(
        args.ffmpeg_path,
        normalized_clips,
        output_path,
        args.qual_preset,
        args.task,
        args.res_preset,
        transition_duration,
        zoom_scale,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)

