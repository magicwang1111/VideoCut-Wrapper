from __future__ import annotations

from dataclasses import asdict
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from videocut.errors import PipelineDefinitionError, VideoCutError
from videocut.pipeline import PipelineRegistry, build_pipeline_context, parse_pipeline_config
from videocut.pipeline.config import validate_variables, resolve_variable_values
from videocut.pipeline.types import PipelineVariableDef
from videocut.store import PipelineRecord, TaskRecord, TaskStore

api_app_module = importlib.import_module("videocut.api.app")


class FakeTaskQueue:
    instances: list["FakeTaskQueue"] = []

    def __init__(self, store, oss, worker_count, on_event, root_dir) -> None:
        self.store = store
        self.oss = oss
        self.worker_count = worker_count
        self.on_event = on_event
        self.root_dir = root_dir
        self.tasks = []
        self.upload_worker_count = 2
        FakeTaskQueue.instances.append(self)

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def enqueue(self, task) -> bool:
        self.tasks.append(task)
        return True

    def get_upload_diagnostics(self, task_id: str):
        return None

    @property
    def queue_size(self) -> int:
        return len(self.tasks)


class RejectingTaskQueue(FakeTaskQueue):
    def enqueue(self, task) -> bool:
        self.tasks.append(task)
        return False

    @property
    def queue_size(self) -> int:
        return 200


def _write_pipeline_config(root, name: str, payload: dict) -> None:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_pipeline_payload(name: str = "trim-mixed-dissolve-v1") -> dict:
    return {
        "name": name,
        "mode": "pipeline",
        "preset": "auto",
        "quality": "high",
        "clips": [
            {"trim_start": 2, "trim_end": 0},
            {"trim_start": 3, "trim_end": 0},
            {"trim_start": 1, "trim_end": 1},
        ],
        "transitions": [
            {"type": "flash-black", "duration": 0.5},
            {"type": "dissolve", "duration": 0.5},
        ],
        "default_transition": {"type": "cut", "duration": 0},
    }


def _configure_api_env(
    tmp_path,
    monkeypatch,
    pipelines_root: Path,
    *,
    bgm_dir: Path | None = None,
    bgm_backup_dir: Path | None = None,
) -> None:
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    if bgm_dir is None:
        monkeypatch.delenv("BGM_DIR", raising=False)
    else:
        monkeypatch.setenv("BGM_DIR", str(bgm_dir))
    if bgm_backup_dir is None:
        monkeypatch.delenv("BGM_BACKUP_DIR", raising=False)
    else:
        monkeypatch.setenv("BGM_BACKUP_DIR", str(bgm_backup_dir))
    monkeypatch.delenv("BGM_OSS_URI", raising=False)
    monkeypatch.delenv("BGM_BACKUP_OSS_URI", raising=False)
    monkeypatch.delenv("OSS_PUBLIC_ENDPOINT", raising=False)
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)


def _make_api_task(
    task_id: str,
    status: str,
    created_at: str,
    *,
    progress: int = 0,
    attempt: int = 0,
    started_at: str | None = None,
    completed_at: str | None = None,
    last_error: str | None = None,
    last_error_at: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        task_kind="pipeline",
        source_name="bgm-concat",
        status=status,  # type: ignore[arg-type]
        progress=progress,
        attempt=attempt,
        payload={"clips": ["GouMei-Video-Cut/inputs/file1.mp4"]},
        oss_key=None,
        error=None,
        last_error=last_error,
        last_error_at=last_error_at,
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
    )


def test_pipeline_registry_scans_and_syncs_to_store(tmp_path) -> None:
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())

    registry = PipelineRegistry(pipelines_root)
    registry.scan()

    store = TaskStore(tmp_path / "tasks.db")
    store.sync_pipelines(
        [
            PipelineRecord(
                name=item.name,
                source_path=str(item.source_path),
                config=asdict(item.config),
                updated_at="2026-04-16T00:00:00",
            )
            for item in registry.list_all()
        ]
    )

    stored = store.get_pipeline("trim-mixed-dissolve-v1")
    assert stored is not None
    assert stored.config["clips"][0]["trim_start"] == 2
    assert stored.config["default_transition"]["type"] == "cut"
    store.close()


def test_pipeline_registry_rejects_name_mismatch(tmp_path) -> None:
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "dir-name", _make_pipeline_payload(name="other-name"))

    registry = PipelineRegistry(pipelines_root)
    with pytest.raises(PipelineDefinitionError):
        registry.scan()


