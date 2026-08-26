from __future__ import annotations

import threading
from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path

import pytest

import videocut.queue.task_queue as task_queue_module
from videocut.aigc import AigcMetadataError, build_aigc_metadata
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


def test_task_queue_persists_external_job_and_reuses_record(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_subtitle"))
    store.mark_rendering("t_subtitle")
    queue = TaskQueue(store, object(), 1, lambda event: None, tmp_path)
    queue.pool = RecordingPool()  # type: ignore[assignment]

    queue._handle_message({
        "type": "external_job",
        "task_id": "t_subtitle",
        "job": {
            "external_task_id": "mps-queue",
            "submitted_attempt": 1,
            "status": "submitted",
            "persist_state": True,
        },
    })
    queue._handle_message({
        "type": "external_job",
        "task_id": "t_subtitle",
        "job": {
            "external_task_id": "mps-queue",
            "status": "failed",
            "provider_status": "FAIL",
            "error_code": "60000",
            "error_code_ext": "302",
            "message": "Server returned 5XX Server Error reply",
            "polled": True,
            "completed": True,
        },
    })

    task = store.get("t_subtitle")
    assert task is not None
    assert task.payload["subtitle_state"] == {"mps_task_id": "mps-queue"}
    jobs = store.list_external_jobs("t_subtitle")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].error_code == "60000"
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


def test_task_queue_records_output_upload_diagnostics(tmp_path, monkeypatch) -> None:
    events: list[str] = []

    class OrderedOss(FakeOss):
        def upload(self, local_path, oss_key) -> None:
            assert Path(local_path).read_bytes() == b"tagged-video-data"
            events.append("upload")
            super().upload(local_path, oss_key)

    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_rendered"))
    store.mark_rendering("t_rendered")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")
    calls: list[tuple[str, str]] = []

    def fake_embed(ffmpeg_path, ffprobe_path, video_path, task_id, **kwargs):
        calls.append((str(video_path), task_id))
        Path(video_path).write_bytes(b"tagged-video-data")
        events.append("label")
        return build_aigc_metadata(task_id)

    monkeypatch.setattr(task_queue_module, "embed_aigc_metadata", fake_embed)

    queue = TaskQueue(store, OrderedOss(), 1, lambda event: None, tmp_path)
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
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
    assert diagnostics["outputSizeBytes"] == len(b"tagged-video-data")
    assert diagnostics["uploadBackend"] == "fake"
    assert diagnostics["endpoint"] == "oss-test"
    assert diagnostics["ossKey"] == "GouMei-Video-Cut/outputs/t_rendered/final.mp4"
    assert calls == [(str(output_path), "t_rendered")]
    assert events == ["label", "upload"]

    completed = store.get("t_rendered")
    assert completed is not None
    assert completed.status == "completed"
    assert not output_path.exists()
    store.close()


def test_local_output_cleanup_failure_does_not_reverse_completed_task(tmp_path, caplog) -> None:
    events: list[dict] = []
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_cleanup_warning"))
    store.mark_rendering("t_cleanup_warning")
    output_path = tmp_path / "final.mp4"
    output_path.mkdir()
    future: Future[str] = Future()
    future.set_result("GouMei-Video-Cut/outputs/t_cleanup_warning/final.mp4")

    queue = TaskQueue(store, FakeOss(), 1, events.append, tmp_path)
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_upload_done("t_cleanup_warning", future, str(output_path))
    queue.upload_executor.shutdown(wait=True)

    completed = store.get("t_cleanup_warning")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.oss_key == "GouMei-Video-Cut/outputs/t_cleanup_warning/final.mp4"
    assert output_path.exists()
    assert events == [
        {
            "type": "completed",
            "taskId": "t_cleanup_warning",
            "ossKey": "GouMei-Video-Cut/outputs/t_cleanup_warning/final.mp4",
        }
    ]
    assert "local output cleanup failed" in caplog.text
    store.close()


def test_output_upload_failure_retains_local_output(tmp_path, monkeypatch) -> None:
    class FailingOss(FakeOss):
        def upload(self, local_path, oss_key) -> None:
            raise RuntimeError("simulated OSS failure")

    def fake_embed(ffmpeg_path, ffprobe_path, video_path, task_id, **kwargs):
        return build_aigc_metadata(task_id)

    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_upload_failed"))
    store.mark_rendering("t_upload_failed")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")
    monkeypatch.setattr(task_queue_module, "embed_aigc_metadata", fake_embed)
    monkeypatch.setattr(task_queue_module, "UPLOAD_MAX_ATTEMPT", 1)

    queue = TaskQueue(store, FailingOss(), 1, lambda event: None, tmp_path)
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_message(
        {"type": "task_rendered", "task_id": "t_upload_failed", "output_path": str(output_path)}
    )
    queue.upload_executor.shutdown(wait=True)

    failed = store.get("t_upload_failed")
    assert failed is not None
    assert failed.status == "failed"
    assert "simulated OSS failure" in (failed.error or "")
    assert output_path.exists()
    store.close()


