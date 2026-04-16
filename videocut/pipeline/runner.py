from __future__ import annotations

import json
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path

from videocut.bgm import apply_bgm, scan_bgm_files
from videocut.errors import RenderError
from videocut.ffmpeg_config import FFmpegVideoSettings, resolve_runtime_video_settings, resolve_video_settings
from videocut.log import get_logger
from videocut.pipeline.config import ParsedPipelineContext
from videocut.pipeline.types import PipelineTransitionConfig, ResolvedPipelineClip
from videocut.presets import AUTO_PRESET, QualityPreset, ResolutionPreset, get_quality_preset, get_resolution_preset
from videocut.render.task import complete_task, create_task, fail_task, start_task, update_progress
from videocut.render.transitions.shared import normalize_clips
from videocut.render.types import RenderResult, VideoClip


def probe_single_video(ffprobe_path: str, video_path: str) -> dict[str, float | int]:
    raw = subprocess.check_output(
        [
            ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            "-select_streams",
            "v:0",
            video_path,
        ],
        encoding="utf-8",
        timeout=10,
    )
    info = json.loads(raw)
    stream = (info.get("streams") or [None])[0] or {}
    duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
    num_str, den_str = str(stream.get("r_frame_rate") or "30/1").split("/")
    num = float(num_str)
    den = float(den_str)
    return {
        "duration": duration if duration == duration and duration != float("inf") else 0.0,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": int(round(num / den) if den else num or 30),
    }


def ffmpeg_pipeline_concat(
    ffmpeg_path: str,
    clips: list[ResolvedPipelineClip],
    junctions: list[PipelineTransitionConfig],
    output_path: str,
    qual_preset: QualityPreset,
    res_preset: ResolutionPreset,
    video_settings: FFmpegVideoSettings,
    task,
) -> None:
    clip_count = len(clips)
    fps = res_preset.fps
    frame_duration = 1 / fps
    min_segment = frame_duration / 2
    def _fmt(value: float) -> str:
        return f"{value:.6f}"

    input_args: list[str] = []
    for clip in clips:
        if clip.trim_start > 0:
            input_args.extend(["-ss", str(clip.trim_start)])
        if clip.trim_end > 0:
            input_args.extend(["-t", str(clip.effective_duration)])
        input_args.extend(["-i", clip.src])

    if clip_count == 1:
        subprocess.run(
            [
                ffmpeg_path,
                *video_settings.input_args(),
                *input_args,
                "-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS,fps=fps={fps}[vout]",
                "-map",
                "[vout]",
                *video_settings.output_args(qual_preset),
                "-an",
                "-y",
                output_path,
            ],
            check=True,
            timeout=600,
        )
        update_progress(task, 1.0)
        return

    durations = [clip.effective_duration for clip in clips]
    tail_consumed = [0.0] * clip_count
    head_consumed = [0.0] * clip_count

    for index, junction in enumerate(junctions):
        current_duration = durations[index]
        next_duration = durations[index + 1]
        if junction.type == "flash-black":
            effective = min(junction.duration / 2, current_duration / 2, next_duration / 2)
            tail_consumed[index] = effective
            head_consumed[index + 1] = effective
        elif junction.type == "dissolve":
            tail_consumed[index] = min(junction.duration, current_duration / 2)

    filter_parts: list[str] = []
    segment_labels: list[str] = []
    for index in range(clip_count):
        duration = durations[index]
        body_start = head_consumed[index]
        body_duration = duration - head_consumed[index] - tail_consumed[index]
        if body_duration > min_segment:
            filter_parts.append(
                f"[{index}:v]trim=start={_fmt(body_start)}:duration={_fmt(body_duration)},"
                f"setpts=PTS-STARTPTS,fps=fps={fps}[body{index}]"
            )
            segment_labels.append(f"[body{index}]")

        if index >= clip_count - 1:
            continue
        junction = junctions[index]
        if junction.type == "flash-black":
            effective = tail_consumed[index]
            filter_parts.append(
                f"[{index}:v]trim=start={_fmt(duration - effective)}:duration={_fmt(effective)},"
                f"setpts=PTS-STARTPTS,fade=t=out:st=0:d={_fmt(effective)},fps=fps={fps}[fb_tail{index}]"
            )
            segment_labels.append(f"[fb_tail{index}]")
            filter_parts.append(
                f"[{index + 1}:v]trim=duration={_fmt(effective)},setpts=PTS-STARTPTS,"
                f"fade=t=in:st=0:d={_fmt(effective)},fps=fps={fps}[fb_head{index + 1}]"
            )
            segment_labels.append(f"[fb_head{index + 1}]")
        elif junction.type == "dissolve":
            effective = tail_consumed[index]
            still_duration = max(0.0, effective - frame_duration)
            filter_parts.append(
                f"[{index}:v]trim=start={_fmt(duration - effective)}:duration={_fmt(effective)},"
                f"setpts=PTS-STARTPTS,format=rgba,fade=t=out:st=0:d={_fmt(effective)}:alpha=1,"
                f"fps=fps={fps}[diss_tail{index}]"
            )
            filter_parts.append(
                f"[{index + 1}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={_fmt(still_duration)},fps=fps={fps}[diss_still{index}]"
            )
            filter_parts.append(
                f"[diss_still{index}][diss_tail{index}]overlay=eof_action=pass:shortest=1,"
                f"format=yuv420p,fps=fps={fps}[diss_trans{index}]"
            )
            segment_labels.append(f"[diss_trans{index}]")

    if not segment_labels:
        raise RenderError("No usable video segments after pipeline transition building.")

    filter_parts.append(f"{''.join(segment_labels)}concat=n={len(segment_labels)}:v=1:a=0[vout]")
    subprocess.run(
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
        check=True,
        timeout=600,
    )
    update_progress(task, 1.0)