def test_build_pipeline_context_applies_binding_and_overrides(tmp_path) -> None:
    config = parse_pipeline_config(_make_pipeline_payload(), tmp_path / "config.json", require_name=True)
    ctx = build_pipeline_context(
        config,
        ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4", "/tmp/d.mp4"],
        tmp_path / "config.json",
        {
            "quality": "medium",
            "clip_overrides": [
                {"index": 1, "trim_start": 5, "trim_end": 1},
            ],
            "transition_overrides": [
                {"index": 2, "type": "flash-black", "duration": 1.2},
            ],
        },
    )

    assert [clip.trim_start for clip in ctx.config.clips] == [2.0, 5.0, 1.0, 0.0]
    assert [clip.trim_end for clip in ctx.config.clips] == [0.0, 1.0, 1.0, 0.0]
    assert ctx.config.quality == "medium"
    assert [(item.type, item.duration) for item in ctx.junctions] == [
        ("flash-black", 0.5),
        ("dissolve", 0.5),
        ("flash-black", 1.2),
    ]


def test_build_pipeline_context_reuses_source_indexes_for_fixed_segments(tmp_path) -> None:
    payload = {
        "name": "segment-5-6-then-3-5-concat",
        "mode": "pipeline",
        "clips": [
            {"source_index": 0, "trim_start": 5, "trim_duration": 1},
            {"source_index": 1, "trim_start": 5, "trim_duration": 1},
            {"source_index": 2, "trim_start": 5, "trim_duration": 1},
            {"source_index": 3, "trim_start": 5, "trim_duration": 1},
            {"source_index": 4, "trim_start": 5, "trim_duration": 1},
            {"source_index": 0, "trim_start": 3, "trim_duration": 2},
            {"source_index": 1, "trim_start": 3, "trim_duration": 2},
            {"source_index": 2, "trim_start": 3, "trim_duration": 2},
            {"source_index": 3, "trim_start": 3, "trim_duration": 2},
            {"source_index": 4, "trim_start": 3, "trim_duration": 2},
        ],
        "default_transition": {"type": "cut", "duration": 0},
    }
    config = parse_pipeline_config(payload, tmp_path / "config.json", require_name=True)
    input_clips = [f"/tmp/clip_{index}.mp4" for index in range(5)]

    ctx = build_pipeline_context(config, input_clips, tmp_path / "config.json")

    assert ctx.resolved_srcs == input_clips + input_clips
    assert [clip.source_index for clip in ctx.config.clips] == [0, 1, 2, 3, 4, 0, 1, 2, 3, 4]
    assert [clip.trim_start for clip in ctx.config.clips] == [5, 5, 5, 5, 5, 3, 3, 3, 3, 3]
    assert [clip.trim_duration for clip in ctx.config.clips] == [1, 1, 1, 1, 1, 2, 2, 2, 2, 2]
    assert [(item.type, item.duration) for item in ctx.junctions] == [("cut", 0)] * 9


def test_build_pipeline_context_enforces_required_clip_count(tmp_path) -> None:
    payload = {
        "name": "trim-2-5-concat",
        "mode": "pipeline",
        "required_clip_count": 5,
        "clips": [
            {"source_index": 0, "trim_start": 2, "trim_duration": 3},
            {"source_index": 1, "trim_start": 2, "trim_duration": 3},
            {"source_index": 2, "trim_start": 2, "trim_duration": 3},
            {"source_index": 3, "trim_start": 2, "trim_duration": 3},
            {"source_index": 4, "trim_start": 2, "trim_duration": 3},
        ],
        "default_transition": {"type": "cut", "duration": 0},
    }
    config = parse_pipeline_config(payload, tmp_path / "config.json", require_name=True)

    ctx = build_pipeline_context(config, [f"/tmp/clip_{index}.mp4" for index in range(5)], tmp_path / "config.json")

    assert [clip.source_index for clip in ctx.config.clips] == [0, 1, 2, 3, 4]
    assert [clip.trim_start for clip in ctx.config.clips] == [2, 2, 2, 2, 2]
    assert [clip.trim_duration for clip in ctx.config.clips] == [3, 3, 3, 3, 3]
    with pytest.raises(VideoCutError, match="exactly 5"):
        build_pipeline_context(config, [f"/tmp/clip_{index}.mp4" for index in range(4)], tmp_path / "config.json")
    with pytest.raises(VideoCutError, match="exactly 5"):
        build_pipeline_context(config, [f"/tmp/clip_{index}.mp4" for index in range(6)], tmp_path / "config.json")


def test_render_endpoint_pipeline_mode(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        store = client.app.state.store
        store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        store.save_file("file2", "GouMei-Video-Cut/inputs/file2.mp4")
        store.save_file("file3", "GouMei-Video-Cut/inputs/file3.mp4")

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["pipelines"] == 1

        headers = {"X-Api-Key": "test-key", "Content-Type": "application/json"}

        pipeline_response = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1", "file2", "file3"],
                "overrides": {"quality": "medium", "clip_overrides": [{"index": 1, "trim_start": 4}]},
            },
        )
        assert pipeline_response.status_code == 200
        body = pipeline_response.json()
        assert body["taskId"].startswith("t_")
        assert len(body["taskId"]) == 18

        pipeline_task = FakeTaskQueue.instances[-1].tasks[-1]
        assert pipeline_task.task_id == body["taskId"]
        assert pipeline_task.task_kind == "pipeline"
        assert pipeline_task.source_name == "trim-mixed-dissolve-v1"
        assert pipeline_task.payload["pipeline_config"]["name"] == "trim-mixed-dissolve-v1"
        assert pipeline_task.payload["clips"] == [
            "GouMei-Video-Cut/inputs/file1.mp4",
            "GouMei-Video-Cut/inputs/file2.mp4",
            "GouMei-Video-Cut/inputs/file3.mp4",
        ]

        # legacy template body is rejected with 422
        bad_response = client.post(
            "/render",
            headers=headers,
            json={
                "template": "trim-mixed-concat",
                "clips": ["file1", "file2"],
                "params": {"preset": "auto"},
            },
        )
        assert bad_response.status_code == 422
        assert bad_response.json()["error_code"] == 1003


