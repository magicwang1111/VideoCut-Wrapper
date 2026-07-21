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


class StartableRecordingPool(RecordingPool):
    def start(self) -> None:
        return

    @property
    def active_count(self) -> int:
        return 0


class FakeOss:
    upload_backend = "fake"
    endpoint = "oss-test"
    ossutil_path = "ossutil64"

    def output_key(self, task_id: str) -> str:
        return f"GouMei-Video-Cut/outputs/{task_id}/final.mp4"

    def upload(self, local_path, oss_key) -> None:
        assert oss_key.startswith("GouMei-Video-Cut/outputs/")
        assert local_path


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


def test_blue_green_marker_skips_replay_once_then_restores_recovery(tmp_path, monkeypatch) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_handoff"))
    marker = tmp_path / "skip-replay-once"
    marker.touch()
    monkeypatch.setenv("TASK_REPLAY_SKIP_ONCE_FILE", str(marker))

    handoff_queue = TaskQueue(store, object(), 1, lambda event: None, tmp_path)
    handoff_pool = StartableRecordingPool()
    handoff_queue.pool = handoff_pool  # type: ignore[assignment]
    handoff_queue.start()

    assert not marker.exists()
    assert handoff_pool.dispatched == []
    assert handoff_queue.active_worker_count == 0
    handoff_queue.upload_executor.shutdown(wait=True)

    restarted_queue = TaskQueue(store, object(), 1, lambda event: None, tmp_path)
    restarted_pool = StartableRecordingPool()
    restarted_queue.pool = restarted_pool  # type: ignore[assignment]
    restarted_queue.start()

    assert [task.task_id for task in restarted_pool.dispatched] == ["t_handoff"]
    restarted_queue.upload_executor.shutdown(wait=True)
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


def test_task_queue_records_output_upload_diagnostics(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_rendered"))
    store.mark_rendering("t_rendered")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")

    queue = TaskQueue(store, FakeOss(), 1, lambda event: None, tmp_path)
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_message({"type": "task_rendered", "task_id": "t_rendered", "output_path": str(output_path)})
    queue.upload_executor.shutdown(wait=True)

    diagnostics = queue.get_upload_diagnostics("t_rendered")
    assert diagnostics is not None
    assert diagnostics["progress95At"]
    assert diagnostics["uploadQueuedAt"]
    assert diagnostics["uploadStartedAt"]
    assert diagnostics["uploadFinishedAt"]
    assert diagnostics["uploadQueueWaitSeconds"] >= 0
    assert diagnostics["uploadRunSeconds"] >= 0
    assert diagnostics["outputSizeBytes"] == len(b"video-data")
    assert diagnostics["uploadBackend"] == "fake"
    assert diagnostics["endpoint"] == "oss-test"
    assert diagnostics["ossKey"] == "GouMei-Video-Cut/outputs/t_rendered/final.mp4"

    completed = store.get("t_rendered")
    assert completed is not None
    assert completed.status == "completed"
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