logger = get_logger(__name__)


class PipelineRunner:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.output_dir = self.root_dir / "output"

    def run(
        self,
        ctx: ParsedPipelineContext,
        ffmpeg_path: str,
        ffprobe_path: str,
        overrides: dict[str, str],
    ) -> RenderResult:
        config = ctx.config
        video_settings = resolve_runtime_video_settings(ffmpeg_path, resolve_video_settings())
        project_dir = ctx.project_dir
        config_path = ctx.config_path
        resolved_srcs = ctx.resolved_srcs
        junctions = ctx.junctions

        preset = overrides.get("preset") or config.preset or AUTO_PRESET
        quality = overrides.get("quality") or config.quality or "high"
        base_name = config.output.filename if config.output and config.output.filename else "final.mp4"
        stem, ext = Path(base_name).stem, Path(base_name).suffix or ".mp4"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{stem}_{timestamp}{ext}"
        project_name = project_dir.name

        task = create_task("pipeline", {})
        start_task(task)

        start_time = time.time()
        out_dir = self.output_dir / project_name
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / output_filename)
        cleanup = None

        try:
            logger.info("[1/3] Probing and validating %d clips...", len(resolved_srcs))
            res_preset = None
            resolved_clips: list[ResolvedPipelineClip] = []
            for index, src in enumerate(resolved_srcs):
                clip_cfg = config.clips[index]
                trim_start = clip_cfg.trim_start
                trim_end = clip_cfg.trim_end
                probed = probe_single_video(ffprobe_path, src)
                effective_duration = max(0.0, float(probed["duration"]) - trim_start - trim_end)
                if effective_duration <= 0:
                    raise RenderError(
                        f"clips[{index}] ({Path(src).name}) has non-positive duration after trim: "
                        f"source={probed['duration']:.2f}s trim_start={trim_start}s trim_end={trim_end}s"
                    )
                logger.info(
                    "  clip_%d: %s (source %.2fs, effective %.2fs)",
                    index + 1, Path(src).name, probed["duration"], effective_duration,
                )
                if index == 0 and preset == AUTO_PRESET and probed["width"] and probed["height"]:
                    res_preset = ResolutionPreset(
                        width=int(probed["width"]),
                        height=int(probed["height"]),
                        fps=int(probed["fps"]),
                        label=f"Auto {probed['width']}x{probed['height']} {probed['fps']}fps",
                    )
                    logger.info("  Auto-detected resolution %dx%d %dfps", probed["width"], probed["height"], probed["fps"])
                resolved_clips.append(
                    ResolvedPipelineClip(
                        key=f"clip_{index + 1}",
                        src=src,
                        probed_duration=float(probed["duration"]),
                        trim_start=trim_start,
                        trim_end=trim_end,
                        effective_duration=effective_duration,
                    )
                )

            if preset != AUTO_PRESET:
                res_preset = get_resolution_preset(preset)
            elif res_preset is None:
                logger.warning("unable to auto-detect resolution, fallback to douyin_vertical")
                res_preset = get_resolution_preset("douyin_vertical")

            qual_preset = get_quality_preset(quality)

            video_clips = [
                VideoClip(key=clip.key, src=clip.src, duration=clip.probed_duration)
                for clip in resolved_clips
            ]
            logger.info(
                "FFmpeg video encoder selected: %s%s",
                video_settings.encoder,
                f" (hwaccel={video_settings.hwaccel})" if video_settings.hwaccel else "",
            )
            normalized_clips, cleanup = normalize_clips(
                self.root_dir,
                ffmpeg_path,
                video_clips,
                qual_preset,
                res_preset,
                video_settings,
            )
            normalized_pipeline_clips = [
                ResolvedPipelineClip(
                    key=resolved_clips[index].key,
                    src=normalized_clips[index].src,
                    probed_duration=resolved_clips[index].probed_duration,
                    trim_start=resolved_clips[index].trim_start,
                    trim_end=resolved_clips[index].trim_end,
                    effective_duration=resolved_clips[index].effective_duration,
                )
                for index in range(len(resolved_clips))
            ]

            logger.info("[2/3] Building pipeline transitions...")
            ffmpeg_pipeline_concat(
                ffmpeg_path,
                normalized_pipeline_clips,
                junctions,
                output_path,
                qual_preset,
                res_preset,
                video_settings,
                task,
            )

            bgm_file_used = None
            if config.bgm and config.bgm.enabled:
                bgm_dir_path = (
                    Path(config.bgm.dir).resolve() if config.bgm.dir
                    else self.root_dir / "input" / "bgm"
                )
                bgm_files = scan_bgm_files(bgm_dir_path)
                chosen = random.choice(bgm_files)
                logger.info("[2.5/3] 混入 BGM: %s (volume=%.2f)", chosen.name, config.bgm.volume)
                apply_bgm(ffmpeg_path, ffprobe_path, output_path, chosen, config.bgm.volume, config.bgm.fade_out)
                bgm_file_used = str(chosen)

            elapsed = time.time() - start_time
            complete_task(task, output_path)
            meta = {
                "taskId": task.id,
                "mode": "pipeline",
                "preset": preset,
                "quality": quality,
                "configPath": str(config_path),
                "clips": [
                    {
                        "src": clip.src,
                        "trimStart": clip.trim_start,
                        "trimEnd": clip.trim_end,
                        "effectiveDuration": clip.effective_duration,
                    }
                    for clip in resolved_clips
                ],
                "junctions": [{"type": item.type, "duration": item.duration} for item in junctions],
                "renderedAt": datetime.utcnow().isoformat(),
                "duration": round(elapsed, 1),
                "resolution": f"{res_preset.width}x{res_preset.height}",
                "bgm": {
                    "file": bgm_file_used,
                    "volume": config.bgm.volume,
                    "fade_out": config.bgm.fade_out,
                } if bgm_file_used else None,
            }
            (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("[3/3] Metadata written")
            return RenderResult(task_id=task.id, status="completed", output_path=output_path, duration=elapsed)
        except Exception as exc:  # intentional: isolate render failure from caller
            elapsed = time.time() - start_time
            fail_task(task, str(exc))
            return RenderResult(task_id=task.id, status="failed", duration=elapsed, error=str(exc))
        finally:
            if cleanup:
                try:
                    cleanup()
                except Exception:  # intentional: best-effort cleanup, never suppress render result
                    pass