def test_render_endpoint_rejects_wrong_required_clip_count(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    payload = _make_pipeline_payload("trim-2-5-concat")
    payload["required_clip_count"] = 5
    _write_pipeline_config(pipelines_root, "trim-2-5-concat", payload)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root)

    with TestClient(api_app_module.create_app()) as client:
        headers = {"X-Api-Key": "test-key", "Content-Type": "application/json"}
        too_few = client.post(
            "/render",
            headers=headers,
            json={"pipeline": "trim-2-5-concat", "clips": ["file1", "file2", "file3", "file4"]},
        )
        too_many = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-2-5-concat",
                "clips": ["file1", "file2", "file3", "file4", "file5", "file6"],
            },
        )

        assert too_few.status_code == 400
        assert too_few.json() == {
            "error_code": 2001,
            "message": "Pipeline requires exactly 5 input clips, got 4.",
            "details": {"requiredClipCount": 5, "clipCount": 4},
        }
        assert too_many.status_code == 400
        assert too_many.json() == {
            "error_code": 2001,
            "message": "Pipeline requires exactly 5 input clips, got 6.",
            "details": {"requiredClipCount": 5, "clipCount": 6},
        }
        assert FakeTaskQueue.instances[-1].tasks == []


def test_upload_audio_uses_user_audio_prefix_and_is_not_bgm_catalog(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_dir / "calm" / "1.mp3").write_text("library", encoding="utf-8")
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        response = client.post(
            "/upload",
            headers={"X-Api-Key": "test-key"},
            files={"file": ("Furious.mp3", b"audio", "audio/mpeg")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "user_audio"
        assert body["ossKey"] == f"GouMei-Video-Cut/user-audio/{body['fileId']}.mp3"
        assert "expiresAt" in body
        assert (tmp_path / "oss" / body["ossKey"]).read_bytes() == b"audio"

        store = client.app.state.store
        record = store.get_file(body["fileId"])
        assert record is not None
        assert record.kind == "user_audio"
        assert record.size_bytes == 5

        catalog = client.get("/bgm", headers={"X-Api-Key": "test-key"})
        assert catalog.status_code == 200
        assert all(item["filename"] != "Furious" for item in catalog.json()["files"])


def test_upload_video_keeps_asset_prefix(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    _configure_api_env(tmp_path, monkeypatch, pipelines_root)

    with TestClient(api_app_module.create_app()) as client:
        response = client.post(
            "/upload",
            headers={"X-Api-Key": "test-key"},
            files={"file": ("clip.mp4", b"video", "video/mp4")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "asset"
        assert body["ossKey"] == f"GouMei-Video-Cut/inputs/{body['fileId']}.mp4"
        assert "expiresAt" not in body


def test_render_endpoint_accepts_uploaded_user_audio_bgm(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    _configure_api_env(tmp_path, monkeypatch, pipelines_root)

    with TestClient(api_app_module.create_app()) as client:
        store = client.app.state.store
        store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        store.save_file(
            "audio1",
            "GouMei-Video-Cut/user-audio/audio1.mp3",
            kind="user_audio",
            size_bytes=123,
        )

        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"fileId": "audio1"}},
            },
        )

        assert response.status_code == 200
        pipeline_task = FakeTaskQueue.instances[-1].tasks[-1]
        assert pipeline_task.payload["user_bgm"] == {
            "fileId": "audio1",
            "ossKey": "GouMei-Video-Cut/user-audio/audio1.mp3",
        }
        assert pipeline_task.payload["overrides"]["bgm"]["enabled"] is True


def test_render_endpoint_accepts_bgm_catalog_overrides(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    (bgm_dir / "catalog").mkdir(parents=True)
    (bgm_dir / "catalog" / "song.mp3").write_text("music", encoding="utf-8")
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        store = client.app.state.store
        store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        headers = {"X-Api-Key": "test-key", "Content-Type": "application/json"}

        category_only = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "catalog"}},
            },
        )
        assert category_only.status_code == 200

        category_filename = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "catalog", "filename": "song"}},
            },
        )
        assert category_filename.status_code == 200
        tasks = FakeTaskQueue.instances[-1].tasks
        assert tasks[-2].payload["overrides"]["bgm"] == {"category": "catalog"}
        assert tasks[-1].payload["overrides"]["bgm"] == {"category": "catalog", "filename": "song"}
        assert "user_bgm" not in tasks[-1].payload


