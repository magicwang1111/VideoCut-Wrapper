from __future__ import annotations

import multiprocessing as mp
import os
import shutil
from pathlib import Path

from videocut.oss import OssClient
from videocut.pipeline import PipelineRunner, build_pipeline_context, parse_pipeline_config
from videocut.render import resolve_ffmpeg_path, resolve_ffprobe_path


def _task_temp_dir(temp_dir: str | Path, task_id: str, attempt: int, worker_id: int) -> Path:
    return Path(temp_dir) / f"{task_id}_attempt{attempt}_worker{worker_id}"


def _download_pipeline_clips(oss: OssClient, clip_keys: list[str], task_temp_dir: Path) -> list[str]:
    local_paths: list[str] = []
    for index, oss_key in enumerate(clip_keys):
        ext = Path(oss_key).suffix or ".mp4"
        local_path = task_temp_dir / f"clip_{index + 1}{ext}"
        oss.download(oss_key, local_path)
        local_paths.append(str(local_path))
    return local_paths


def _download_user_bgm(oss: OssClient, payload: dict, task_temp_dir: Path) -> str | None:
    user_bgm = payload.get("user_bgm")
    if user_bgm is None:
        return None
    if not isinstance(user_bgm, dict) or not isinstance(user_bgm.get("ossKey"), str):
        raise ValueError("pipeline payload user_bgm requires an ossKey")
    oss_key = user_bgm["ossKey"]
    ext = Path(oss_key).suffix or ".mp3"
    local_path = task_temp_dir / f"user_audio{ext}"
    oss.download(oss_key, local_path)
    return str(local_path)


def worker_main(
    worker_id: int,
    input_queue: mp.Queue,
    event_queue: mp.Queue,
    root_dir: str,
    temp_dir: str,
) -> None:
    oss = OssClient()
    pipeline_runner = PipelineRunner(root_dir)
    keep_failed_task_temp = os.getenv("KEEP_FAILED_TASK_TEMP", "0") == "1"

    event_queue.put({"type": "worker_ready", "worker_id": worker_id})

    while True:
        message = input_queue.get()
        if message is None:
            return

        task_id = message["task_id"]
        attempt = int(message.get("attempt") or 0)
        payload = dict(message["payload"])
        task_temp_dir = _task_temp_dir(temp_dir, task_id, attempt, worker_id)
        failed = False

        def failure_message(error: str) -> str:
            if keep_failed_task_temp:
                return f"{error}\n  Task temp dir retained: {task_temp_dir}"
            return error

        try:
            try:
                shutil.rmtree(task_temp_dir, ignore_errors=True)
            except Exception:
                pass
            task_temp_dir.mkdir(parents=True, exist_ok=True)
            event_queue.put({"type": "lease_start", "worker_id": worker_id, "task_id": task_id})
            clip_keys = payload.get("clips")
            if not isinstance(clip_keys, list) or not all(isinstance(item, str) for item in clip_keys):
                raise ValueError("pipeline payload requires a clips array")
            raw_config = payload.get("pipeline_config")
            if not isinstance(raw_config, dict):
                raise ValueError("pipeline payload requires pipeline_config")
            local_clips = _download_pipeline_clips(oss, clip_keys, task_temp_dir)
            user_bgm_path = _download_user_bgm(oss, payload, task_temp_dir)
            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 25})
            config = parse_pipeline_config(raw_config, payload.get("pipeline_source_path") or message["source_name"], require_name=True)
            ctx = build_pipeline_context(
                config,
                local_clips,
                payload.get("pipeline_source_path") or message["source_name"],
                payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None,
                user_bgm_path=user_bgm_path,
            )
            ffmpeg_path = resolve_ffmpeg_path(root_dir)
            ffprobe_path = resolve_ffprobe_path(root_dir)
            if not ffmpeg_path or not ffprobe_path:
                raise ValueError("Pipeline mode requires FFmpeg and ffprobe.")
            result = pipeline_runner.run(ctx, ffmpeg_path, ffprobe_path, {}, task_id=task_id)

            if result.status == "failed" or not result.output_path:
                failed = True
                event_queue.put(
                    {
                        "type": "task_failed",
                        "worker_id": worker_id,
                        "task_id": task_id,
                        "error": failure_message(result.error or "unknown error"),
                    }
                )
                continue

            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 90})
            event_queue.put(
                {
                    "type": "task_rendered",
                    "worker_id": worker_id,
                    "task_id": task_id,
                    "output_path": result.output_path,
                }
            )
        except Exception as exc:
            failed = True
            event_queue.put(
                {"type": "task_failed", "worker_id": worker_id, "task_id": task_id, "error": failure_message(str(exc))}
            )
        finally:
            if not (failed and keep_failed_task_temp):
                shutil.rmtree(task_temp_dir, ignore_errors=True)
