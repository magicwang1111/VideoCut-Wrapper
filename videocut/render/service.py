from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from videocut.errors import DependencyError, RenderError
from videocut.presets import AUTO_PRESET, ResolutionPreset, get_quality_preset, get_resolution_preset
from videocut.render.task import RenderTask, complete_task, create_task, fail_task, start_task
from videocut.render.transitions import (
    handle_flash_black_concat,
    handle_trim_concat,
    handle_trim_mixed_concat,
    handle_trim_xfade_concat,
    handle_xfade_concat,
    handle_zoom_dissolve_concat,
)
from videocut.render.transitions.shared import TransitionHandlerArgs
from videocut.render.types import ProbedVideoInfo, RenderRequest, RenderResult

TRANSITION_TEMPLATE_IDS = {
    "trim-concat",
    "xfade-concat",
    "trim-xfade-concat",
    "zoom-dissolve-concat",
    "flash-black-concat",
    "trim-mixed-concat",
}


def find_file_recursive(directory: Path, filename: str, max_depth: int = 3) -> Path | None:
    if not directory.exists() or max_depth < 0:
        return None
    direct_match = directory / filename
    if direct_match.exists():
        return direct_match
    for entry in directory.iterdir():
        if not entry.is_dir():
            continue
        found = find_file_recursive(entry, filename, max_depth - 1)
        if found:
            return found
    return None


def _resolve_binary_path(root_dir: Path, env_name: str, filenames: tuple[str, ...], which_name: str) -> str | None:
    explicit_path = os.getenv(env_name)
    if explicit_path and Path(explicit_path).exists():
        return str(Path(explicit_path).resolve())

    for root in (root_dir / "ffmpeg", root_dir.parent / "ffmpeg"):
        for filename in filenames:
            found = find_file_recursive(root, filename)
            if found:
                return str(found)

    for name in (which_name, *filenames):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def resolve_ffprobe_path(root_dir: str | Path) -> str | None:
    return _resolve_binary_path(Path(root_dir).resolve(), "FFPROBE_PATH", ("ffprobe.exe", "ffprobe"), "ffprobe")


def resolve_ffmpeg_path(root_dir: str | Path) -> str | None:
    return _resolve_binary_path(Path(root_dir).resolve(), "FFMPEG_PATH", ("ffmpeg.exe", "ffmpeg"), "ffmpeg")


def probe_video(ffprobe_path: str, video_path: str) -> tuple[ProbedVideoInfo | None, str | None]:
    try:
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
        stream = (info.get("streams") or [None])[0]
        if not isinstance(stream, dict) or not stream.get("width") or not stream.get("height"):
            return None, "ffprobe did not return width/height"
        fps_str = stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "30/1"
        num_str, den_str = str(fps_str).split("/")
        num = float(num_str)
        den = float(den_str)
        fps = round(num / den) if den else int(num)
        duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
        normalized_duration = duration if duration == duration and duration != float("inf") else 0.0
        return (
            ProbedVideoInfo(
                width=int(stream["width"]),
                height=int(stream["height"]),
                fps=int(fps or 30),
                duration=float(normalized_duration),
                label=f"Source {stream['width']}x{stream['height']} {int(fps or 30)}fps",
            ),
            None if normalized_duration > 0 else "ffprobe did not return duration",
        )
    except subprocess.CalledProcessError as exc:
        return None, exc.output.strip() or str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def find_first_video_key(variables: dict[str, Any], template_info) -> str | None:
    for key, definition in template_info.manifest.variables.items():
        if definition.type == "video":
            value = variables.get(key)
            if isinstance(value, str) and value:
                return key
    return None


def collect_video_info(
    ffprobe_path: str | None,
    variables: dict[str, Any],
    template_info,
) -> tuple[dict[str, ProbedVideoInfo], list[dict[str, str]]]:
    video_info: dict[str, ProbedVideoInfo] = {}
    failures: list[dict[str, str]] = []
    if ffprobe_path is None:
        return video_info, failures

    for key, definition in template_info.manifest.variables.items():
        if definition.type == "video_list":
            clip_list = variables.get(key)
            if not isinstance(clip_list, list):
                continue
            durations: list[float] = []
            for index, src in enumerate(clip_list):
                if not isinstance(src, str) or not src.strip():
                    durations.append(0.0)
                    continue
                probed, reason = probe_video(ffprobe_path, src)
                durations.append(probed.duration if probed else 0.0)
                if probed and key not in video_info:
                    video_info[key] = probed
                if probed is None or reason:
                    failures.append(
                        {
                            "key": f"{key}[{index}]",
                            "src": src,
                            "reason": reason or "ffprobe returned incomplete metadata",
                        }
                    )
            variables[f"{key}_source_durations"] = durations
            continue

        if definition.type != "video":
            continue
        value = variables.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        probed, reason = probe_video(ffprobe_path, value)
        if probed:
            video_info[key] = probed
        if probed is None or reason:
            failures.append({"key": key, "src": value, "reason": reason or "ffprobe returned incomplete metadata"})
    return video_info, failures


def inject_video_metadata_variables(
    variables: dict[str, Any],
    video_info: dict[str, ProbedVideoInfo],
) -> dict[str, Any]:
    if not video_info:
        return variables
    injected = dict(variables)
    for key, metadata in video_info.items():
        injected[f"{key}_source_duration"] = metadata.duration
    return injected


