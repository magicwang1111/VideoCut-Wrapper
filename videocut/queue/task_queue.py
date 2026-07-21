from __future__ import annotations

import multiprocessing as mp
import os
import queue
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from videocut.env import load_project_env
from videocut.log import get_logger
from videocut.oss import OssClient
from videocut.queue.worker_process import worker_main
from videocut.runtime_paths import resolve_runtime_path
from videocut.store import TaskStore
from videocut.time_utils import now_beijing_iso

load_project_env()

TASK_MAX_ATTEMPT = int(os.getenv("TASK_MAX_ATTEMPT", "3"))
QUEUE_MAX = int(os.getenv("QUEUE_MAX", "200"))
UPLOAD_MAX_ATTEMPT = int(os.getenv("UPLOAD_MAX_ATTEMPT", "3"))
UPLOAD_WORKER_COUNT = int(os.getenv("UPLOAD_WORKER_COUNT", "2"))

TaskEventHandler = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class WorkerTask:
    task_id: str
    task_kind: str
    source_name: str
    payload: dict[str, Any]
    attempt: int = 0


@dataclass(slots=True)
class WorkerState:
    worker_id: int
    process: mp.Process
    input_queue: mp.Queue
    current_task_id: str | None = None
    ready: bool = False


logger = get_logger(__name__)


def _round_elapsed(seconds: float | None) -> float | None:
    return round(seconds, 3) if seconds is not None else None


class WorkerPool:
    def __init__(
        self,
        count: int,
        root_dir: Path,
        on_message: Callable[[dict[str, Any]], None],
        on_worker_dead: Callable[[int, str | None], None],
    ) -> None:
        cpu_default = max(1, (os.cpu_count() or 2) // 2)
        self.count = count or cpu_default
        self.root_dir = root_dir
        self.on_message = on_message
        self.on_worker_dead = on_worker_dead
        self.temp_dir = resolve_runtime_path(os.getenv("TEMP_DIR"), root_dir / "temp", root_dir=root_dir)
        self.event_queue: mp.Queue = mp.Queue()
        self.workers: list[WorkerState] = []
        self._stop_event = threading.Event()
        self._next_id = 0
        self._event_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None

    def start(self) -> None:
        for _ in range(self.count):
            self._spawn_worker()
        self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
        self._event_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def _spawn_worker(self) -> None:
        worker_id = self._next_id
        self._next_id += 1
        input_queue: mp.Queue = mp.Queue()
        process = mp.Process(
            target=worker_main,
            args=(
                worker_id,
                input_queue,
                self.event_queue,
                str(self.root_dir),
                str(self.temp_dir),
            ),
            daemon=True,
        )
        state = WorkerState(worker_id=worker_id, process=process, input_queue=input_queue)
        process.start()
        self.workers.append(state)

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                message = self.event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            worker = self._get_worker(message.get("worker_id"))
            if worker is None:
                continue
            if message["type"] == "worker_ready":
                worker.ready = True
            elif message["type"] == "lease_start":
                worker.current_task_id = message["task_id"]
            elif message["type"] in {"task_done", "task_failed", "task_rendered"}:
                worker.current_task_id = None
            self.on_message(message)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            for worker in list(self.workers):
                if worker.process.is_alive():
                    continue
                task_id = worker.current_task_id
                self.workers.remove(worker)
                self.on_worker_dead(worker.worker_id, task_id)
                if not self._stop_event.is_set():
                    self._spawn_worker()
            time.sleep(0.5)

    def dispatch(self, task: WorkerTask) -> bool:
        idle = next((worker for worker in self.workers if worker.ready and worker.current_task_id is None), None)
        if idle is None:
            return False
        idle.current_task_id = task.task_id
        idle.input_queue.put(
            {
                "task_id": task.task_id,
                "task_kind": task.task_kind,
                "source_name": task.source_name,
                "payload": task.payload,
                "attempt": task.attempt,
            }
        )
        return True

    @property
    def idle_count(self) -> int:
        return len([worker for worker in self.workers if worker.ready and worker.current_task_id is None])

    def stop(self) -> None:
        self._stop_event.set()
        for worker in self.workers:
            try:
                worker.input_queue.put(None)
            except Exception:
                pass
        for worker in self.workers:
            worker.process.join(timeout=5)
            if worker.process.is_alive():
                worker.process.terminate()
        if self._event_thread:
            self._event_thread.join(timeout=1)
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=1)

    def _get_worker(self, worker_id: int | None) -> WorkerState | None:
        return next((worker for worker in self.workers if worker.worker_id == worker_id), None)


