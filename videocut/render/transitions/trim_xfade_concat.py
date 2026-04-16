from __future__ import annotations

from pathlib import Path

from videocut.errors import RenderError
from videocut.log import get_logger
from videocut.render.transitions.shared import (
    TransitionHandlerArgs,
    TransitionHandlerResult,
    collect_clips,
    normalize_clips,
)
from videocut.render.transitions.xfade_concat import ffmpeg_xfade_concat


logger = get_logger(__name__)


def handle_trim_xfade_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    logger.info("[1/3] Collecting input clips...")
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
    all_clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    clips = []
    for clip in all_clips:
        if clip.duration > 0 and clip.duration <= trim_start:
            logger.warning("  skipping %s, duration %.1fs <= %.1fs", clip.key, clip.duration, trim_start)
            continue
        clips.append(clip)
    if not clips:
        raise RenderError("No usable video clips after trim filtering.")
    logger.info("  %d clip(s), trim first %.1fs, front fade transition %.1fs", len(clips), trim_start, transition_duration)
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_xfade_concat(
        args.ffmpeg_path,
        normalized_clips,
        output_path,
        args.qual_preset,
        args.task,
        args.res_preset,
        transition_duration,
        trim_start=trim_start,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)

