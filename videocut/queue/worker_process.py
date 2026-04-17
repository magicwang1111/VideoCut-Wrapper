from __future__ import annotations

import multiprocessing as mp
import shutil
from pathlib import Path

from videocut.oss import OssClient
from videocut.pipeline import PipelineRunner, build_pipeline_context, parse_pipeline_config
from videocut.render import resolve_ffmpeg_path, resolve_ffprobe_path


def _download_pipeline_clips(oss: OssClient, clip_keys: list[str], task_temp_dir: Path) -> list[str]:
    local_paths: list[str] = []
    for index, oss_key in enumerate(clip_keys):
        ext = Path(oss_key).suffix or ".mp4"
        local_path = task_temp_dir / f"clip_{index + 1}{ext}"
        oss.download(oss_key, local_path)
        local_paths.append(str(local_path))
    return local_paths


def worker_main(
    worker_id: int,
    input_queue: mp.Queue,
    event_queue: mp.Queue,
    root_dir: str,
    temp_dir: str,
) -> None:
    oss = OssClient()
    pipeline_runner = PipelineRunner(root_dir)

    event_queue.put({"type": "worker_ready", "worker_id": worker_id})

    while True:
        message = input_queue.get()
        if message is None:
            return

        task_id = message["task_id"]
        payload = dict(message["payload"])
        task_temp_dir = Path(temp_dir) / task_id
        task_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            event_queue.put({"type": "lease_start", "worker_id": worker_id, "task_id": task_id})
            clip_keys = payload.get("clips")
            if not isinstance(clip_keys, list) or not all(isinstance(item, str) for item in clip_keys):
                raise ValueError("pipeline payload requires a clips array")
            raw_config = payload.get("pipeline_config")
            if not isinstance(raw_config, dict):
                raise ValueError("pipeline payload requires pipeline_config")
            local_clips = _download_pipeline_clips(oss, clip_keys, task_temp_dir)
            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 25})
            config = parse_pipeline_config(raw_config, payload.get("pipeline_source_path") or message["source_name"], require_name=True)
            ctx = build_pipeline_context(
                config,
                local_clips,
                payload.get("pipeline_source_path") or message["source_name"],
                payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None,
            )
            ffmpeg_path = resolve_ffmpeg_path(root_dir)
            ffprobe_path = resolve_ffprobe_path(root_dir)
            if not ffmpeg_path or not ffprobe_path:
                raise ValueError("Pipeline mode requires FFmpeg and ffprobe.")
            result = pipeline_runner.run(ctx, ffmpeg_path, ffprobe_path, {}, task_id=task_id)

            if result.status == "failed" or not result.output_path:
                event_queue.put(
                    {
                        "type": "task_failed",
                        "worker_id": worker_id,
                        "task_id": task_id,
                        "error": result.error or "unknown error",
                    }
                )
                continue

            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 90})
            oss_key = oss.output_key(task_id)
            oss.upload(result.output_path, oss_key)
            event_queue.put(
                {"type": "task_done", "worker_id": worker_id, "task_id": task_id, "oss_key": oss_key}
            )
        except Exception as exc:
            event_queue.put(
                {"type": "task_failed", "worker_id": worker_id, "task_id": task_id, "error": str(exc)}
            )
        finally:
            shutil.rmtree(task_temp_dir, ignore_errors=True)
