from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from videocut.errors import PipelineNotFoundError, VideoCutError
from videocut.log import get_logger, setup_logging
from videocut.oss import OssClient
from videocut.pipeline import PipelineRegistry
from videocut.queue import TaskQueue, WorkerTask
from videocut.runtime_paths import resolve_runtime_path
from videocut.store import PipelineRecord, TaskRecord, TaskStore

logger = get_logger(__name__)

ALLOWED_UPLOAD_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".aac", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


class RenderBody(BaseModel):
    pipeline: str
    clips: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)


def parse_media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def auth_guard(request: Request) -> None:
    api_keys = {key.strip() for key in os.getenv("API_KEYS", "").split(",") if key.strip()}
    key = request.headers.get("x-api-key")
    if not key or key not in api_keys:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def require_content_type(request: Request, expected: str) -> None:
    raw_content_type = request.headers.get("content-type")
    if not raw_content_type:
        raise HTTPException(
            status_code=415,
            detail={"error": "unsupported_content_type", "expected": expected, "received": None},
        )
    if raw_content_type != raw_content_type.strip() or parse_media_type(raw_content_type) != expected:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "unsupported_content_type",
                "expected": expected,
                "received": raw_content_type,
            },
        )


def _create_store_record(task_id: str, task_kind: str, source_name: str, payload: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        task_kind=task_kind,  # type: ignore[arg-type]
        source_name=source_name,
        status="pending",
        progress=0,
        attempt=0,
        payload=payload,
        oss_key=None,
        error=None,
        last_error=None,
        last_error_at=None,
        created_at=datetime.now(UTC).isoformat(),
        started_at=None,
        completed_at=None,
    )


def _looks_like_external_path(value: str) -> bool:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", value):
        return True
    return value.startswith(("/", "\\", "./", "../", ".\\", "..\\"))


