from __future__ import annotations

import threading
from datetime import UTC, datetime

from videocut.queue.task_queue import TaskQueue, WorkerTask
from videocut.queue.worker_process import _download_user_bgm, _task_temp_dir
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
    queue.upload_executor.shutdown(wait=True)
    store.close()


def test_task_queue_dispatches_next_render_before_output_upload_finishes(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_rendered"))
    store.create(_make_task("t_next"))
    store.mark_rendering("t_rendered")

    queue = TaskQueue(store, object(), 1, lambda event: None, tmp_path)
    pool = RecordingPool()
    queue.pool = pool  # type: ignore[assignment]
    queue.queue.append(WorkerTask("t_next", "pipeline", "bgm-concat", {"clips": ["next.mp4"]}))

    upload_can_finish = threading.Event()

    def fake_upload_output(task_id: str, output_path: str) -> str:
        assert task_id == "t_rendered"
        assert output_path == "/tmp/final.mp4"
        assert upload_can_finish.wait(timeout=5)
        return "GouMei-Video-Cut/outputs/t_rendered/final.mp4"

    queue._upload_output = fake_upload_output  # type: ignore[method-assign]

    queue._handle_message({"type": "task_rendered", "task_id": "t_rendered", "output_path": "/tmp/final.mp4"})

    assert [task.task_id for task in pool.dispatched] == ["t_next"]
    rendered = store.get("t_rendered")
    assert rendered is not None
    assert rendered.status == "rendering"
    assert rendered.progress == 95

    upload_can_finish.set()
    queue.upload_executor.shutdown(wait=True)

    completed = store.get("t_rendered")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.oss_key == "GouMei-Video-Cut/outputs/t_rendered/final.mp4"
    store.close()


def test_worker_task_temp_dir_is_attempt_and_worker_unique(tmp_path) -> None:
    assert _task_temp_dir(tmp_path, "t_demo", 2, 7) == tmp_path / "t_demo_attempt2_worker7"


def test_worker_downloads_user_bgm_to_task_temp_dir(tmp_path) -> None:
    class FakeOss:
        def download(self, oss_key, local_path) -> None:
            assert oss_key == "GouMei-Video-Cut/user-audio/audio1.mp3"
            local_path.write_text("audio", encoding="utf-8")

    local_path = _download_user_bgm(
        FakeOss(),  # type: ignore[arg-type]
        {"user_bgm": {"ossKey": "GouMei-Video-Cut/user-audio/audio1.mp3"}},
        tmp_path,
    )

    assert local_path == str(tmp_path / "user_audio.mp3")
    assert (tmp_path / "user_audio.mp3").read_text(encoding="utf-8") == "audio"
