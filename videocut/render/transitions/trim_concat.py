from __future__ import annotations

from pathlib import Path

from videocut.errors import RenderError
from videocut.log import get_logger
from videocut.render.transitions.shared import (
    TransitionHandlerArgs,
    TransitionHandlerResult,
    collect_clips,
    ffmpeg_trim_concat,
    normalize_clips,
)


logger = get_logger(__name__)


def handle_trim_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    trim_start = 2.0
    logger.info("[1/3] Collecting input clips...")
    all_clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    clips = []
    for clip in all_clips:
        if clip.duration > 0 and clip.duration <= trim_start:
            logger.warning("  skipping %s, duration %.1fs <= %.1fs", clip.key, clip.duration, trim_start)
            continue
        clips.append(clip)

    if not clips:
        raise RenderError("No usable video clips after trim filtering.")

    logger.info("  %d clip(s), trim first %.1fs from each", len(clips), trim_start)
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
        args.video_settings,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_trim_concat(
        args.root_dir,
        args.ffmpeg_path,
        normalized_clips,
        output_path,
        args.qual_preset,
        args.video_settings,
        args.task,
        trim_start,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)