def test_render_endpoint_rejects_missing_bgm_category_before_enqueue(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    backup_dir = tmp_path / "runtime" / "bgm-backup"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir, bgm_backup_dir=backup_dir)

    with TestClient(api_app_module.create_app()) as client:
        client.app.state.store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "electronic"}},
            },
        )

        assert response.status_code == 400
        assert response.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {
                "field": "overrides.bgm.category",
                "category": "electronic",
                "bgmRoot": str(bgm_dir.resolve()),
                "backupBgmRoot": str(backup_dir.resolve()),
                "reason": "category_not_found",
            },
        }
        assert FakeTaskQueue.instances[-1].tasks == []


def test_render_endpoint_accepts_exact_bgm_file_from_backup_before_enqueue(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    backup_dir = tmp_path / "runtime" / "bgm-backup"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    (backup_dir / "legacy").mkdir(parents=True)
    (backup_dir / "legacy" / "old.mp3").write_text("music", encoding="utf-8")
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir, bgm_backup_dir=backup_dir)

    with TestClient(api_app_module.create_app()) as client:
        client.app.state.store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "legacy", "filename": "old"}},
            },
        )

        assert response.status_code == 200
        assert FakeTaskQueue.instances[-1].tasks[-1].payload["overrides"]["bgm"] == {
            "category": "legacy",
            "filename": "old",
        }


def test_render_endpoint_rejects_missing_exact_bgm_file_before_enqueue(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    backup_dir = tmp_path / "runtime" / "bgm-backup"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    (bgm_dir / "catalog").mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir, bgm_backup_dir=backup_dir)

    with TestClient(api_app_module.create_app()) as client:
        client.app.state.store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "catalog", "filename": "missing"}},
            },
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == 2007
        assert response.json()["details"]["reason"] == "file_not_found"
        assert response.json()["details"]["filename"] == "missing"
        assert FakeTaskQueue.instances[-1].tasks == []


def test_render_endpoint_accepts_backup_only_bgm_category_random_selection(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    backup_dir = tmp_path / "runtime" / "bgm-backup"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    (backup_dir / "legacy").mkdir(parents=True)
    (backup_dir / "legacy" / "old.mp3").write_text("music", encoding="utf-8")
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir, bgm_backup_dir=backup_dir)

    with TestClient(api_app_module.create_app()) as client:
        client.app.state.store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "legacy"}},
            },
        )

        assert response.status_code == 200
        assert FakeTaskQueue.instances[-1].tasks[-1].payload["overrides"]["bgm"] == {"category": "legacy"}


def test_render_endpoint_rejects_unsafe_bgm_category_before_enqueue(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        client.app.state.store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "../outside"}},
            },
        )

        assert response.status_code == 400
        assert response.json()["error_code"] == 2007
        assert response.json()["details"]["reason"] == "invalid_category"
        assert FakeTaskQueue.instances[-1].tasks == []


def test_render_endpoint_rejects_invalid_user_audio_bgm_refs(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    _configure_api_env(tmp_path, monkeypatch, pipelines_root)

    with TestClient(api_app_module.create_app()) as client:
        store = client.app.state.store
        store.save_file("file1", "GouMei-Video-Cut/inputs/file1.mp4")
        store.save_file("video1", "GouMei-Video-Cut/inputs/video1.mp4")
        store.save_file(
            "audio1",
            "GouMei-Video-Cut/user-audio/audio1.mp3",
            kind="user_audio",
            size_bytes=123,
        )
        headers = {"X-Api-Key": "test-key", "Content-Type": "application/json"}

        missing = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"fileId": "missing"}},
            },
        )
        assert missing.status_code == 400
        assert missing.json() == {
            "error_code": 2008,
            "message": "BGM file reference not found.",
            "details": {"fileId": "missing"},
        }

        wrong_kind = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"fileId": "video1"}},
            },
        )
        assert wrong_kind.status_code == 400
        assert wrong_kind.json() == {
            "error_code": 2008,
            "message": "Invalid BGM file reference.",
            "details": {"fileId": "video1", "kind": "asset", "expected": "user_audio"},
        }

        mixed = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"fileId": "audio1", "volume": 0.3}},
            },
        )
        assert mixed.status_code == 400
        assert mixed.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {"field": "overrides.bgm.fileId", "conflicts": ["volume"]},
        }

        empty_file_id = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"fileId": ""}},
            },
        )
        assert empty_file_id.status_code == 400
        assert empty_file_id.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {"field": "overrides.bgm.fileId", "expected": "non-empty string"},
        }

        snake_case_file_id = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"file_id": "audio1"}},
            },
        )
        assert snake_case_file_id.status_code == 400
        assert snake_case_file_id.json()["error_code"] == 2007
        assert snake_case_file_id.json()["details"] == {
            "field": "overrides.bgm.file_id",
            "expected": "overrides.bgm.fileId",
            "unknown": ["file_id"],
        }

        invalid_bgm_shape = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": "audio1"},
            },
        )
        assert invalid_bgm_shape.status_code == 400
        assert invalid_bgm_shape.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {"field": "overrides.bgm", "expected": "object"},
        }

        unknown_bgm_field = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": "卡点", "tempo": 120}},
            },
        )
        assert unknown_bgm_field.status_code == 400
        assert unknown_bgm_field.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {"field": "overrides.bgm", "unknown": ["tempo"]},
        }

        wrong_bgm_field_type = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["file1"],
                "overrides": {"bgm": {"category": 123}},
            },
        )
        assert wrong_bgm_field_type.status_code == 400
        assert wrong_bgm_field_type.json() == {
            "error_code": 2007,
            "message": "Invalid BGM override.",
            "details": {"field": "overrides.bgm.category", "expected": "string"},
        }

        audio_as_clip = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["audio1"],
                "overrides": {},
            },
        )
        assert audio_as_clip.status_code == 400
        assert audio_as_clip.json()["error_code"] == 2002
        assert FakeTaskQueue.instances[-1].tasks == []


