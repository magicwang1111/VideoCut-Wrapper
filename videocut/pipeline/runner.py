from __future__ import annotations

import json
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path

from videocut.errors import RenderError
from videocut.pipeline.config import ParsedPipelineContext
from videocut.pipeline.types import PipelineTransitionConfig, ResolvedPipelineClip
from videocut.presets import AUTO_PRESET, QualityPreset, ResolutionPreset, get_quality_preset, get_resolution_preset
from videocut.render.task import complete_task, create_task, fail_task, start_task, update_progress
from videocut.render.transitions.shared import normalize_clips
from videocut.render.types import RenderResult


_BGM_EXTENSIONS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}


def scan_bgm_files(bgm_dir: Path) -> list[Path]:
    if not bgm_dir.is_dir():
        raise RenderError(f"BGM 目录不存在: {bgm_dir}")
    files = [p for p in bgm_dir.iterdir() if p.suffix.lower() in _BGM_EXTENSIONS]
    if not files:
        raise RenderError(f"BGM 目录中没有找到音频文件: {bgm_dir}")
    return files


def apply_bgm(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: str,
    bgm_file: Path,
    volume: float,
    fade_out: float,
) -> None:
    """将 bgm_file 混入 video_path（视频 → 临时文件 → 覆盖原文件）。"""
    tmp_path = video_path + ".bgm_tmp.mp4"
    raw = subprocess.check_output(
        [ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", video_path],
        encoding="utf-8",
        timeout=10,
    )
    video_duration = float(json.loads(raw)["format"]["duration"])

    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2000000000,"
        f"volume={volume:.4f},"
        f"atrim=end={video_duration:.6f}"
    )
    if fade_out > 0:
        fade_start = max(0.0, video_duration - fade_out)
        audio_filter += f",afade=t=out:st={fade_start:.6f}:d={fade_out:.4f}"
    audio_filter += "[bgm]"

    subprocess.run(
        [
            ffmpeg_path, "-i", video_path, "-i", str(bgm_file),
            "-filter_complex", audio_filter,
            "-map", "0:v", "-map", "[bgm]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-y", tmp_path,
        ],
        check=True,
        timeout=600,
    )
    Path(tmp_path).replace(video_path)


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
    task,
) -> None:
    clip_count = len(clips)
    fps = res_preset.fps
    frame_duration = 1 / fps
    min_segment = frame_duration / 2
    fmt = lambda value: f"{value:.6f}"

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
                f"[{index}:v]trim=start={fmt(body_start)}:duration={fmt(body_duration)},"
                f"setpts=PTS-STARTPTS,fps=fps={fps}[body{index}]"
            )
            segment_labels.append(f"[body{index}]")

        if index >= clip_count - 1:
            continue
        junction = junctions[index]
        if junction.type == "flash-black":
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
        elif junction.type == "dissolve":
            effective = tail_consumed[index]
            still_duration = max(0.0, effective - frame_duration)
            filter_parts.append(
                f"[{index}:v]trim=start={fmt(duration - effective)}:duration={fmt(effective)},"
                f"setpts=PTS-STARTPTS,format=rgba,fade=t=out:st=0:d={fmt(effective)}:alpha=1,"
                f"fps=fps={fps}[diss_tail{index}]"
            )
            filter_parts.append(
                f"[{index + 1}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={fmt(still_duration)},fps=fps={fps}[diss_still{index}]"
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
        check=True,
        timeout=600,
    )
    update_progress(task, 1.0)


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
            print(f"\n[1/3] Probing and validating {len(resolved_srcs)} clips...")
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
                print(
                    f"  clip_{index + 1}: {Path(src).name} "
                    f"(source {probed['duration']:.2f}s, effective {effective_duration:.2f}s)"
                )
                if index == 0 and preset == AUTO_PRESET and probed["width"] and probed["height"]:
                    res_preset = ResolutionPreset(
                        width=int(probed["width"]),
                        height=int(probed["height"]),
                        fps=int(probed["fps"]),
                        label=f"Auto {probed['width']}x{probed['height']} {probed['fps']}fps",
                    )
                    print(f"  Auto-detected resolution {probed['width']}x{probed['height']} {probed['fps']}fps")
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
                print("  Warning: unable to auto-detect resolution, fallback to douyin_vertical")
                res_preset = get_resolution_preset("douyin_vertical")

            qual_preset = get_quality_preset(quality)

            video_clips = [
                type("VideoClipProxy", (), {"key": clip.key, "src": clip.src, "duration": clip.probed_duration})()
                for clip in resolved_clips
            ]
            normalized_clips, cleanup = normalize_clips(self.root_dir, ffmpeg_path, video_clips, qual_preset, res_preset)
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

            print("[2/3] Building pipeline transitions...")
            ffmpeg_pipeline_concat(
                ffmpeg_path,
                normalized_pipeline_clips,
                junctions,
                output_path,
                qual_preset,
                res_preset,
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
                print(f"[2.5/3] 混入 BGM: {chosen.name}（volume={config.bgm.volume}）")
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
            print("[3/3] Writing metadata...")
            return RenderResult(task_id=task.id, status="completed", output_path=output_path, duration=elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - start_time
            fail_task(task, str(exc))
            return RenderResult(task_id=task.id, status="failed", duration=elapsed, error=str(exc))
        finally:
            if cleanup:
                try:
                    cleanup()
                except Exception:  # noqa: BLE001
                    pass