class TaskQueue:
    def __init__(self, store: TaskStore, oss: OssClient, worker_count: int, on_event: TaskEventHandler, root_dir: str | Path) -> None:
        self.store = store
        self.oss = oss
        self.on_event = on_event
        self.queue: list[WorkerTask] = []
        self.progress_throttle: dict[str, float] = {}
        self.upload_diagnostics: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.pool = WorkerPool(worker_count, Path(root_dir).resolve(), self._handle_message, self._handle_worker_dead)
        self.upload_worker_count = max(1, UPLOAD_WORKER_COUNT)
        self.upload_executor = ThreadPoolExecutor(max_workers=self.upload_worker_count, thread_name_prefix="videocut-upload")

    def start(self) -> None:
        self.pool.start()
        self._replay_from_store()
        self._drain()

    def stop(self) -> None:
        self.pool.stop()
        self.upload_executor.shutdown(wait=True)

    def _replay_from_store(self) -> None:
        stalled = self.store.get_pending_and_stalled()
        with self._lock:
            for task in stalled:
                if task.status == "rendering":
                    self.store.record_failure(task.id, "Task replayed after service restart or unexpected shutdown.")
                    self.store.reset_to_queue(task.id)
                if task.attempt >= TASK_MAX_ATTEMPT:
                    self.store.mark_failed(task.id, "Exceeded max retry attempts.")
                    continue
                self.queue.append(
                    WorkerTask(
                        task_id=task.id,
                        task_kind=task.task_kind,
                        source_name=task.source_name,
                        payload=task.payload,
                    )
                )
        if stalled:
            logger.info("Replayed %d pending/rendering task(s)", len(stalled))

    def enqueue(self, task: WorkerTask) -> bool:
        with self._lock:
            if len(self.queue) >= QUEUE_MAX:
                return False
            self.queue.append(task)
        self._drain()
        return True

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self.queue)

    def get_upload_diagnostics(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            diagnostics = self.upload_diagnostics.get(task_id)
            if diagnostics is None:
                return None
            return {key: value for key, value in diagnostics.items() if not key.startswith("_")}

    def _get_raw_upload_diagnostics(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self.upload_diagnostics.get(task_id, {}))

    def _update_upload_diagnostics(self, task_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            diagnostics = self.upload_diagnostics.setdefault(task_id, {})
            diagnostics.update(updates)
            return dict(diagnostics)

    def _drain(self) -> None:
        with self._lock:
            while self.queue and self.pool.idle_count > 0:
                task = self.queue.pop(0)
                attempt = self.store.mark_rendering(task.task_id)
                dispatch_task = WorkerTask(
                    task_id=task.task_id,
                    task_kind=task.task_kind,
                    source_name=task.source_name,
                    payload=task.payload,
                    attempt=attempt,
                )
                if not self.pool.dispatch(dispatch_task):
                    self.queue.insert(0, task)
                    break

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message["type"]
        task_id = message.get("task_id")
        if message_type == "worker_ready":
            self._drain()
            return

        if message_type == "task_done":
            self.store.mark_completed(task_id, message["oss_key"])
            self.on_event({"type": "completed", "taskId": task_id, "ossKey": message["oss_key"]})
            self._drain()
            return

        if message_type == "task_rendered":
            output_path = message.get("output_path")
            if not isinstance(output_path, str) or not output_path:
                self.store.mark_failed(task_id, "Worker rendered task without output_path.")
                self.on_event({"type": "failed", "taskId": task_id, "error": "Worker rendered task without output_path."})
                self._drain()
                return
            progress95_at = now_beijing_iso()
            queued_monotonic = time.monotonic()
            self.store.update_progress(task_id, 95)
            self.on_event({"type": "progress", "taskId": task_id, "progress": 95})
            self._update_upload_diagnostics(
                task_id,
                progress95At=progress95_at,
                uploadQueuedAt=progress95_at,
                uploadStartedAt=None,
                uploadFinishedAt=None,
                uploadQueueWaitSeconds=None,
                uploadRunSeconds=None,
                outputSizeBytes=None,
                uploadBackend=getattr(self.oss, "upload_backend", None),
                endpoint=getattr(self.oss, "endpoint", None),
                ossutilPath=getattr(self.oss, "ossutil_path", None),
                _uploadQueuedMonotonic=queued_monotonic,
            )
            logger.info("task %s reached 95%%; output upload queued: %s", task_id, output_path)
            requested_oss_key = message.get("oss_key") if isinstance(message.get("oss_key"), str) else None
            if requested_oss_key is None:
                future = self.upload_executor.submit(self._upload_output, task_id, output_path)
            else:
                future = self.upload_executor.submit(self._upload_output, task_id, output_path, requested_oss_key)
            cleanup_dir = message.get("cleanup_dir") if isinstance(message.get("cleanup_dir"), str) else None
            future.add_done_callback(
                lambda item, task_id=task_id, cleanup_dir=cleanup_dir: self._handle_upload_done(task_id, item, cleanup_dir)
            )
            self._drain()
            return

        if message_type == "task_failed":
            record = self.store.get(task_id)
            if record and record.attempt < TASK_MAX_ATTEMPT:
                self.store.record_failure(task_id, message["error"])
                self.store.reset_to_queue(task_id)
                with self._lock:
                    self.queue.append(
                        WorkerTask(
                            task_id=task_id,
                            task_kind=record.task_kind,
                            source_name=record.source_name,
                            payload=record.payload,
                        )
                    )
            else:
                self.store.mark_failed(task_id, message["error"])
                self.on_event({"type": "failed", "taskId": task_id, "error": message["error"]})
            self._drain()
            return

        if message_type == "progress":
            now = time.time()
            last = self.progress_throttle.get(task_id, 0.0)
            if now - last > 1.0:
                self.progress_throttle[task_id] = now
                self.store.update_progress(task_id, int(message["progress"]))
                self.on_event({"type": "progress", "taskId": task_id, "progress": int(message["progress"])})
            return

        if message_type == "task_metadata":
            updates = message.get("updates")
            if isinstance(updates, dict):
                self.store.patch_payload(task_id, updates)

    def _upload_output(self, task_id: str, output_path: str, requested_oss_key: str | None = None) -> str:
        started_monotonic = time.monotonic()
        output_file = Path(output_path)
        try:
            output_size_bytes = output_file.stat().st_size
        except OSError:
            output_size_bytes = None
        queued_monotonic = self._get_raw_upload_diagnostics(task_id).get("_uploadQueuedMonotonic")
        queue_wait_seconds = None
        if isinstance(queued_monotonic, float):
            queue_wait_seconds = started_monotonic - queued_monotonic
        self._update_upload_diagnostics(
            task_id,
            uploadStartedAt=now_beijing_iso(),
            uploadQueueWaitSeconds=_round_elapsed(queue_wait_seconds),
            outputSizeBytes=output_size_bytes,
        )
        oss_key = requested_oss_key or self.oss.output_key(task_id)
        last_error: Exception | None = None
        for attempt in range(1, max(1, UPLOAD_MAX_ATTEMPT) + 1):
            self._update_upload_diagnostics(task_id, uploadAttempt=attempt, ossKey=oss_key)
            try:
                self.oss.upload(output_path, oss_key)
                upload_run_seconds = time.monotonic() - started_monotonic
                self._update_upload_diagnostics(
                    task_id,
                    uploadFinishedAt=now_beijing_iso(),
                    uploadRunSeconds=_round_elapsed(upload_run_seconds),
                    uploadError=None,
                )
                logger.info(
                    "task %s output upload finished: size=%s bytes wait=%.3fs run=%.3fs backend=%s endpoint=%s",
                    task_id,
                    output_size_bytes,
                    queue_wait_seconds or 0.0,
                    upload_run_seconds,
                    getattr(self.oss, "upload_backend", None),
                    getattr(self.oss, "endpoint", None),
                )
                return oss_key
            except Exception as exc:
                last_error = exc
                self._update_upload_diagnostics(task_id, uploadError=str(exc))
                if attempt >= max(1, UPLOAD_MAX_ATTEMPT):
                    break
                logger.warning("upload failed for %s attempt %d/%d", task_id, attempt, UPLOAD_MAX_ATTEMPT, exc_info=True)
                time.sleep(min(10.0, 2.0 * attempt))
        upload_run_seconds = time.monotonic() - started_monotonic
        self._update_upload_diagnostics(
            task_id,
            uploadFinishedAt=now_beijing_iso(),
            uploadRunSeconds=_round_elapsed(upload_run_seconds),
            uploadError=str(last_error) if last_error else "unknown upload error",
        )
        logger.error(
            "task %s output upload failed after %.3fs: %s",
            task_id,
            upload_run_seconds,
            last_error,
        )
        raise RuntimeError(f"Output upload failed after {max(1, UPLOAD_MAX_ATTEMPT)} attempt(s): {last_error}")

    def _handle_upload_done(self, task_id: str, future: Future[str], cleanup_dir: str | None = None) -> None:
        try:
            oss_key = future.result()
        except Exception as exc:
            error = str(exc)
            self.store.mark_failed(task_id, error)
            self.on_event({"type": "failed", "taskId": task_id, "error": error})
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return
        self.store.mark_completed(task_id, oss_key)
        self.on_event({"type": "completed", "taskId": task_id, "ossKey": oss_key})
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    def _handle_worker_dead(self, worker_id: int, task_id: str | None) -> None:
        if not task_id:
            return
        record = self.store.get(task_id)
        if record is None:
            return
        if record.attempt >= TASK_MAX_ATTEMPT:
            self.store.mark_failed(task_id, "Worker crashed and exceeded retry limit.")
            self.on_event({"type": "failed", "taskId": task_id, "error": "Worker crashed"})
            return
        self.store.record_failure(task_id, "Worker process exited unexpectedly.")
        self.store.reset_to_queue(task_id)
        with self._lock:
            self.queue.append(
                WorkerTask(
                    task_id=task_id,
                    task_kind=record.task_kind,
                    source_name=record.source_name,
                    payload=record.payload,
                )
            )
        self._drain()
