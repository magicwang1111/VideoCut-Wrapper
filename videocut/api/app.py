from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from videocut.oss import OssClient
from videocut.queue import TaskQueue, WorkerTask
from videocut.store import TaskRecord, TaskStore

ALLOWED_UPLOAD_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".aac", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_RENDER_BODY_BYTES = 1024 * 1024


class RenderBody(BaseModel):
    template: str
    clips: list[str]
    params: dict[str, Any] = Field(default_factory=dict)


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


def _create_store_record(task_id: str, template_id: str, variables: dict[str, Any]) -> TaskRecord:
    from datetime import datetime

    return TaskRecord(
        id=task_id,
        template_id=template_id,
        status="pending",
        progress=0,
        attempt=0,
        variables=variables,
        oss_key=None,
        error=None,
        created_at=datetime.utcnow().isoformat(),
        started_at=None,
        completed_at=None,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    root_dir = Path(__file__).resolve().parents[2]
    db_path = Path(os.getenv("DB_PATH", str(root_dir / "data" / "tasks.db"))).resolve()
    worker_count = int(os.getenv("WORKER_COUNT", "0")) or max(1, (os.cpu_count() or 2) // 2)

    store = TaskStore(db_path)
    oss = OssClient()
    task_queue = TaskQueue(
        store,
        oss,
        worker_count,
        lambda event: print(f"[Task] {event}"),
        root_dir=root_dir,
    )
    task_queue.start()
    cleaned = store.cleanup_old_tasks(int(os.getenv("TASK_TTL_DAYS", "7")))
    if cleaned:
        print(f"[Store] cleaned {cleaned} expired task(s)")

    app.state.root_dir = root_dir
    app.state.store = store
    app.state.oss = oss
    app.state.task_queue = task_queue
    app.state.worker_count = worker_count
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

    @app.get("/health")
    async def health() -> dict[str, Any]:
        queue_obj: TaskQueue = app.state.task_queue
        return {"ok": True, "workers": app.state.worker_count, "queueSize": queue_obj.queue_size}

    @app.post("/upload", dependencies=[Depends(auth_guard)])
    async def upload(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
        require_content_type(request, "multipart/form-data")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            raise HTTPException(status_code=400, detail={"error": "unsupported_format", "ext": ext})

        file_id = uuid4().hex[:12]
        oss: OssClient = app.state.oss
        oss_key = oss.input_key(file_id, ext)
        temp_path = Path(os.getenv("TEMP_DIR", str(app.state.root_dir / "temp"))).resolve() / f"upload_{file_id}{ext}"
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
        if not body.template or not body.clips:
            raise HTTPException(status_code=400, detail={"error": "invalid_body"})

        store: TaskStore = app.state.store
        resolved_keys: list[str] = []
        for item in body.clips:
            if "/" in item:
                resolved_keys.append(item)
                continue
            oss_key = store.get_oss_key(item)
            if not oss_key:
                raise HTTPException(status_code=400, detail={"error": "file_not_found", "fileId": item})
            resolved_keys.append(oss_key)

        preset = str(body.params.get("preset", "auto"))
        quality = str(body.params.get("quality", "high"))
        task_id = f"t_{uuid4().hex[:8]}"
        variables = {"clips": resolved_keys, **body.params, "_preset": preset, "_quality": quality}
        store.create(_create_store_record(task_id, body.template, variables))

        queue_obj: TaskQueue = app.state.task_queue
        enqueued = queue_obj.enqueue(
            WorkerTask(
                task_id=task_id,
                template_id=body.template,
                variables=variables,
                preset=preset,
                quality=quality,
            )
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
