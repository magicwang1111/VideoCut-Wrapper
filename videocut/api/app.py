from __future__ import annotations

import os
import re
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from videocut.bgm import list_bgm_catalog, resolve_bgm_dir
from videocut.env import load_project_env
from videocut.errors import PipelineNotFoundError, VideoCutError
from videocut.log import get_logger, setup_logging
from videocut.oss import OssClient
from videocut.pipeline import PipelineRegistry
from videocut.queue import TaskQueue, WorkerTask
from videocut.render.task import generate_task_id
from videocut.runtime_paths import resolve_runtime_path
from videocut.store import PipelineRecord, TaskRecord, TaskStore
from videocut.time_utils import BEIJING_TZ, now_beijing, now_beijing_iso

load_project_env()

logger = get_logger(__name__)

AUDIO_UPLOAD_EXTS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"}
ASSET_UPLOAD_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_UPLOAD_EXTS = ASSET_UPLOAD_EXTS | AUDIO_UPLOAD_EXTS
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
BGM_OVERRIDE_FIELDS = {"fileId", "enabled", "dir", "category", "filename", "volume", "fade_out"}


class ApiErrorCode(IntEnum):
    UNAUTHORIZED = 1001
    UNSUPPORTED_CONTENT_TYPE = 1002
    VALIDATION_ERROR = 1003
    NOT_FOUND = 1004
    INVALID_BODY = 2001
    INVALID_CLIP_REFERENCE = 2002
    PIPELINE_NOT_FOUND = 2003
    TASK_NOT_FOUND = 2004
    UNSUPPORTED_FORMAT = 2005
    FILE_NOT_FOUND = 2006
    INVALID_BGM_OVERRIDE = 2007
    INVALID_BGM_FILE_REFERENCE = 2008
    TASK_OUTPUT_NOT_READY = 3001
    QUEUE_FULL = 3002
    FILE_TOO_LARGE = 3003
    INTERNAL_ERROR = 9001


class RenderBody(BaseModel):
    pipeline: str
    clips: list[str] = Field(default_factory=list)
    overrides: dict[str, Any] = Field(default_factory=dict)


def api_error_payload(error_code: ApiErrorCode | int, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error_code": int(error_code),
        "message": message,
        "details": details or {},
    }