def test_cleanup_expired_user_audio_removes_local_oss_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    store = TaskStore(tmp_path / "tasks.db")
    oss = api_app_module.OssClient()
    oss_key = "GouMei-Video-Cut/user-audio/audio1.mp3"
    local_file = tmp_path / "oss" / oss_key
    local_file.parent.mkdir(parents=True)
    local_file.write_text("audio", encoding="utf-8")
    store.save_file(
        "audio1",
        oss_key,
        kind="user_audio",
        size_bytes=123,
        expires_at="2000-01-01T00:00:00+00:00",
    )

    assert api_app_module._cleanup_expired_uploads(store, oss) == 1
    assert store.get_file("audio1") is None
    assert not local_file.exists()
    store.close()


def test_bgm_endpoint_requires_auth(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "input" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        response = client.get("/bgm")
        assert response.status_code == 401
        assert response.json()["error_code"] == 1001


def test_bgm_endpoint_returns_catalog_from_runtime_bgm_dir(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    bgm_backup_dir = tmp_path / "runtime" / "bgm-backup"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    (bgm_dir / "intense").mkdir(parents=True)
    (bgm_dir / "calm").mkdir(parents=True)
    (bgm_backup_dir / "legacy").mkdir(parents=True)
    (bgm_dir / "intense" / "2.mp3").write_text("intense", encoding="utf-8")
    (bgm_dir / "calm" / "1.mp3").write_text("calm", encoding="utf-8")
    (bgm_dir / "calm" / "note.txt").write_text("ignored", encoding="utf-8")
    (bgm_backup_dir / "legacy" / "old.mp3").write_text("legacy", encoding="utf-8")
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir, bgm_backup_dir=bgm_backup_dir)

    with TestClient(api_app_module.create_app()) as client:
        response = client.get("/bgm", headers={"X-Api-Key": "test-key"})
        assert response.status_code == 200
        assert response.json() == {
            "bgmRoot": str(bgm_dir.resolve()),
            "categories": [
                {"name": "calm", "displayName": "舒缓", "count": 1},
                {"name": "intense", "displayName": "激烈", "count": 1},
            ],
            "files": [
                {
                    "category": "calm",
                    "displayName": "舒缓",
                    "filename": "1",
                    "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3",
                },
                {
                    "category": "intense",
                    "displayName": "激烈",
                    "filename": "2",
                    "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/intense/2.mp3",
                },
            ],
        }


def test_bgm_endpoint_returns_empty_catalog_for_empty_bgm_dir(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "runtime" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    bgm_dir.mkdir(parents=True)
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        response = client.get("/bgm", headers={"X-Api-Key": "test-key"})
        assert response.status_code == 200
        assert response.json() == {
            "bgmRoot": str(bgm_dir.resolve()),
            "categories": [],
            "files": [],
        }


def test_bgm_endpoint_returns_file_not_found_for_missing_bgm_dir(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    bgm_dir = tmp_path / "missing" / "bgm"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    _configure_api_env(tmp_path, monkeypatch, pipelines_root, bgm_dir=bgm_dir)

    with TestClient(api_app_module.create_app()) as client:
        response = client.get("/bgm", headers={"X-Api-Key": "test-key"})
        assert response.status_code == 404
        assert response.json() == {
            "error_code": 2006,
            "message": "BGM directory not found.",
            "details": {"bgmRoot": str(bgm_dir.resolve())},
        }


def test_validate_variables_required_missing() -> None:
    schema = {"clip_count": PipelineVariableDef(type="number", required=True)}
    with pytest.raises(VideoCutError, match="required"):
        validate_variables(schema, {})


def test_validate_variables_number_out_of_range() -> None:
    schema = {"trim_start": PipelineVariableDef(type="number", min=0.0, max=30.0)}
    with pytest.raises(VideoCutError, match="trim_start"):
        validate_variables(schema, {"trim_start": 99})


def test_validate_variables_select_invalid_option() -> None:
    schema = {"trans": PipelineVariableDef(type="select", options=["dissolve", "cut"])}
    with pytest.raises(VideoCutError, match="trans"):
        validate_variables(schema, {"trans": "zoom-dissolve"})


def test_validate_variables_boolean_type_error() -> None:
    schema = {"show_logo": PipelineVariableDef(type="boolean")}
    with pytest.raises(VideoCutError, match="show_logo"):
        validate_variables(schema, {"show_logo": "yes"})


def test_resolve_variable_values_fills_defaults_and_normalises_bool() -> None:
    schema = {
        "trim_start": PipelineVariableDef(type="number", default=2.0),
        "enabled": PipelineVariableDef(type="boolean", default=False),
    }
    result = resolve_variable_values(schema, {"trim_start": 5})
    assert result["trim_start"] == 5
    assert result["enabled"] is False


def test_zoom_dissolve_pipeline_config_parses() -> None:
    payload = {
        "name": "zoom-dissolve-concat",
        "mode": "pipeline",
        "preset": "auto",
        "quality": "high",
        "clips": [{"trim_start": 0, "trim_end": 0}] * 3,
        "default_transition": {"type": "zoom-dissolve", "duration": 0.4, "scale": 1.18},
        "variables": {
            "zoom_scale": {"type": "number", "default": 1.18, "min": 1.05, "max": 2.0},
        },
        "overridable": ["zoom_scale"],
    }
    from pathlib import Path
    config = parse_pipeline_config(payload, Path("/tmp/config.json"), require_name=True)
    assert config.default_transition is not None
    assert config.default_transition.type == "zoom-dissolve"
    assert config.default_transition.scale == pytest.approx(1.18)
    assert config.variables is not None
    assert "zoom_scale" in config.variables


def test_bgm_concat_pipeline_config_parses() -> None:
    config_path = Path(__file__).resolve().parents[1] / "pipelines" / "bgm-concat" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = parse_pipeline_config(payload, config_path, require_name=True)

    assert config.name == "bgm-concat"
    assert len(config.clips) == 1
    assert config.clips[0].trim_start == 0
    assert config.clips[0].trim_end == 0
    assert config.default_transition is not None
    assert config.default_transition.type == "cut"
    assert config.default_transition.duration == 0
    assert config.bgm is not None
    assert config.bgm.enabled is True
    assert config.bgm.dir == "input/bgm"


def test_bgm_category_override_clears_existing_filename_and_preserves_options(tmp_path) -> None:
    payload = _make_pipeline_payload()
    payload["bgm"] = {
        "enabled": True,
        "dir": "input/bgm",
        "category": "激烈",
        "filename": "2",
        "volume": 0.45,
        "fade_out": 1.5,
    }
    config = parse_pipeline_config(payload, tmp_path / "config.json", require_name=True)
    ctx = build_pipeline_context(
        config,
        ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"],
        tmp_path / "config.json",
        {"bgm": {"category": "舒缓"}},
    )

    assert ctx.config.bgm is not None
    assert ctx.config.bgm.category == "舒缓"
    assert ctx.config.bgm.filename is None
    assert ctx.config.bgm.enabled is True
    assert ctx.config.bgm.dir == "input/bgm"
    assert ctx.config.bgm.volume == pytest.approx(0.45)
    assert ctx.config.bgm.fade_out == pytest.approx(1.5)


def test_bgm_category_filename_override_preserves_manifest_shape(tmp_path) -> None:
    payload = _make_pipeline_payload()
    payload["bgm"] = {
        "enabled": True,
        "dir": "input/bgm",
        "volume": 0.45,
        "fade_out": 1.5,
    }
    config = parse_pipeline_config(payload, tmp_path / "config.json", require_name=True)
    ctx = build_pipeline_context(
        config,
        ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"],
        tmp_path / "config.json",
        {"bgm": {"category": "舒缓", "filename": "1"}},
    )

    assert ctx.config.bgm is not None
    assert ctx.config.bgm.category == "舒缓"
    assert ctx.config.bgm.filename == "1"
    assert ctx.config.bgm.enabled is True
    assert ctx.config.bgm.dir == "input/bgm"
    assert ctx.config.bgm.volume == pytest.approx(0.45)
    assert ctx.config.bgm.fade_out == pytest.approx(1.5)


def test_bgm_file_override_is_rejected(tmp_path) -> None:
    config = parse_pipeline_config(_make_pipeline_payload(), tmp_path / "config.json", require_name=True)

    with pytest.raises(VideoCutError, match="bgm.file is not supported"):
        build_pipeline_context(
            config,
            ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.mp4"],
            tmp_path / "config.json",
            {"bgm": {"file": "舒缓/1.mp3"}},
        )


def test_pipeline_render_rejects_local_paths(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["D:/tmp/local.mp4"],
            },
        )
        assert response.status_code == 400
        assert response.json() == {
            "error_code": 2002,
            "message": "Invalid clip reference.",
            "details": {"value": "D:/tmp/local.mp4"},
        }


def test_api_error_codes_for_auth_pipeline_task_and_download(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        missing_route = client.get("/not-a-route", headers={"X-Api-Key": "test-key"})
        assert missing_route.status_code == 404
        assert missing_route.json() == {
            "error_code": 1004,
            "message": "Not Found.",
            "details": {"path": "/not-a-route"},
        }

        unauthorized = client.get("/tasks/t_missing")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error_code"] == 1001

        headers = {"X-Api-Key": "test-key", "Content-Type": "application/json"}
        missing_pipeline = client.post(
            "/render",
            headers=headers,
            json={
                "pipeline": "not-exists",
                "clips": ["GouMei-Video-Cut/inputs/file1.mp4"],
            },
        )
        assert missing_pipeline.status_code == 400
        assert missing_pipeline.json() == {
            "error_code": 2003,
            "message": 'Pipeline "not-exists" is not registered.',
            "details": {"available": ["trim-mixed-dissolve-v1"]},
        }

        missing_task = client.get("/tasks/t_missing", headers={"X-Api-Key": "test-key"})
        assert missing_task.status_code == 404
        assert missing_task.json()["error_code"] == 2004

        not_ready_task_id = "t_not_ready"
        client.app.state.store.create(
            api_app_module._create_store_record(
                not_ready_task_id,
                "pipeline",
                "trim-mixed-dissolve-v1",
                {"clips": ["GouMei-Video-Cut/inputs/file1.mp4"]},
            )
        )
        not_ready = client.get(f"/tasks/{not_ready_task_id}/download", headers={"X-Api-Key": "test-key"})
        assert not_ready.status_code == 404
        assert not_ready.json() == {
            "error_code": 3001,
            "message": "Task output is not ready.",
            "details": {},
        }


def test_completed_task_routes_return_public_output_url(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.delenv("OSS_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    monkeypatch.setenv("OSS_PUBLIC_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_BUCKET", "goumee-coze")
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        task_id = "t_public_url"
        store = client.app.state.store
        store.create(
            api_app_module._create_store_record(
                task_id,
                "pipeline",
                "trim-mixed-dissolve-v1",
                {"clips": ["GouMei-Video-Cut/inputs/file1.mp4"]},
            )
        )
        store.mark_rendering(task_id)
        store.mark_completed(
            task_id,
            "GouMei-Video-Cut/outputs/20260606/20260606_104858/t_public_url/final video 中文.mp4",
        )
        expected_url = (
            "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/"
            "GouMei-Video-Cut/outputs/20260606/20260606_104858/t_public_url/"
            "final%20video%20%E4%B8%AD%E6%96%87.mp4"
        )

        task_response = client.get(f"/tasks/{task_id}", headers={"X-Api-Key": "test-key"})
        assert task_response.status_code == 200
        assert task_response.json()["outputUrl"] == expected_url
        assert "%2F" not in task_response.json()["outputUrl"]

        download_response = client.get(
            f"/tasks/{task_id}/download",
            headers={"X-Api-Key": "test-key"},
            follow_redirects=False,
        )
        assert download_response.status_code == 302
        assert download_response.headers["location"] == expected_url


def test_completed_task_routes_reject_internal_public_output_url(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.delenv("OSS_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    monkeypatch.setenv("OSS_PUBLIC_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    monkeypatch.setenv("OSS_BUCKET", "goumee-coze")
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        task_id = "t_bad_output_url"
        store = client.app.state.store
        store.create(
            api_app_module._create_store_record(
                task_id,
                "pipeline",
                "trim-mixed-dissolve-v1",
                {"clips": ["GouMei-Video-Cut/inputs/file1.mp4"]},
            )
        )
        store.mark_rendering(task_id)
        store.mark_completed(task_id, "GouMei-Video-Cut/outputs/20260606/20260606_104858/t_bad_output_url/final.mp4")

        task_response = client.get(f"/tasks/{task_id}", headers={"X-Api-Key": "test-key"})
        assert task_response.status_code == 500
        assert task_response.json()["error_code"] == 3004
        assert task_response.json()["details"]["reason"] == "internal_endpoint"
        assert task_response.json()["details"]["outputUrlHost"] == "goumee-coze.oss-cn-hangzhou-internal.aliyuncs.com"

        download_response = client.get(
            f"/tasks/{task_id}/download",
            headers={"X-Api-Key": "test-key"},
            follow_redirects=False,
        )
        assert download_response.status_code == 500
        assert download_response.json()["error_code"] == 3004
        assert download_response.json()["details"]["reason"] == "internal_endpoint"


def test_task_summary_and_active_routes_return_overall_status(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "bgm-concat", _make_pipeline_payload(name="bgm-concat"))
    _configure_api_env(tmp_path, monkeypatch, pipelines_root)

    with TestClient(api_app_module.create_app()) as client:
        assert client.get("/tasks/summary").status_code == 401
        assert client.get("/tasks/active").status_code == 401

        store = client.app.state.store
        store.create(_make_api_task("t_completed", "completed", "2026-05-20T01:00:00+00:00"))
        store.create(_make_api_task("t_failed", "failed", "2026-05-20T01:10:00+00:00"))
        store.create(
            _make_api_task(
                "t_rendering",
                "rendering",
                "2026-05-20T02:20:00+00:00",
                progress=45,
                attempt=1,
                started_at="2026-05-20T02:20:05+00:00",
                last_error="download timeout",
                last_error_at="2026-05-20T02:19:00+00:00",
            )
        )
        store.create(_make_api_task("t_pending", "pending", "2026-05-20T02:10:00+00:00"))

        summary = client.get("/tasks/summary", headers={"X-Api-Key": "test-key"})
        assert summary.status_code == 200
        summary_body = summary.json()
        assert "+00:00" not in summary_body["generatedAt"]
        assert summary_body["workers"] >= 1
        assert summary_body["uploadWorkers"] == 2
        assert summary_body["queueSize"] == 0
        assert summary_body["counts"] == {
            "total": 4,
            "pending": 1,
            "rendering": 1,
            "completed": 1,
            "failed": 1,
        }

        active = client.get("/tasks/active", headers={"X-Api-Key": "test-key"})
        assert active.status_code == 200
        active_body = active.json()
        assert "+00:00" not in active_body["generatedAt"]
        assert [item["taskId"] for item in active_body["tasks"]] == ["t_pending", "t_rendering"]
        assert active_body["tasks"][0]["createdAt"] == "2026-05-20T10:10:00.000000"
        assert active_body["tasks"][0]["startedAt"] is None
        assert active_body["tasks"][1] == {
            "taskId": "t_rendering",
            "status": "rendering",
            "progress": 45,
            "attempt": 1,
            "createdAt": "2026-05-20T10:20:00.000000",
            "startedAt": "2026-05-20T10:20:05.000000",
            "lastError": "download timeout",
            "lastErrorAt": "2026-05-20T10:19:00.000000",
            "taskKind": "pipeline",
            "sourceName": "bgm-concat",
        }

        task = client.get("/tasks/t_rendering", headers={"X-Api-Key": "test-key"})
        assert task.status_code == 200
        task_body = task.json()
        assert task_body["createdAt"] == "2026-05-20T10:20:00.000000"
        assert task_body["startedAt"] == "2026-05-20T10:20:05.000000"
        assert task_body["lastErrorAt"] == "2026-05-20T10:19:00.000000"


def test_render_queue_full_returns_error_code(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", RejectingTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        response = client.post(
            "/render",
            headers={"X-Api-Key": "test-key", "Content-Type": "application/json"},
            json={
                "pipeline": "trim-mixed-dissolve-v1",
                "clips": ["GouMei-Video-Cut/inputs/file1.mp4"],
            },
        )
        assert response.status_code == 503
        assert response.json() == {
            "error_code": 3002,
            "message": "Queue is full.",
            "details": {"queueSize": 200},
        }


def test_get_task_returns_failure_history(tmp_path, monkeypatch) -> None:
    FakeTaskQueue.instances.clear()
    pipelines_root = tmp_path / "pipelines"
    _write_pipeline_config(pipelines_root, "trim-mixed-dissolve-v1", _make_pipeline_payload())
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("OSS_LOCAL_ROOT", str(tmp_path / "oss"))
    monkeypatch.setenv("OSS_PREFIX", "GouMei-Video-Cut")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("PIPELINES_DIR", str(pipelines_root))
    monkeypatch.setattr(api_app_module, "TaskQueue", FakeTaskQueue)

    with TestClient(api_app_module.create_app()) as client:
        store = client.app.state.store
        task_id = "t_history"
        store.create(
            api_app_module._create_store_record(
                task_id,
                "pipeline",
                "trim-mixed-dissolve-v1",
                {"clips": ["GouMei-Video-Cut/inputs/file1.mp4"]},
            )
        )
        store.mark_rendering(task_id)
        store.record_failure(task_id, "download timeout")
        store.reset_to_queue(task_id)
        store.mark_rendering(task_id)
        store.mark_completed(task_id, "GouMei-Video-Cut/outputs/t_history/final.mp4")

        response = client.get(f"/tasks/{task_id}", headers={"X-Api-Key": "test-key"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["attempt"] == 2
        assert body["error"] is None
        assert body["lastError"] == "download timeout"
        assert body["lastErrorAt"] is not None
        assert body["failureHistory"] == [
            {
                "attempt": 1,
                "error": "download timeout",
                "createdAt": body["lastErrorAt"],
            }
        ]