def _resolve_clip_refs(store: TaskStore, oss: OssClient, clips: list[str], *, strict_pipeline: bool) -> list[str]:
    resolved_keys: list[str] = []
    prefix = f"{oss.prefix}/"
    for item in clips:
        if "/" in item or "\\" in item:
            if strict_pipeline:
                if _looks_like_external_path(item) or not item.startswith(prefix):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "invalid_clip_reference", "value": item},
                    )
            resolved_keys.append(item)
            continue
        oss_key = store.get_oss_key(item)
        if not oss_key:
            raise HTTPException(status_code=400, detail={"error": "file_not_found", "fileId": item})
        resolved_keys.append(oss_key)
    return resolved_keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    root_dir = Path(__file__).resolve().parents[2]
    db_path = resolve_runtime_path(os.getenv("DB_PATH"), root_dir / "data" / "tasks.db", root_dir=root_dir)
    pipelines_dir = resolve_runtime_path(os.getenv("PIPELINES_DIR"), root_dir / "pipelines", root_dir=root_dir)
    worker_count = int(os.getenv("WORKER_COUNT", "0")) or max(1, (os.cpu_count() or 2) // 2)

    store = TaskStore(db_path)
    registry = PipelineRegistry(pipelines_dir)
    registry.scan()
    now = datetime.now(UTC).isoformat()
    store.sync_pipelines(
        [
            PipelineRecord(
                name=item.name,
                source_path=str(item.source_path),
                config=asdict(item.config),
                updated_at=now,
            )
            for item in registry.list_all()
        ]
    )

    oss = OssClient()
    task_queue = TaskQueue(
        store,
        oss,
        worker_count,
        lambda event: logger.debug("task event: %s", event),
        root_dir=root_dir,
    )
    task_queue.start()
    cleaned = store.cleanup_old_tasks(int(os.getenv("TASK_TTL_DAYS", "7")))
    if cleaned:
        logger.info("cleaned %d expired task(s)", cleaned)

    app.state.root_dir = root_dir
    app.state.store = store
    app.state.oss = oss
    app.state.task_queue = task_queue
    app.state.worker_count = worker_count
    app.state.pipelines_dir = pipelines_dir
    app.state.pipeline_count = registry.size
    try:
        yield
    finally:
        task_queue.stop()
        store.close()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.exception_handler(VideoCutError)
    async def videocut_error_handler(_: Request, exc: VideoCutError):
        return JSONResponse(status_code=400, content={"code": exc.code, "error": str(exc)})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        queue_obj: TaskQueue = app.state.task_queue
        return {
            "ok": True,
            "workers": app.state.worker_count,
            "queueSize": queue_obj.queue_size,
            "pipelines": app.state.pipeline_count,
        }

    @app.post("/upload", dependencies=[Depends(auth_guard)])
    async def upload(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
        require_content_type(request, "multipart/form-data")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            raise HTTPException(status_code=400, detail={"error": "unsupported_format", "ext": ext})

        file_id = uuid4().hex[:12]
        oss: OssClient = app.state.oss
        oss_key = oss.input_key(file_id, ext)
        temp_root = resolve_runtime_path(os.getenv("TEMP_DIR"), app.state.root_dir / "temp", root_dir=app.state.root_dir)
        temp_path = temp_root / f"upload_{file_id}{ext}"
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        size = 0
        with temp_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    temp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail={"error": "file_too_large"})
                handle.write(chunk)

        try:
            oss.upload(temp_path, oss_key)
            store: TaskStore = app.state.store
            store.save_file(file_id, oss_key)
        finally:
            temp_path.unlink(missing_ok=True)

        return {"fileId": file_id, "ossKey": oss_key}

    @app.post("/render", dependencies=[Depends(auth_guard)])
    async def render(request: Request, body: RenderBody) -> dict[str, str]:
        require_content_type(request, "application/json")
        if not body.pipeline or not body.clips:
            raise HTTPException(status_code=400, detail={"error": "invalid_body"})

        store: TaskStore = app.state.store
        oss: OssClient = app.state.oss

        resolved_keys = _resolve_clip_refs(store, oss, body.clips, strict_pipeline=True)
        pipeline_name = body.pipeline
        pipeline_record = store.get_pipeline(pipeline_name)
        if pipeline_record is None:
            raise PipelineNotFoundError(pipeline_name, [item.name for item in store.list_pipelines()])
        payload = {
            "clips": resolved_keys,
            "pipeline_config": pipeline_record.config,
            "pipeline_source_path": pipeline_record.source_path,
            "overrides": body.overrides,
        }
        task_id = f"t_{uuid4().hex[:8]}"
        store.create(_create_store_record(task_id, "pipeline", pipeline_name, payload))
        queue_obj: TaskQueue = app.state.task_queue
        enqueued = queue_obj.enqueue(
            WorkerTask(task_id=task_id, task_kind="pipeline", source_name=pipeline_name, payload=payload)
        )
        if not enqueued:
            store.mark_failed(task_id, "Queue is full.")
            raise HTTPException(status_code=503, detail={"error": "queue_full", "queueSize": queue_obj.queue_size})
        return {"taskId": task_id}

    @app.get("/tasks/{task_id}", dependencies=[Depends(auth_guard)])
    async def get_task(task_id: str) -> dict[str, Any]:
        store: TaskStore = app.state.store
        task = store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        failures = store.list_failures(task_id)
        oss: OssClient = app.state.oss
        output_url = oss.presign_url(task.oss_key, 3600) if task.status == "completed" and task.oss_key else None
        return {
            "taskId": task.id,
            "status": task.status,
            "progress": task.progress,
            "attempt": task.attempt,
            "createdAt": task.created_at,
            "startedAt": task.started_at,
            "completedAt": task.completed_at,
            "outputUrl": output_url,
            "error": task.error,
            "lastError": task.last_error,
            "lastErrorAt": task.last_error_at,
            "failureHistory": [
                {
                    "attempt": item.attempt,
                    "error": item.error,
                    "createdAt": item.created_at,
                }
                for item in failures
            ],
            "taskKind": task.task_kind,
            "sourceName": task.source_name,
        }

    @app.get("/tasks/{task_id}/download", dependencies=[Depends(auth_guard)])
    async def download_task(task_id: str):
        store: TaskStore = app.state.store
        task = store.get(task_id)
        if not task or task.status != "completed" or not task.oss_key:
            raise HTTPException(status_code=404, detail={"error": "not_found_or_not_ready"})
        oss: OssClient = app.state.oss
        if oss.local_root:
            return FileResponse(Path(oss.local_root) / task.oss_key, filename="final.mp4")
        return RedirectResponse(oss.presign_url(task.oss_key, 3600), status_code=302)

    return app


app = create_app()
