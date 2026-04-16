from __future__ import annotations

import multiprocessing as mp
import shutil
from pathlib import Path
from typing import Any

from videocut.oss import OssClient
from videocut.registry import TemplateRegistry
from videocut.render import RenderService
from videocut.render.types import RenderRequest


def _map_clip_variables(template_info, variables: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(variables)
    clips = mapped.pop("clips", None)
    if not isinstance(clips, list):
        return mapped

    video_list_key = next(
        (key for key, definition in template_info.manifest.variables.items() if definition.type == "video_list"),
        None,
    )
    if video_list_key:
        if video_list_key not in mapped:
            mapped[video_list_key] = clips
        return mapped

    video_keys = [key for key, definition in template_info.manifest.variables.items() if definition.type == "video"]
    for index, clip in enumerate(clips):
        if index >= len(video_keys):
            break
        mapped.setdefault(video_keys[index], clip)
    return mapped


def _download_materials(template_info, oss: OssClient, variables: dict[str, Any], task_temp_dir: Path) -> dict[str, Any]:
    resolved = dict(variables)
    for key, definition in template_info.manifest.variables.items():
        if definition.type == "video_list":
            value = resolved.get(key)
            if not isinstance(value, list):
                continue
            local_paths: list[str] = []
            for index, oss_key in enumerate(value):
                if not isinstance(oss_key, str):
                    continue
                ext = Path(oss_key).suffix or ".mp4"
                local_path = task_temp_dir / f"{key}_{index}{ext}"
                oss.download(oss_key, local_path)
                local_paths.append(str(local_path))
            resolved[key] = local_paths
            continue

        if definition.type not in {"video", "image", "audio"}:
            continue
        value = resolved.get(key)
        if not isinstance(value, str):
            continue
        ext = Path(value).suffix or ".bin"
        local_path = task_temp_dir / f"{key}{ext}"
        oss.download(value, local_path)
        resolved[key] = str(local_path)
    return resolved


def worker_main(
    worker_id: int,
    input_queue: mp.Queue,
    event_queue: mp.Queue,
    root_dir: str,
    temp_dir: str,
    templates_dir: str,
) -> None:
    oss = OssClient()
    render_service = RenderService(root_dir)
    registry = TemplateRegistry(templates_dir)
    registry.scan()

    event_queue.put({"type": "worker_ready", "worker_id": worker_id})

    while True:
        message = input_queue.get()
        if message is None:
            return

        task_id = message["task_id"]
        template_id = message["template_id"]
        variables = dict(message["variables"])
        preset = message["preset"]
        quality = message["quality"]
        task_temp_dir = Path(temp_dir) / task_id
        task_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            event_queue.put({"type": "lease_start", "worker_id": worker_id, "task_id": task_id})
            template_info = registry.get(template_id)
            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 10})
            mapped_variables = _map_clip_variables(template_info, variables)
            downloaded_variables = _download_materials(template_info, oss, mapped_variables, task_temp_dir)
            event_queue.put({"type": "progress", "worker_id": worker_id, "task_id": task_id, "progress": 25})

            result = render_service.render(
                RenderRequest(
                    task_id=task_id,
                    template_id=template_id,
                    template_info=template_info,
                    variables=downloaded_variables,
                    preset=preset,
                    quality=quality,
                    output_filename="final.mp4",
                    project_dir=str(task_temp_dir),
                )
            )
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
        except Exception as exc:  # noqa: BLE001
            event_queue.put(
                {"type": "task_failed", "worker_id": worker_id, "task_id": task_id, "error": str(exc)}
            )
        finally:
            shutil.rmtree(task_temp_dir, ignore_errors=True)