def test_aigc_labeling_failure_prevents_upload(tmp_path, monkeypatch) -> None:
    class RejectingOss(FakeOss):
        def __init__(self) -> None:
            self.uploaded = False

        def upload(self, local_path, oss_key) -> None:
            self.uploaded = True

    def fail_embed(ffmpeg_path, ffprobe_path, video_path, task_id, **kwargs):
        raise RuntimeError("invalid AIGC metadata")

    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_unlabeled"))
    store.mark_rendering("t_unlabeled")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")
    oss = RejectingOss()
    monkeypatch.setattr(task_queue_module, "embed_aigc_metadata", fail_embed)

    queue = TaskQueue(store, oss, 1, lambda event: None, tmp_path)
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_message({"type": "task_rendered", "task_id": "t_unlabeled", "output_path": str(output_path)})
    queue.upload_executor.shutdown(wait=True)

    task = store.get("t_unlabeled")
    diagnostics = queue.get_upload_diagnostics("t_unlabeled")
    assert task is not None
    assert task.status == "failed"
    assert "AIGC metadata labeling failed [3006/AIGC_METADATA_UNKNOWN] after 3 attempt(s)" in (task.error or "")
    assert task.payload["_aigc_failure"]["code"] == 3006
    assert task.payload["_aigc_failure"]["reason"] == "AIGC_METADATA_UNKNOWN"
    assert len(task.payload["_aigc_failure"]["attempts"]) == 3
    assert diagnostics is not None
    assert diagnostics["uploadError"] == "invalid AIGC metadata"
    assert diagnostics["aigcMetadataAttempt"] == 3
    assert diagnostics["aigcMetadataStatus"] == "failed"
    assert diagnostics["aigcMetadataErrorCode"] == 3006
    assert diagnostics["aigcMetadataErrorReason"] == "AIGC_METADATA_UNKNOWN"
    assert oss.uploaded is False
    assert output_path.exists()
    store.close()


def test_aigc_labeling_retries_then_uploads(tmp_path, monkeypatch) -> None:
    calls = 0

    def flaky_embed(ffmpeg_path, ffprobe_path, video_path, task_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise AigcMetadataError(
                "temporary ffmpeg failure",
                reason="AIGC_FFMPEG_FAILED",
                phase="write",
            )
        return build_aigc_metadata(task_id)

    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_retry"))
    store.mark_rendering("t_retry")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")
    monkeypatch.setattr(task_queue_module, "embed_aigc_metadata", flaky_embed)
    monkeypatch.setattr(task_queue_module.time, "sleep", lambda seconds: None)

    queue = TaskQueue(store, FakeOss(), 1, lambda event: None, tmp_path)
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_message({"type": "task_rendered", "task_id": "t_retry", "output_path": str(output_path)})
    queue.upload_executor.shutdown(wait=True)

    task = store.get("t_retry")
    diagnostics = queue.get_upload_diagnostics("t_retry")
    assert calls == 3
    assert task is not None
    assert task.status == "completed"
    assert task.payload["_aigc_failure"] is None
    assert diagnostics is not None
    assert diagnostics["aigcMetadataAttempt"] == 3
    assert diagnostics["aigcMetadataStatus"] == "verified"
    assert len(diagnostics["aigcMetadataErrors"]) == 2
    store.close()


def test_aigc_non_retryable_failure_stops_after_one_attempt(tmp_path, monkeypatch) -> None:
    calls = 0

    def fail_precheck(ffmpeg_path, ffprobe_path, video_path, task_id, **kwargs):
        nonlocal calls
        calls += 1
        raise AigcMetadataError(
            "source is missing",
            reason="AIGC_SOURCE_MISSING_OR_EMPTY",
            phase="precheck",
            retryable=False,
        )

    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_precheck"))
    store.mark_rendering("t_precheck")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video-data")
    monkeypatch.setattr(task_queue_module, "embed_aigc_metadata", fail_precheck)

    queue = TaskQueue(store, FakeOss(), 1, lambda event: None, tmp_path)
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
    queue.pool = RecordingPool()  # type: ignore[assignment]
    queue._handle_message({"type": "task_rendered", "task_id": "t_precheck", "output_path": str(output_path)})
    queue.upload_executor.shutdown(wait=True)

    task = store.get("t_precheck")
    assert calls == 1
    assert task is not None
    assert task.status == "failed"
    assert task.payload["_aigc_failure"]["attempt"] == 1
    assert task.payload["_aigc_failure"]["retryable"] is False
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
