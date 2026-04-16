from __future__ import annotations

from pathlib import Path

from videocut.errors import RenderError
from videocut.render.transitions.shared import (
    TransitionHandlerArgs,
    TransitionHandlerResult,
    collect_clips,
    ffmpeg_trim_concat,
    normalize_clips,
)


def handle_trim_concat(args: TransitionHandlerArgs) -> TransitionHandlerResult:
    trim_start = 2.0
    print("\n[1/3] Collecting input clips...")
    all_clips = collect_clips(args.request.variables, args.request.template_info, args.video_info)
    clips = []
    for clip in all_clips:
        if clip.duration > 0 and clip.duration <= trim_start:
            print(f"  Warning: skipping {clip.key}, duration {clip.duration:.1f}s <= {trim_start}s")
            continue
        clips.append(clip)

    if not clips:
        raise RenderError("No usable video clips after trim filtering.")

    print(f"  {len(clips)} clip(s), trim first {trim_start}s from each")
    normalized_clips, cleanup = normalize_clips(
        args.root_dir,
        args.ffmpeg_path,
        clips,
        args.qual_preset,
        args.res_preset,
    )
    output_path = str(Path(args.out_dir) / args.out_file)
    ffmpeg_trim_concat(
        args.root_dir,
        args.ffmpeg_path,
        normalized_clips,
        output_path,
        args.qual_preset,
        args.task,
        trim_start,
    )
    return TransitionHandlerResult(cleanup=cleanup, output_path=output_path)