def api_http_exception(
    status_code: int,
    error_code: ApiErrorCode | int,
    message: str,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    return HTTPException(status_code=status_code, detail=api_error_payload(error_code, message, details))


def parse_media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def format_api_time(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(BEIJING_TZ).replace(tzinfo=None).isoformat(timespec="microseconds")


def now_api_time() -> str:
    return format_api_time(now_beijing()) or ""


def active_task_payload(task: TaskRecord) -> dict[str, Any]:
    return {
        "taskId": task.id,
        "status": task.status,
        "progress": task.progress,
        "attempt": task.attempt,
        "createdAt": format_api_time(task.created_at),
        "startedAt": format_api_time(task.started_at),
        "lastError": task.last_error,
        "lastErrorAt": format_api_time(task.last_error_at),
        "taskKind": task.task_kind,
        "sourceName": task.source_name,
    }


def auth_guard(request: Request) -> None:
    api_keys = {key.strip() for key in os.getenv("API_KEYS", "").split(",") if key.strip()}
    key = request.headers.get("x-api-key")
    if not key or key not in api_keys:
        raise api_http_exception(401, ApiErrorCode.UNAUTHORIZED, "Unauthorized.")


def require_content_type(request: Request, expected: str) -> None:
    raw_content_type = request.headers.get("content-type")
    if not raw_content_type:
        raise api_http_exception(
            415,
            ApiErrorCode.UNSUPPORTED_CONTENT_TYPE,
            "Unsupported content type.",
            {"expected": expected, "received": None},
        )
    if raw_content_type != raw_content_type.strip() or parse_media_type(raw_content_type) != expected:
        raise api_http_exception(
            415,
            ApiErrorCode.UNSUPPORTED_CONTENT_TYPE,
            "Unsupported content type.",
            {"expected": expected, "received": raw_content_type},
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
        created_at=now_beijing_iso(),
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
    user_audio_prefix = f"{oss.prefix}/user-audio/"
    for item in clips:
        if "/" in item or "\\" in item:
            if strict_pipeline:
                if _looks_like_external_path(item) or not item.startswith(prefix) or item.startswith(user_audio_prefix):
                    raise api_http_exception(
                        400,
                        ApiErrorCode.INVALID_CLIP_REFERENCE,
                        "Invalid clip reference.",
                        {"value": item},
                    )
            resolved_keys.append(item)
            continue
        file_record = store.get_file(item)
        if not file_record:
            raise api_http_exception(
                400,
                ApiErrorCode.FILE_NOT_FOUND,
                "File reference not found.",
                {"fileId": item},
            )
        if file_record.kind != "asset":
            raise api_http_exception(
                400,
                ApiErrorCode.INVALID_CLIP_REFERENCE,
                "Invalid clip reference.",
                {"fileId": item, "kind": file_record.kind},
            )
        resolved_keys.append(file_record.oss_key)
    return resolved_keys


def _upload_ttl_days() -> int:
    raw = os.getenv("UPLOAD_TTL_DAYS") or os.getenv("TASK_TTL_DAYS") or "7"
    return max(1, int(raw))


def _user_audio_expires_at() -> str:
    return (now_beijing() + timedelta(days=_upload_ttl_days())).isoformat()


def _cleanup_expired_uploads(store: TaskStore, oss: OssClient) -> int:
    records = store.cleanup_expired_files()
    if oss.local_root:
        for record in records:
            if record.kind == "user_audio":
                try:
                    oss.delete(record.oss_key)
                except Exception:  # intentional: cleanup should not prevent API startup
                    logger.warning("failed to cleanup expired upload: %s", record.oss_key, exc_info=True)
    return len(records)


def _invalid_bgm_override(details: dict[str, Any]) -> HTTPException:
    return api_http_exception(400, ApiErrorCode.INVALID_BGM_OVERRIDE, "Invalid BGM override.", details)


def _validate_optional_bgm_field_types(bgm: dict[str, Any]) -> None:
    for field in ("dir", "category", "filename"):
        value = bgm.get(field)
        if field in bgm and not isinstance(value, str):
            raise _invalid_bgm_override({"field": f"overrides.bgm.{field}", "expected": "string"})

    for field in ("volume", "fade_out"):
        value = bgm.get(field)
        if field in bgm and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise _invalid_bgm_override({"field": f"overrides.bgm.{field}", "expected": "number"})

    enabled = bgm.get("enabled")
    if "enabled" in bgm and not isinstance(enabled, bool):
        raise _invalid_bgm_override({"field": "overrides.bgm.enabled", "expected": "boolean"})


def _resolve_user_bgm(store: TaskStore, overrides: dict[str, Any]) -> dict[str, str] | None:
    if "bgm" not in overrides:
        return None
    bgm = overrides.get("bgm")
    if not isinstance(bgm, dict):
        raise _invalid_bgm_override({"field": "overrides.bgm", "expected": "object"})

    unknown_fields = sorted(field for field in bgm if field not in BGM_OVERRIDE_FIELDS)
    if unknown_fields:
        details: dict[str, Any] = {"field": "overrides.bgm", "unknown": unknown_fields}
        if "file_id" in unknown_fields:
            details = {
                "field": "overrides.bgm.file_id",
                "expected": "overrides.bgm.fileId",
                "unknown": unknown_fields,
            }
        raise _invalid_bgm_override(details)

    _validate_optional_bgm_field_types(bgm)

    if "fileId" not in bgm:
        return None
    file_id = bgm.get("fileId")
    if not isinstance(file_id, str) or not file_id.strip():
        raise _invalid_bgm_override({"field": "overrides.bgm.fileId", "expected": "non-empty string"})
    conflicts = sorted(field for field in bgm if field != "fileId")
    if conflicts:
        raise _invalid_bgm_override({"field": "overrides.bgm.fileId", "conflicts": conflicts})
    if "enabled" not in bgm:
        bgm["enabled"] = True
    file_record = store.get_file(file_id.strip())
    if not file_record:
        raise api_http_exception(
            400,
            ApiErrorCode.INVALID_BGM_FILE_REFERENCE,
            "BGM file reference not found.",
            {"fileId": file_id.strip()},
        )
    if file_record.kind != "user_audio":
        raise api_http_exception(
            400,
            ApiErrorCode.INVALID_BGM_FILE_REFERENCE,
            "Invalid BGM file reference.",
            {"fileId": file_id.strip(), "kind": file_record.kind, "expected": "user_audio"},
        )
    return {"fileId": file_record.file_id, "ossKey": file_record.oss_key}


def _validate_pipeline_clip_count(pipeline_config: dict[str, Any], clip_count: int) -> None:
    required_clip_count = pipeline_config.get("required_clip_count")
    if required_clip_count is None:
        return
    if not isinstance(required_clip_count, int) or required_clip_count <= 0:
        raise api_http_exception(
            400,
            ApiErrorCode.INVALID_BODY,
            "Invalid pipeline required_clip_count.",
            {"requiredClipCount": required_clip_count},
        )
    if clip_count != required_clip_count:
        raise api_http_exception(
            400,
            ApiErrorCode.INVALID_BODY,
            f"Pipeline requires exactly {required_clip_count} input clips, got {clip_count}.",
            {"requiredClipCount": required_clip_count, "clipCount": clip_count},
        )


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
    now = now_beijing_iso()
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
    cleaned_uploads = _cleanup_expired_uploads(store, oss)
    if cleaned_uploads:
        logger.info("cleaned %d expired upload file record(s)", cleaned_uploads)

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

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error_code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        if exc.status_code == 404:
            return JSONResponse(
                status_code=404,
                content=api_error_payload(ApiErrorCode.NOT_FOUND, "Not Found.", {"path": request.url.path}),
            )
        logger.warning("Unhandled HTTPException detail shape: %s", exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=api_error_payload(ApiErrorCode.VALIDATION_ERROR, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=api_error_payload(
                ApiErrorCode.VALIDATION_ERROR,
                "Request validation failed.",
                {"validation": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(VideoCutError)
    async def videocut_error_handler(_: Request, exc: VideoCutError):
        if isinstance(exc, PipelineNotFoundError):
            return JSONResponse(
                status_code=400,
                content=api_error_payload(
                    ApiErrorCode.PIPELINE_NOT_FOUND,
                    f'Pipeline "{exc.pipeline_name}" is not registered.',
                    {"available": exc.available},
                ),
            )
        logger.warning("Unhandled VideoCutError: %s", exc)
        return JSONResponse(
            status_code=500,
            content=api_error_payload(ApiErrorCode.INTERNAL_ERROR, "Internal server error."),
        )

    @app.exception_handler(Exception)
    async def internal_exception_handler(_: Request, exc: Exception):
        logger.exception("Unhandled API exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=api_error_payload(ApiErrorCode.INTERNAL_ERROR, "Internal server error."),
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        queue_obj: TaskQueue = app.state.task_queue
        return {
            "ok": True,
            "workers": app.state.worker_count,
            "queueSize": queue_obj.queue_size,
            "pipelines": app.state.pipeline_count,
        }

    @app.get("/bgm", dependencies=[Depends(auth_guard)])
    async def list_bgm() -> dict[str, object]:
        bgm_dir = resolve_bgm_dir(app.state.root_dir)
        if not bgm_dir.is_dir():
            raise api_http_exception(
                404,
                ApiErrorCode.FILE_NOT_FOUND,
                "BGM directory not found.",
                {"bgmRoot": str(bgm_dir)},
            )
        return list_bgm_catalog(bgm_dir)

    @app.post("/upload", dependencies=[Depends(auth_guard)])
    async def upload(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
        require_content_type(request, "multipart/form-data")
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            raise api_http_exception(
                400,
                ApiErrorCode.UNSUPPORTED_FORMAT,
                "Unsupported upload format.",
                {"ext": ext},
            )

        file_id = uuid4().hex[:12]
        oss: OssClient = app.state.oss
        kind = "user_audio" if ext in AUDIO_UPLOAD_EXTS else "asset"
        expires_at = _user_audio_expires_at() if kind == "user_audio" else None
        oss_key = oss.user_audio_key(file_id, ext) if kind == "user_audio" else oss.input_key(file_id, ext)
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
                    raise api_http_exception(413, ApiErrorCode.FILE_TOO_LARGE, "File is too large.")
                handle.write(chunk)

        try:
            oss.upload(temp_path, oss_key)
            store: TaskStore = app.state.store
            store.save_file(file_id, oss_key, kind=kind, size_bytes=size, expires_at=expires_at)
        finally:
            temp_path.unlink(missing_ok=True)

        response = {"fileId": file_id, "ossKey": oss_key, "kind": kind}
        if expires_at:
            response["expiresAt"] = format_api_time(expires_at) or expires_at
        return response

    @app.post("/render", dependencies=[Depends(auth_guard)])
    async def render(request: Request, body: RenderBody) -> dict[str, str]:
        require_content_type(request, "application/json")
        if not body.pipeline or not body.clips:
            raise api_http_exception(400, ApiErrorCode.INVALID_BODY, "Invalid request body.")

        store: TaskStore = app.state.store
        oss: OssClient = app.state.oss

        pipeline_name = body.pipeline
        pipeline_record = store.get_pipeline(pipeline_name)
        if pipeline_record is None:
            raise PipelineNotFoundError(pipeline_name, [item.name for item in store.list_pipelines()])
        _validate_pipeline_clip_count(pipeline_record.config, len(body.clips))

        resolved_keys = _resolve_clip_refs(store, oss, body.clips, strict_pipeline=True)
        overrides = deepcopy(body.overrides)
        user_bgm = _resolve_user_bgm(store, overrides)
        payload = {
            "clips": resolved_keys,
            "pipeline_config": pipeline_record.config,
            "pipeline_source_path": pipeline_record.source_path,
            "overrides": overrides,
        }
        if user_bgm:
            payload["user_bgm"] = user_bgm
        task_id = generate_task_id(prefix="t_")
        store.create(_create_store_record(task_id, "pipeline", pipeline_name, payload))
        queue_obj: TaskQueue = app.state.task_queue
        enqueued = queue_obj.enqueue(
            WorkerTask(task_id=task_id, task_kind="pipeline", source_name=pipeline_name, payload=payload)
        )
        if not enqueued:
            store.mark_failed(task_id, "Queue is full.")
            raise api_http_exception(
                503,
                ApiErrorCode.QUEUE_FULL,
                "Queue is full.",
                {"queueSize": queue_obj.queue_size},
            )
        return {"taskId": task_id}

    @app.get("/tasks/summary", dependencies=[Depends(auth_guard)])
    async def task_summary() -> dict[str, Any]:
        store: TaskStore = app.state.store
        queue_obj: TaskQueue = app.state.task_queue
        return {
            "generatedAt": now_api_time(),
            "workers": app.state.worker_count,
            "uploadWorkers": queue_obj.upload_worker_count,
            "queueSize": queue_obj.queue_size,
            "counts": store.count_tasks_by_status(),
        }

    @app.get("/tasks/active", dependencies=[Depends(auth_guard)])
    async def active_tasks() -> dict[str, Any]:
        store: TaskStore = app.state.store
        queue_obj: TaskQueue = app.state.task_queue
        tasks = []
        for task in store.list_active_tasks():
            payload = active_task_payload(task)
            upload_diagnostics = queue_obj.get_upload_diagnostics(task.id)
            if upload_diagnostics:
                payload["uploadDiagnostics"] = upload_diagnostics
            tasks.append(payload)
        return {
            "generatedAt": now_api_time(),
            "tasks": tasks,
        }

    @app.get("/tasks/{task_id}", dependencies=[Depends(auth_guard)])
    async def get_task(task_id: str) -> dict[str, Any]:
        store: TaskStore = app.state.store
        task = store.get(task_id)
        if not task:
            raise api_http_exception(404, ApiErrorCode.TASK_NOT_FOUND, "Task not found.")
        failures = store.list_failures(task_id)
        oss: OssClient = app.state.oss
        queue_obj: TaskQueue = app.state.task_queue
        output_url = oss.presign_url(task.oss_key, 3600) if task.status == "completed" and task.oss_key else None
        payload = {
            "taskId": task.id,
            "status": task.status,
            "progress": task.progress,
            "attempt": task.attempt,
            "createdAt": format_api_time(task.created_at),
            "startedAt": format_api_time(task.started_at),
            "completedAt": format_api_time(task.completed_at),
            "outputUrl": output_url,
            "error": task.error,
            "lastError": task.last_error,
            "lastErrorAt": format_api_time(task.last_error_at),
            "failureHistory": [
                {
                    "attempt": item.attempt,
                    "error": item.error,
                    "createdAt": format_api_time(item.created_at),
                }
                for item in failures
            ],
            "taskKind": task.task_kind,
            "sourceName": task.source_name,
        }
        upload_diagnostics = queue_obj.get_upload_diagnostics(task.id)
        if upload_diagnostics:
            payload["uploadDiagnostics"] = upload_diagnostics
        return payload

    @app.get("/tasks/{task_id}/download", dependencies=[Depends(auth_guard)])
    async def download_task(task_id: str):
        store: TaskStore = app.state.store
        task = store.get(task_id)
        if not task or task.status != "completed" or not task.oss_key:
            raise api_http_exception(404, ApiErrorCode.TASK_OUTPUT_NOT_READY, "Task output is not ready.")
        oss: OssClient = app.state.oss
        if oss.local_root:
            return FileResponse(Path(oss.local_root) / task.oss_key, filename="final.mp4")
        return RedirectResponse(oss.presign_url(task.oss_key, 3600), status_code=302)

    return app


app = create_app()
