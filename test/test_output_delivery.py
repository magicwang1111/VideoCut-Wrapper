from concurrent.futures import Future
from pathlib import Path
import importlib

import pytest
from fastapi.testclient import TestClient

from videocut.oss.client import OssClient
from videocut.queue.task_queue import TaskQueue
from videocut.store import TaskStore
from test_queue_attempts import _make_task, RecordingPool

api = importlib.import_module("videocut.api.app")


@pytest.mark.parametrize("head_error", [FileNotFoundError("Consumed output"), TimeoutError("OSS unavailable")])
def test_completed_delivery_query_and_redirect_do_not_recheck_oss(tmp_path, monkeypatch, head_error):
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    app = api.create_app()
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    store.mark_completed("t_demo", "outputs/final.mp4")
    oss = OssClient()
    oss.local_root = None
    calls = []
    def unavailable(key):
        calls.append(key)
        raise head_error
    monkeypatch.setattr(oss, "head", unavailable)
    app.state.store = store
    app.state.oss = oss
    app.state.task_queue = TaskQueue(store, oss, 1, lambda e: None, tmp_path)
    client = TestClient(app)
    result = client.get("/tasks/t_demo/download", headers={"X-Api-Key": "test-key"}, follow_redirects=False)
    assert result.status_code == 302
    assert result.headers["location"] == oss.public_url("outputs/final.mp4")
    query = client.get("/tasks/t_demo", headers={"X-Api-Key": "test-key"}).json()
    assert query["status"] == "completed"
    assert query["outputUrl"] == result.headers["location"]
    assert "outputAvailable" not in query
    assert "outputCheckError" not in query
    assert calls == []
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
    assert not output.exists()
    store.close()


@pytest.mark.parametrize("nested_output", [True, False])
def test_success_cleans_local_output_even_if_consumer_deletes_oss_immediately(tmp_path, monkeypatch, nested_output):
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.mp4").write_bytes(b"input")
    output = (workspace if nested_output else tmp_path) / "final.mp4"
    output.write_bytes(b"video")
    oss = OssClient()
    evidence = oss.upload(output, "outputs/final.mp4")
    assert evidence["remoteSizeBytes"] == 5
    def consume(event):
        assert store.get("t_demo").status == "completed"
        oss.delete(event["ossKey"])
    queue = TaskQueue(store, oss, 1, consume, tmp_path)
    result = Future()
    result.set_result("outputs/final.mp4")
    queue._handle_upload_done("t_demo", result, str(output), str(workspace))
    assert not output.exists()
    assert not workspace.exists()
    assert not (tmp_path / "oss/outputs/final.mp4").exists()
    assert store.get("t_demo").status == "completed"
    assert queue.get_upload_diagnostics("t_demo")["localDeletedAt"]
    queue.upload_executor.shutdown()
    store.close()


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_old_task_cleanup_is_not_blocked_by_legacy_retention_metadata(tmp_path, status):
    store = TaskStore(tmp_path / "tasks.db")
    task = _make_task()
    task.created_at = "2020-01-01T00:00:00+08:00"
    store.create(task)
    if status == "completed":
        store.mark_completed(task.id, "outputs/consumed.mp4")
    else:
        store.mark_failed(task.id, "upload failed")
    store.patch_payload(task.id, {"_upload_diagnostics": {
        "localOutputPath": str(tmp_path / "final.mp4"), "localDeletedAt": None,
        "localRetainUntil": "2020-01-02T00:00:00+00:00",
    }})
    assert store.cleanup_old_tasks(7) == 1
    assert store.get(task.id) is None
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
