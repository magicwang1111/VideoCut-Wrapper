from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from videocut.log import get_logger
from videocut.oss import OssClient
from videocut.queue.worker_process import worker_main
from videocut.runtime_paths import resolve_runtime_path
from videocut.store import TaskStore

TASK_MAX_ATTEMPT = int(os.getenv("TASK_MAX_ATTEMPT", "3"))
QUEUE_MAX = int(os.getenv("QUEUE_MAX", "200"))

TaskEventHandler = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class WorkerTask:
    task_id: str
    task_kind: str
    source_name: str
    payload: dict[str, Any]


@dataclass(slots=True)
class WorkerState:
    worker_id: int
    process: mp.Process
    input_queue: mp.Queue
    current_task_id: str | None = None
    ready: bool = False


logger = get_logger(__name__)


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
            elif message["type"] in {"task_done", "task_failed"}:
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
        self._lock = threading.RLock()
        self.pool = WorkerPool(worker_count, Path(root_dir).resolve(), self._handle_message, self._handle_worker_dead)

    def start(self) -> None:
        self.pool.start()
        self._replay_from_store()
        self._drain()

    def stop(self) -> None:
        self.pool.stop()

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

    def _drain(self) -> None:
        with self._lock:
            while self.queue and self.pool.idle_count > 0:
                task = self.queue.pop(0)
                self.store.mark_rendering(task.task_id)
                if not self.pool.dispatch(task):
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
