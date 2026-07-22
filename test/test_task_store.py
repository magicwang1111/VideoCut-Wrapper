from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from videocut.store import TaskRecord, TaskStore
from videocut.time_utils import to_beijing_iso


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


def test_task_store_saves_file_metadata_and_cleans_expired_records(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    now = datetime.now(UTC)
    expired_at = (now - timedelta(seconds=1)).isoformat()
    future_at = (now + timedelta(days=1)).isoformat()

    store.save_file(
        "audio1",
        "GouMei-Video-Cut/user-audio/audio1.mp3",
        kind="user_audio",
        size_bytes=123,
        expires_at=expired_at,
    )
    store.save_file(
        "audio2",
        "GouMei-Video-Cut/user-audio/audio2.mp3",
        kind="user_audio",
        size_bytes=456,
        expires_at=future_at,
    )

    record = store.get_file("audio1")
    assert record is not None
    assert record.kind == "user_audio"
    assert record.size_bytes == 123
    assert record.expires_at == to_beijing_iso(expired_at)

    cleaned = store.cleanup_expired_files(now)
    assert [item.file_id for item in cleaned] == ["audio1"]
    assert store.get_file("audio1") is None
    assert store.get_file("audio2") is not None
    store.close()


def test_task_store_writes_and_normalizes_beijing_timestamps(tmp_path) -> None:
    db_path = tmp_path / "tasks.db"
    store = TaskStore(db_path)
    store.create(
        TaskRecord(
            id="t_utc",
            task_kind="pipeline",
            source_name="bgm-concat",
            status="completed",
            progress=100,
            attempt=1,
            payload={"clips": ["demo.mp4"]},
            oss_key=None,
            error=None,
            last_error="download timeout",
            last_error_at="2026-05-28T03:00:01+00:00",
            created_at="2026-05-28T03:00:00+00:00",
            started_at="2026-05-28T03:00:02+00:00",
            completed_at="2026-05-28T03:00:03+00:00",
        )
    )
    store.record_failure("t_utc", "download timeout")
    store.save_file(
        "audio_utc",
        "GouMei-Video-Cut/user-audio/audio_utc.mp3",
        kind="user_audio",
        expires_at="2026-05-28T03:00:04+00:00",
    )
    store.close()

    raw = sqlite3.connect(db_path)
    task_row = raw.execute(
        "SELECT created_at, started_at, completed_at, last_error_at FROM tasks WHERE id='t_utc'"
    ).fetchone()
    file_row = raw.execute("SELECT expires_at, created_at FROM files WHERE file_id='audio_utc'").fetchone()
    failure_row = raw.execute("SELECT created_at FROM task_failures WHERE task_id='t_utc'").fetchone()
    raw.close()

    assert task_row[:3] == (
        "2026-05-28T11:00:00+08:00",
        "2026-05-28T11:00:02+08:00",
        "2026-05-28T11:00:03+08:00",
    )
    assert task_row[3].endswith("+08:00")
    assert file_row[0] == "2026-05-28T11:00:04+08:00"
    assert file_row[1].endswith("+08:00")
    assert failure_row[0].endswith("+08:00")


def test_task_store_migrates_existing_utc_timestamps_to_beijing(tmp_path) -> None:
    db_path = tmp_path / "tasks.db"
    store = TaskStore(db_path)
    store.create(
        TaskRecord(
            id="t_existing",
            task_kind="pipeline",
            source_name="bgm-concat",
            status="completed",
            progress=100,
            attempt=1,
            payload={"clips": ["demo.mp4"]},
            oss_key=None,
            error=None,
            last_error=None,
            last_error_at=None,
            created_at="2026-05-28T03:00:00+00:00",
            started_at=None,
            completed_at=None,
        )
    )
    store.close()

    raw = sqlite3.connect(db_path)
    raw.execute("UPDATE tasks SET created_at='2026-05-28T03:00:00+00:00' WHERE id='t_existing'")
    raw.commit()
    raw.close()

    store = TaskStore(db_path)
    task = store.get("t_existing")
    assert task is not None
    assert task.created_at == "2026-05-28T11:00:00+08:00"
    store.close()


def test_task_store_backfills_legacy_mps_jobs_idempotently(tmp_path, caplog) -> None:
    db_path = tmp_path / "tasks.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE tasks (
          id TEXT PRIMARY KEY, template_id TEXT NOT NULL, task_kind TEXT NOT NULL DEFAULT 'template',
          status TEXT NOT NULL DEFAULT 'pending', progress INTEGER NOT NULL DEFAULT 0,
          attempt INTEGER NOT NULL DEFAULT 0, variables TEXT NOT NULL, oss_key TEXT, error TEXT,
          last_error TEXT, last_error_at TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
        );
        """
    )
    rows = [
        ("t_mps", '{"subtitle_state":{"mps_task_id":"mps-legacy"}}'),
        ("t_duplicate", '{"subtitle_state":{"mps_task_id":"mps-legacy"}}'),
        ("t_plain", '{"clips":["demo.mp4"]}'),
        ("t_invalid", "{not-json"),
    ]
    raw.executemany(
        "INSERT INTO tasks (id, template_id, task_kind, variables, created_at) VALUES (?, 'subtitle-burn', 'pipeline', ?, '2026-07-22T10:00:00+08:00')",
        rows,
    )
    raw.commit()
    raw.close()

    store = TaskStore(db_path)
    jobs = store.list_external_jobs("t_mps")
    assert len(jobs) == 1
    assert jobs[0].external_task_id == "mps-legacy"
    assert jobs[0].status == "unknown"
    assert jobs[0].submitted_attempt is None
    assert jobs[0].submitted_at is None
    assert store.list_external_jobs("t_duplicate") == []
    assert "invalid variables JSON" in caplog.text
    store.close()

    store = TaskStore(db_path)
    assert len(store.list_external_jobs("t_mps")) == 1
    raw = sqlite3.connect(db_path)
    assert raw.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 4
    assert raw.execute("SELECT COUNT(*) FROM task_external_jobs").fetchone()[0] == 1
    raw.close()
    store.close()


def test_task_store_upserts_external_job_and_payload_atomically(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_external"))
    store.mark_rendering("t_external")
    store.upsert_external_job(
        "t_external",
        provider="tencent_mps",
        job_kind="smart_subtitles",
        external_task_id="mps-1",
        submitted_attempt=1,
        status="submitted",
        submitted_at="2026-07-22T10:00:00+08:00",
        payload_updates={"subtitle_state": {"mps_task_id": "mps-1"}},
    )
    store.upsert_external_job(
        "t_external",
        provider="tencent_mps",
        job_kind="smart_subtitles",
        external_task_id="mps-1",
        status="processing",
        provider_status="PROCESSING",
        last_polled_at="2026-07-22T10:00:05+08:00",
    )
    store.upsert_external_job(
        "t_external",
        provider="tencent_mps",
        job_kind="smart_subtitles",
        external_task_id="mps-1",
        status="failed",
        provider_status="FAIL",
        error_code="60000",
        error_code_ext="302",
        message="Server returned 5XX Server Error reply",
        last_polled_at="2026-07-22T10:00:10+08:00",
        completed_at="2026-07-22T10:00:10+08:00",
    )

    task = store.get("t_external")
    assert task is not None
    assert task.payload["subtitle_state"]["mps_task_id"] == "mps-1"
    jobs = store.list_external_jobs("t_external")
    assert len(jobs) == 1
    assert jobs[0].submitted_attempt == 1
    assert jobs[0].status == "failed"
    assert jobs[0].provider_status == "FAIL"
    assert jobs[0].error_code == "60000"
    assert jobs[0].error_code_ext == "302"
    assert jobs[0].message == "Server returned 5XX Server Error reply"
    assert jobs[0].submitted_at == "2026-07-22T10:00:00+08:00"
    assert jobs[0].completed_at == "2026-07-22T10:00:10+08:00"
    store.close()


def test_task_store_cleanup_removes_external_jobs(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    store.create(_make_task("t_old"))
    store.upsert_external_job(
        "t_old", provider="tencent_mps", job_kind="smart_subtitles",
        external_task_id="mps-old", status="succeeded",
    )
    raw = sqlite3.connect(tmp_path / "tasks.db")
    raw.execute(
        "UPDATE tasks SET status='completed', created_at='2020-01-01T00:00:00+08:00' WHERE id='t_old'"
    )
    raw.commit()
    raw.close()

    assert store.cleanup_old_tasks(1) == 1
    assert store.list_external_jobs("t_old") == []
    store.close()