class RenderService:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.output_dir = self.root_dir / "output"

    def _finalize_success(self, task: RenderTask, output_path: str, start_time: float) -> RenderResult:
        elapsed = time.time() - start_time
        complete_task(task, output_path)
        return RenderResult(task_id=task.id, status="completed", output_path=output_path, duration=elapsed)

    def _finalize_transition_success(
        self,
        request: RenderRequest,
        task: RenderTask,
        out_dir: Path,
        res_preset: ResolutionPreset,
        start_time: float,
        cleanup_fns: list[Any],
        handler_result,
    ) -> RenderResult:
        cleanup_fns.append(handler_result.cleanup)
        completed = self._finalize_success(task, handler_result.output_path, start_time)
        print("[3/3] Writing metadata...")
        self.write_meta(out_dir, task, request, res_preset, completed.duration or 0.0)
        return completed

    def render(self, request: RenderRequest) -> RenderResult:
        ffprobe_path = resolve_ffprobe_path(self.root_dir)
        ffmpeg_path = resolve_ffmpeg_path(self.root_dir)
        request_variables = dict(request.variables)
        video_info, failures = collect_video_info(ffprobe_path, request_variables, request.template_info)
        render_variables = inject_video_metadata_variables(request_variables, video_info)

        if request.template_id in TRANSITION_TEMPLATE_IDS:
            if not ffmpeg_path or not ffprobe_path:
                raise DependencyError(
                    "FFmpeg/ffprobe",
                    "Install FFmpeg or configure FFMPEG_PATH and FFPROBE_PATH.",
                )
            if failures:
                detail = "\n".join(
                    f"  - {item['key']}: {item['src']} ({item['reason']})" for item in failures
                )
                raise RenderError(f"{request.template_id} requires readable source durations.\n{detail}")

        if request.preset == AUTO_PRESET:
            first_video_key = find_first_video_key(request_variables, request.template_info)
            video_path = request_variables.get(first_video_key) if first_video_key else None
            probed = video_info.get(first_video_key) if first_video_key else None
            if probed:
                print(
                    f"  Auto-detected resolution {probed.width}x{probed.height} {probed.fps}fps "
                    f"(source: {Path(str(video_path)).name})"
                )
                res_preset: ResolutionPreset = ResolutionPreset(
                    width=probed.width,
                    height=probed.height,
                    fps=probed.fps,
                    label=probed.label,
                )
            else:
                print("  Warning: unable to auto-detect video metadata, fallback to douyin_vertical")
                res_preset = get_resolution_preset("douyin_vertical")
        else:
            res_preset = get_resolution_preset(request.preset)

        qual_preset = get_quality_preset(request.quality)
        task = create_task(request.template_id, request_variables)
        if request.task_id:
            task.id = request.task_id
        start_task(task)

        project_name = Path(request.project_dir).name
        out_dir = self.output_dir / project_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = request.output_filename or "final.mp4"
        start_time = time.time()
        cleanup_fns: list[Any] = []

        try:
            transition_args = TransitionHandlerArgs(
                root_dir=self.root_dir,
                ffmpeg_path=ffmpeg_path or "",
                ffprobe_path=ffprobe_path or "",
                request=RenderRequest(
                    template_id=request.template_id,
                    template_info=request.template_info,
                    variables=render_variables,
                    preset=request.preset,
                    quality=request.quality,
                    output_filename=request.output_filename,
                    project_dir=request.project_dir,
                    task_id=request.task_id,
                ),
                video_info=video_info,
                qual_preset=qual_preset,
                res_preset=res_preset,
                task=task,
                out_dir=out_dir,
                out_file=out_file,
            )

            if request.template_id == "trim-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_trim_concat(transition_args)
                )
            if request.template_id == "xfade-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_xfade_concat(transition_args)
                )
            if request.template_id == "trim-xfade-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_trim_xfade_concat(transition_args)
                )
            if request.template_id == "zoom-dissolve-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_zoom_dissolve_concat(transition_args)
                )
            if request.template_id == "flash-black-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_flash_black_concat(transition_args)
                )
            if request.template_id == "trim-mixed-concat":
                return self._finalize_transition_success(
                    request, task, out_dir, res_preset, start_time, cleanup_fns, handle_trim_mixed_concat(transition_args)
                )

            raise RenderError(f'Template "{request.template_id}" is not implemented in the Python runtime.')
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - start_time
            fail_task(task, str(exc))
            return RenderResult(task_id=task.id, status="failed", duration=elapsed, error=str(exc))
        finally:
            for cleanup in cleanup_fns:
                try:
                    cleanup()
                except Exception:  # noqa: BLE001
                    pass

    def write_meta(
        self,
        out_dir: Path,
        task: RenderTask,
        request: RenderRequest,
        res_preset: ResolutionPreset,
        duration: float,
    ) -> None:
        meta = {
            "taskId": task.id,
            "templateId": request.template_id,
            "templateVersion": request.template_info.manifest.version,
            "preset": request.preset,
            "quality": request.quality,
            "variables": request.variables,
            "renderedAt": datetime.utcnow().isoformat(),
            "duration": round(duration, 1),
            "resolution": f"{res_preset.width}x{res_preset.height}",
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

