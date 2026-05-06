from __future__ import annotations

from datetime import UTC, datetime

from videocut.queue.task_queue import TaskQueue, WorkerTask
from videocut.queue.worker_process import _task_temp_dir
from videocut.store import TaskRecord, TaskStore


class RecordingPool:
    def __init__(self) -> None:
        self.dispatched: list[WorkerTask] = []

    @property
    def idle_count(self) -> int:
        return 1

    def dispatch(self, task: WorkerTask) -> bool:
        self.dispatched.append(task)
        return True


def _make_task(task_id: str = "t_demo") -> TaskRecord:
    return TaskRecord(
        id=task_id,
        task_kind="pipeline",
        source_name="bgm-concat",
        status="pending",
        progress=0,
        attempt=0,
        payload={"clips": ["demo.mp4"]},
        oss_key=None,
        error=None,
        last_error=None,
        last_error_at=None,
        created_at=datetime.now(UTC).isoformat(),
        started_at=None,
        completed_at=None,
    )


def test_task_queue_dispatches_current_attempt(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    queue = TaskQueue(store, object(), 1, lambda event: None, tmp_path)
    pool = RecordingPool()
    queue.pool = pool  # type: ignore[assignment]

    queue.enqueue(WorkerTask("t_demo", "pipeline", "bgm-concat", {"clips": ["demo.mp4"]}))
    store.record_failure("t_demo", "first failure")
    store.reset_to_queue("t_demo")
    queue.enqueue(WorkerTask("t_demo", "pipeline", "bgm-concat", {"clips": ["demo.mp4"]}))

    assert [task.attempt for task in pool.dispatched] == [1, 2]
    task = store.get("t_demo")
    assert task is not None
    assert task.attempt == 2
    store.close()


def test_worker_task_temp_dir_is_attempt_and_worker_unique(tmp_path) -> None:
    assert _task_temp_dir(tmp_path, "t_demo", 2, 7) == tmp_path / "t_demo_attempt2_worker7"
