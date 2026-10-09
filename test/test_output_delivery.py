from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib

import pytest
from fastapi.testclient import TestClient

from videocut.oss.client import OssClient, OutputMissingError
from videocut.queue.task_queue import TaskQueue
from videocut.store import TaskStore
from test_queue_attempts import _make_task, RecordingPool

api = importlib.import_module("videocut.api.app")


@pytest.mark.parametrize("mode,status,code", [("missing", 410, 3007), ("unavailable", 503, 3008), ("present", 200, None)])
def test_download_checks_current_object_without_changing_completed_status(tmp_path, monkeypatch, mode, status, code):
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    app = api.create_app()
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    store.mark_completed("t_demo", "outputs/final.mp4")
    oss = OssClient()
    output = tmp_path / "oss/outputs/final.mp4"
    output.parent.mkdir(parents=True)
    if mode == "present":
        output.write_bytes(b"real-file")
    if mode == "unavailable":
        def unavailable(key):
            raise TimeoutError("HEAD timed out")
        monkeypatch.setattr(oss, "head", unavailable)
    app.state.store = store
    app.state.oss = oss
    app.state.task_queue = TaskQueue(store, oss, 1, lambda e: None, tmp_path)
    client = TestClient(app)
    result = client.get("/tasks/t_demo/download", headers={"X-Api-Key": "test-key"})
    assert result.status_code == status
    if code:
        assert result.json()["error_code"] == code
        assert result.json()["details"]["taskId"] == "t_demo"
        assert result.json()["details"]["requestId"]
    query = client.get("/tasks/t_demo", headers={"X-Api-Key": "test-key"}).json()
    assert query["status"] == "completed"
    assert query["outputAvailable"] is (True if mode == "present" else False if mode == "missing" else None)
    if mode != "present":
        assert query["outputUrl"] is None
    app.state.task_queue.upload_executor.shutdown()
    store.close()


def test_failed_upload_keeps_output_inside_cleanup_directory(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    folder = tmp_path / "render"
    folder.mkdir()
    output = folder / "final.mp4"
    output.write_bytes(b"video")
    queue = TaskQueue(store, object(), 1, lambda e: None, tmp_path)
    result = Future()
    result.set_exception(RuntimeError("Remote object verification failed"))
    queue._handle_upload_done("t_demo", result, str(output), str(folder))
    assert output.read_bytes() == b"video"
    assert store.get("t_demo").status == "failed"
    queue.upload_executor.shutdown()
    store.close()


def test_restart_resumes_saved_mp4_without_render_dispatch(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    store.mark_rendering("t_demo")
    output = tmp_path / "final.mp4"
    output.write_bytes(b"video")
    store.patch_payload("t_demo", {"_upload_diagnostics": {"localOutputPath": str(output), "ossKey": "outputs/final.mp4"}})
    queue = TaskQueue(store, object(), 1, lambda e: None, tmp_path)
    queue.pool = RecordingPool()
    def upload(task_id, path, key=None):
        assert Path(path).read_bytes() == b"video"
        return key
    monkeypatch.setattr(queue, "_upload_output", upload)
    queue._replay_from_store()
    queue.upload_executor.shutdown()
    assert queue.pool.dispatched == []
    assert queue.queue == []
    assert store.get("t_demo").status == "completed"
    assert output.exists()
    store.close()


@pytest.mark.parametrize("remote_ok", [True, False])
def test_retention_only_cleans_expired_verified_completed_output(tmp_path, monkeypatch, remote_ok):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    output = tmp_path / "final.mp4"
    output.write_bytes(b"video")
    oss = OssClient()
    if remote_ok:
        oss.upload(output, "outputs/final.mp4")
    store.mark_completed("t_demo", "outputs/final.mp4")
    store.patch_payload("t_demo", {"_upload_diagnostics": {"localOutputPath": str(output), "outputSizeBytes": 5,
        "localRetainUntil": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}})
    queue = TaskQueue(store, oss, 1, lambda e: None, tmp_path)
    queue.cleanup_retained_outputs()
    assert output.exists() is (not remote_ok)
    assert store.get("t_demo").status == "completed"
    queue.upload_executor.shutdown()
    store.close()


def test_retry_upload_restores_missing_object_without_model_or_render(tmp_path, monkeypatch):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    store.mark_failed("t_demo", "upload failed")
    output = tmp_path / "final.mp4"
    output.write_bytes(b"original-video")
    store.patch_payload("t_demo", {"_upload_diagnostics": {"localOutputPath": str(output), "ossKey": "outputs/original.mp4"}})
    queue = TaskQueue(store, OssClient(), 1, lambda e: None, tmp_path)
    queue.pool = RecordingPool()
    from videocut.aigc import build_aigc_metadata
    monkeypatch.setattr("videocut.queue.task_queue.embed_aigc_metadata", lambda *a, **k: build_aigc_metadata("t_demo"))
    queue.ffmpeg_path = "ffmpeg"
    queue.ffprobe_path = "ffprobe"
    assert queue.retry_output_upload("t_demo")
    queue.upload_executor.shutdown()
    assert store.get("t_demo").status == "completed"
    assert (tmp_path / "oss/outputs/original.mp4").read_bytes() == b"original-video"
    assert queue.pool.dispatched == []
    saved = store.get("t_demo").payload["_upload_diagnostics"]
    assert saved["outputAvailable"] is True
    assert saved["remoteSizeBytes"] == 14
    assert saved["outputVerifiedAt"]
    store.close()


def test_old_task_cleanup_preserves_recovery_record(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = _make_task()
    task.created_at = "2020-01-01T00:00:00+08:00"
    store.create(task)
    store.mark_failed(task.id, "upload failed")
    store.patch_payload(task.id, {"_upload_diagnostics": {"localOutputPath": str(tmp_path / "final.mp4")}})
    assert store.cleanup_old_tasks(7) == 0
    assert store.get(task.id) is not None
    store.close()


def test_legacy_completion_event_cannot_bypass_remote_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    queue = TaskQueue(store, OssClient(), 1, lambda e: None, tmp_path)
    queue.pool = RecordingPool()
    queue._handle_message({"type": "task_done", "task_id": "t_demo", "oss_key": "outputs/missing.mp4"})
    assert store.get("t_demo").status == "failed"
    queue.upload_executor.shutdown()
    store.close()
