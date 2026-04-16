from __future__ import annotations

from pathlib import Path
from typing import Literal

from videocut.errors import RenderError
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

JunctionType = Literal["flash-black", "dissolve", "cut"]


def parse_junction_type(raw: object) -> JunctionType:
    if raw in {"flash-black", "dissolve", "cut"}:
        return raw  # type: ignore[return-value]
    return "cut"


def ffmpeg_trim_mixed_concat(
    ffmpeg_path: str,
    clips: list[VideoClip],
    junctions: list[JunctionType],
    trim_start: float,
    output_path: str,
    qual_preset: QualityPreset,
    task: RenderTask,
    res_preset: ResolutionPreset,
    transition_duration: float,
) -> None:
    clip_count = len(clips)
    total_transition = transition_duration
    half_duration = total_transition / 2
    fps = res_preset.fps
    frame_duration = 1 / fps
    min_segment = frame_duration / 2
    fmt = lambda value: f"{value:.6f}"

    input_args: list[str] = []
    for clip in clips:
        if trim_start > 0:
            input_args.extend(["-ss", str(trim_start)])
        input_args.extend(["-i", clip.src])

    durations = [max(0.0, clip.duration - trim_start) for clip in clips]

    if clip_count == 1:
        _run_ffmpeg(
            [
                ffmpeg_path,
                *input_args,
                "-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS,fps=fps={fps}[vout]",
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
        return

    tail_consumed = [0.0] * clip_count
    head_consumed = [0.0] * clip_count
    for index, junction in enumerate(junctions):
        current_duration = durations[index]
        next_duration = durations[index + 1]
        if junction == "flash-black":
            effective = min(half_duration, current_duration / 2, next_duration / 2)
            if effective < half_duration - 0.001:
                print(
                    f"  Warning: junction {index + 1} flash-black shortened to {(effective * 2):.2f}s"
                )
            tail_consumed[index] = effective
            head_consumed[index + 1] = effective
        elif junction == "dissolve":
            effective = min(total_transition, current_duration / 2)
            if effective < total_transition - 0.001:
                print(f"  Warning: junction {index + 1} dissolve shortened to {effective:.2f}s")
            tail_consumed[index] = effective

    filter_parts: list[str] = []
    segment_labels: list[str] = []
    for index in range(clip_count):
        duration = durations[index]
        body_start = head_consumed[index]
        body_duration = duration - head_consumed[index] - tail_consumed[index]
        if body_duration > min_segment:
            filter_parts.append(
                f"[{index}:v]trim=start={fmt(body_start)}:duration={fmt(body_duration)},"
                f"setpts=PTS-STARTPTS,fps=fps={fps}[body{index}]"
            )
            segment_labels.append(f"[body{index}]")
        elif body_duration > 0:
            print(f"  Warning: {clips[index].key} body segment too short ({body_duration:.3f}s), skip")

        if index >= clip_count - 1:
            continue
        junction = junctions[index]
        if junction == "flash-black":
            effective = tail_consumed[index]
            filter_parts.append(
                f"[{index}:v]trim=start={fmt(duration - effective)}:duration={fmt(effective)},"
                f"setpts=PTS-STARTPTS,fade=t=out:st=0:d={fmt(effective)},fps=fps={fps}[fb_tail{index}]"
            )
            segment_labels.append(f"[fb_tail{index}]")
            filter_parts.append(
                f"[{index + 1}:v]trim=duration={fmt(effective)},setpts=PTS-STARTPTS,"
                f"fade=t=in:st=0:d={fmt(effective)},fps=fps={fps}[fb_head{index + 1}]"
            )
            segment_labels.append(f"[fb_head{index + 1}]")
        elif junction == "dissolve":
            effective = tail_consumed[index]
            still_duration = max(0.0, effective - frame_duration)
            filter_parts.append(
                f"[{index}:v]trim=start={fmt(duration - effective)}:duration={fmt(effective)},"
                f"setpts=PTS-STARTPTS,format=rgba,fade=t=out:st=0:d={fmt(effective)}:alpha=1,"
                f"fps=fps={fps}[diss_tail{index}]"
            )
            filter_parts.append(
                f"[{index + 1}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={fmt(still_duration)},"
                f"fps=fps={fps}[diss_still{index}]"
            )
            filter_parts.append(
                f"[diss_still{index}][diss_tail{index}]overlay=eof_action=pass:shortest=1,"
                f"format=yuv420p,fps=fps={fps}[diss_trans{index}]"
            )
            segment_labels.append(f"[diss_trans{index}]")

    if not segment_labels:
        raise RenderError("No usable video segments after transition building.")

    filter_parts.append(f"{''.join(segment_labels)}concat=n={len(segment_labels)}:v=1:a=0[vout]")
    print(
        f"\n[2/3] FFmpeg mixed-transition concat {clip_count} clips "
        f"(trim {trim_start}s, junctions {' -> '.join(junctions)})..."
    )
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


def handle_trim_mixed_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    print("\n[1/3] Collecting input clips...")
    trim_start = (
        float(args.request.variables["trim_start"])
        if isinstance(args.request.variables.get("trim_start"), (int, float))
        else 2.0
    )
    transition_duration = (
        float(args.request.variables["transition_duration"])
        if isinstance(args.request.variables.get("transition_duration"), (int, float))
        else 0.5
    )
    clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    if not clips:
        raise RenderError("No usable video clips were provided.")

    junctions = [parse_junction_type(args.request.variables.get(f"transition_{index}")) for index in range(1, len(clips))]
    print(
        f"  {len(clips)} clip(s), trim {trim_start}s, transitions {junctions}, duration {transition_duration}s"
    )
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_trim_mixed_concat(
        args.ffmpeg_path,
        normalized_clips,
        junctions,
        trim_start,
        output_path,
        args.qual_preset,
        args.task,
        args.res_preset,
        transition_duration,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)

