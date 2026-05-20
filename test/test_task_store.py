from __future__ import annotations

from datetime import UTC, datetime

from videocut.store import TaskRecord, TaskStore


def _make_task(task_id: str = "t_demo") -> TaskRecord:
    return TaskRecord(
        id=task_id,
        task_kind="pipeline",
        source_name="trim-mixed-concat",
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


def test_task_store_preserves_failure_history_across_retry(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())

    assert store.mark_rendering("t_demo") == 1
    store.record_failure("t_demo", "download timeout")
    store.reset_to_queue("t_demo")
    assert store.mark_rendering("t_demo") == 2
    store.mark_completed("t_demo", "GouMei-Video-Cut/outputs/t_demo/final.mp4")

    task = store.get("t_demo")
    assert task is not None
    assert task.status == "completed"
    assert task.attempt == 2
    assert task.error is None
    assert task.last_error == "download timeout"
    assert task.last_error_at is not None

    failures = store.list_failures("t_demo")
    assert len(failures) == 1
    assert failures[0].attempt == 1
    assert failures[0].error == "download timeout"
    store.close()


def test_task_store_records_final_failure(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_failed"))

    assert store.mark_rendering("t_failed") == 1
    store.mark_failed("t_failed", "ffmpeg exited with code 1")

    task = store.get("t_failed")
    assert task is not None
    assert task.status == "failed"
    assert task.error == "ffmpeg exited with code 1"
    assert task.last_error == "ffmpeg exited with code 1"

    failures = store.list_failures("t_failed")
    assert len(failures) == 1
    assert failures[0].attempt == 1
    assert failures[0].error == "ffmpeg exited with code 1"
    store.close()


def test_task_store_empty_summary_and_active_tasks(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")

    assert store.count_tasks_by_status() == {
        "total": 0,
        "pending": 0,
        "rendering": 0,
        "completed": 0,
        "failed": 0,
    }
    assert store.list_active_tasks() == []
    store.close()
