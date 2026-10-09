from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from videocut.time_utils import now_beijing, now_beijing_iso, to_beijing_iso

FileKind = Literal["asset", "user_audio"]
TaskKind = Literal["template", "pipeline"]
TaskStatus = Literal["pending", "rendering", "completed", "failed"]
TASK_STATUSES: tuple[TaskStatus, ...] = ("pending", "rendering", "completed", "failed")
ExternalJobStatus = Literal["unknown", "submitted", "processing", "succeeded", "failed"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskRecord:
    id: str
    task_kind: TaskKind
    source_name: str
    status: TaskStatus
    progress: int
    attempt: int
    payload: dict[str, Any]
    oss_key: str | None
    error: str | None
    last_error: str | None
    last_error_at: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


@dataclass(slots=True)
class TaskFailureRecord:
    task_id: str
    attempt: int
    error: str
    created_at: str


@dataclass(slots=True)
class ExternalJobRecord:
    task_id: str
    provider: str
    job_kind: str
    external_task_id: str
    submitted_attempt: int | None
    status: ExternalJobStatus
    provider_status: str | None
    error_code: str | None
    error_code_ext: str | None
    message: str | None
    submitted_at: str | None
    last_polled_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class PipelineRecord:
    name: str
    source_path: str
    config: dict[str, Any]
    updated_at: str


@dataclass(slots=True)
class FileRecord:
    file_id: str
    oss_key: str
    kind: FileKind
    size_bytes: int | None
    created_at: str
    expires_at: str | None


class TaskStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,
                  template_id TEXT NOT NULL,
                  task_kind TEXT NOT NULL DEFAULT 'template',
                  status TEXT NOT NULL DEFAULT 'pending',
                  progress INTEGER NOT NULL DEFAULT 0,
                  attempt INTEGER NOT NULL DEFAULT 0,
                  variables TEXT NOT NULL,
                  oss_key TEXT,
                  error TEXT,
                  last_error TEXT,
                  last_error_at TEXT,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE TABLE IF NOT EXISTS files (
                  file_id TEXT PRIMARY KEY,
                  oss_key TEXT NOT NULL,
                  kind TEXT NOT NULL DEFAULT 'asset',
                  size_bytes INTEGER,
                  expires_at TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pipelines (
                  name TEXT PRIMARY KEY,
                  source_path TEXT NOT NULL,
                  config_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_failures (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT NOT NULL,
                  attempt INTEGER NOT NULL,
                  error TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_failures_task_created
                ON task_failures(task_id, created_at);
                CREATE TABLE IF NOT EXISTS task_external_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  job_kind TEXT NOT NULL,
                  external_task_id TEXT NOT NULL,
                  submitted_attempt INTEGER,
                  status TEXT NOT NULL DEFAULT 'unknown',
                  provider_status TEXT,
                  error_code TEXT,
                  error_code_ext TEXT,
                  message TEXT,
                  submitted_at TEXT,
                  last_polled_at TEXT,
                  completed_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(provider, external_task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_external_jobs_task
                ON task_external_jobs(task_id);
                CREATE INDEX IF NOT EXISTS idx_external_jobs_status
                ON task_external_jobs(provider, status);
                """
            )
            columns = {
                str(row["name"])
                for row in self._db.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "task_kind" not in columns:
                self._db.execute("ALTER TABLE tasks ADD COLUMN task_kind TEXT NOT NULL DEFAULT 'template'")
            if "last_error" not in columns:
                self._db.execute("ALTER TABLE tasks ADD COLUMN last_error TEXT")
            if "last_error_at" not in columns:
                self._db.execute("ALTER TABLE tasks ADD COLUMN last_error_at TEXT")
            file_columns = {
                str(row["name"])
                for row in self._db.execute("PRAGMA table_info(files)").fetchall()
            }
            if "kind" not in file_columns:
                self._db.execute("ALTER TABLE files ADD COLUMN kind TEXT NOT NULL DEFAULT 'asset'")
            if "size_bytes" not in file_columns:
                self._db.execute("ALTER TABLE files ADD COLUMN size_bytes INTEGER")
            if "expires_at" not in file_columns:
                self._db.execute("ALTER TABLE files ADD COLUMN expires_at TEXT")
            self._backfill_external_jobs()
            self._normalize_existing_timestamps()
            self._db.commit()

    def _backfill_external_jobs(self) -> None:
        recorded_at = now_beijing_iso()
        rows = self._db.execute("SELECT id, variables FROM tasks").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["variables"] or "{}")
            except (TypeError, json.JSONDecodeError):
                logger.warning("Skipping external job backfill for task %s: invalid variables JSON.", row["id"])
                continue
            if not isinstance(payload, dict):
                continue
            state = payload.get("subtitle_state")
            if not isinstance(state, dict):
                continue
            external_task_id = str(state.get("mps_task_id") or "").strip()
            if not external_task_id:
                continue
            self._db.execute(
                """
                INSERT OR IGNORE INTO task_external_jobs (
                  task_id, provider, job_kind, external_task_id, submitted_attempt,
                  status, provider_status, error_code, error_code_ext, message,
                  submitted_at, last_polled_at, completed_at, created_at, updated_at
                ) VALUES (?, 'tencent_mps', 'smart_subtitles', ?, NULL,
                          'unknown', NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (str(row["id"]), external_task_id, recorded_at, recorded_at),
            )

    def _normalize_existing_timestamps(self) -> None:
        timestamp_columns = {
            "tasks": ("last_error_at", "created_at", "started_at", "completed_at"),
            "task_failures": ("created_at",),
            "task_external_jobs": (
                "submitted_at", "last_polled_at", "completed_at", "created_at", "updated_at"
            ),
            "files": ("expires_at", "created_at"),
            "pipelines": ("updated_at",),
        }
        for table, columns in timestamp_columns.items():
            selected_columns = ", ".join(columns)
            rows = self._db.execute(f"SELECT rowid, {selected_columns} FROM {table}").fetchall()
            for row in rows:
                updates: list[str] = []
                values: list[Any] = []
                for column in columns:
                    original = row[column]
                    normalized = to_beijing_iso(original)
                    if normalized != original:
                        updates.append(f"{column}=?")
                        values.append(normalized)
                if updates:
                    values.append(row["rowid"])
                    self._db.execute(
                        f"UPDATE {table} SET {', '.join(updates)} WHERE rowid=?",
                        values,
                    )

    def create(self, record: TaskRecord) -> TaskRecord:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO tasks (
                  id, template_id, task_kind, status, progress, attempt, variables, oss_key, error,
                  last_error, last_error_at, created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.source_name,
                    record.task_kind,
                    record.status,
                    record.progress,
                    record.attempt,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.oss_key,
                    record.error,
                    record.last_error,
                    to_beijing_iso(record.last_error_at),
                    to_beijing_iso(record.created_at),
                    to_beijing_iso(record.started_at),
                    to_beijing_iso(record.completed_at),
                ),
            )
            self._db.commit()
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def mark_rendering(self, task_id: str) -> int:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='rendering', progress=0, started_at=?, completed_at=NULL, error=NULL, attempt=attempt+1 WHERE id=?",
                (now_beijing_iso(), task_id),
            )
            row = self._db.execute("SELECT attempt FROM tasks WHERE id = ?", (task_id,)).fetchone()
            self._db.commit()
        return int(row["attempt"]) if row is not None else 0

    def update_progress(self, task_id: str, progress: int) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET progress=? WHERE id=?", (progress, task_id))
            self._db.commit()

    def patch_payload(self, task_id: str, updates: dict[str, Any]) -> None:
        with self._lock:
            row = self._db.execute("SELECT variables FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return
            payload = json.loads(row["variables"] or "{}")
            payload.update(updates)
            self._db.execute(
                "UPDATE tasks SET variables=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), task_id),
            )
            self._db.commit()

    def upsert_external_job(
        self,
        task_id: str,
        *,
        provider: str,
        job_kind: str,
        external_task_id: str,
        status: ExternalJobStatus,
        provider_status: str | None = None,
        submitted_attempt: int | None = None,
        error_code: str | None = None,
        error_code_ext: str | None = None,
        message: str | None = None,
        submitted_at: str | None = None,
        last_polled_at: str | None = None,
        completed_at: str | None = None,
        payload_updates: dict[str, Any] | None = None,
    ) -> None:
        now = now_beijing_iso()
        with self._lock:
            task_row = self._db.execute("SELECT variables FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task_row is None:
                return
            if payload_updates:
                payload = json.loads(task_row["variables"] or "{}")
                payload.update(payload_updates)
                self._db.execute(
                    "UPDATE tasks SET variables=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), task_id),
                )
            self._db.execute(
                """
                INSERT INTO task_external_jobs (
                  task_id, provider, job_kind, external_task_id, submitted_attempt,
                  status, provider_status, error_code, error_code_ext, message,
                  submitted_at, last_polled_at, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, external_task_id) DO UPDATE SET
                  status=excluded.status,
                  provider_status=excluded.provider_status,
                  submitted_attempt=COALESCE(task_external_jobs.submitted_attempt, excluded.submitted_attempt),
                  error_code=excluded.error_code,
                  error_code_ext=excluded.error_code_ext,
                  message=excluded.message,
                  submitted_at=COALESCE(task_external_jobs.submitted_at, excluded.submitted_at),
                  last_polled_at=excluded.last_polled_at,
                  completed_at=excluded.completed_at,
                  updated_at=excluded.updated_at
                WHERE task_external_jobs.task_id=excluded.task_id
                """,
                (
                    task_id, provider, job_kind, external_task_id, submitted_attempt,
                    status, provider_status, error_code, error_code_ext, message,
                    to_beijing_iso(submitted_at), to_beijing_iso(last_polled_at),
                    to_beijing_iso(completed_at), now, now,
                ),
            )
            self._db.commit()

    def list_external_jobs(self, task_id: str) -> list[ExternalJobRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM task_external_jobs WHERE task_id=? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_external_job(row) for row in rows]

    def mark_completed(self, task_id: str, oss_key: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='completed', progress=100, oss_key=?, error=NULL, completed_at=? WHERE id=?",
                (oss_key, now_beijing_iso(), task_id),
            )
            self._db.commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        self.record_failure(task_id, error)
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, now_beijing_iso(), task_id),
            )
            self._db.commit()

    def reset_to_queue(self, task_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='pending', progress=0, started_at=NULL, completed_at=NULL, error=NULL WHERE id=?",
                (task_id,),
            )
            self._db.commit()

    def get_pending_and_stalled(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'rendering') ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_tasks_by_status(self) -> dict[str, int]:
        counts = {"total": 0, **{status: 0 for status in TASK_STATUSES}}
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
        for row in rows:
            status = str(row["status"])
            if status in TASK_STATUSES:
                counts[status] = int(row["count"])
        counts["total"] = sum(counts[status] for status in TASK_STATUSES)
        return counts

    def list_active_tasks(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'rendering') ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def cleanup_old_tasks(self, ttl_days: int) -> int:
        cutoff = to_beijing_iso(now_beijing() - timedelta(days=ttl_days))
        with self._lock:
            self._db.execute(
                """
                DELETE FROM task_external_jobs
                WHERE task_id IN (
                  SELECT id FROM tasks WHERE status IN ('completed','failed') AND created_at < ?
                )
                """,
                (cutoff,),
            )
            self._db.execute(
                """
                DELETE FROM task_failures
                WHERE task_id IN (
                  SELECT id FROM tasks WHERE status IN ('completed','failed') AND created_at < ?
                )
                """,
                (cutoff,),
            )
            cursor = self._db.execute(
                "DELETE FROM tasks WHERE status IN ('completed','failed') AND created_at < ?",
                (cutoff,),
            )
            self._db.commit()
            return cursor.rowcount

    def record_failure(self, task_id: str, error: str) -> TaskFailureRecord | None:
        created_at = now_beijing_iso()
        with self._lock:
            row = self._db.execute("SELECT attempt FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            record = TaskFailureRecord(
                task_id=task_id,
                attempt=int(row["attempt"]),
                error=error,
                created_at=created_at,
            )
            self._db.execute(
                "INSERT INTO task_failures (task_id, attempt, error, created_at) VALUES (?, ?, ?, ?)",
                (record.task_id, record.attempt, record.error, record.created_at),
            )
            self._db.execute(
                "UPDATE tasks SET last_error=?, last_error_at=? WHERE id=?",
                (record.error, record.created_at, task_id),
            )
            self._db.commit()
        return record

    def list_failures(self, task_id: str) -> list[TaskFailureRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT task_id, attempt, error, created_at FROM task_failures WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [
            TaskFailureRecord(
                task_id=str(row["task_id"]),
                attempt=int(row["attempt"]),
                error=str(row["error"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def sync_pipelines(self, records: list[PipelineRecord]) -> None:
        with self._lock:
            if records:
                self._db.executemany(
                    """
                    INSERT INTO pipelines (name, source_path, config_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                      source_path = excluded.source_path,
                      config_json = excluded.config_json,
                      updated_at = excluded.updated_at
                    """,
                    [
                        (
                            item.name,
                            item.source_path,
                            json.dumps(item.config, ensure_ascii=False),
                            to_beijing_iso(item.updated_at),
                        )
                        for item in records
                    ],
                )
                placeholders = ", ".join("?" for _ in records)
                self._db.execute(
                    f"DELETE FROM pipelines WHERE name NOT IN ({placeholders})",
                    [item.name for item in records],
                )
            else:
                self._db.execute("DELETE FROM pipelines")
            self._db.commit()

    def get_pipeline(self, name: str) -> PipelineRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM pipelines WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return PipelineRecord(
            name=str(row["name"]),
            source_path=str(row["source_path"]),
            config=json.loads(row["config_json"]),
            updated_at=str(row["updated_at"]),
        )

    def list_pipelines(self) -> list[PipelineRecord]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM pipelines ORDER BY name ASC").fetchall()
        return [
            PipelineRecord(
                name=str(row["name"]),
                source_path=str(row["source_path"]),
                config=json.loads(row["config_json"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def save_file(
        self,
        file_id: str,
        oss_key: str,
        *,
        kind: FileKind = "asset",
        size_bytes: int | None = None,
        expires_at: str | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT OR REPLACE INTO files (file_id, oss_key, kind, size_bytes, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_id, oss_key, kind, size_bytes, to_beijing_iso(expires_at), now_beijing_iso()),
            )
            self._db.commit()

    def get_oss_key(self, file_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT oss_key FROM files WHERE file_id=?", (file_id,)).fetchone()
        return str(row["oss_key"]) if row else None

    def get_file(self, file_id: str) -> FileRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        return self._row_to_file_record(row) if row else None

    def cleanup_expired_files(self, now: datetime | None = None) -> list[FileRecord]:
        cutoff = to_beijing_iso(now) if now is not None else now_beijing_iso()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM files WHERE expires_at IS NOT NULL AND expires_at < ?",
                (cutoff,),
            ).fetchall()
            records = [self._row_to_file_record(row) for row in rows]
            if records:
                self._db.executemany(
                    "DELETE FROM files WHERE file_id=?",
                    [(record.file_id,) for record in records],
                )
                self._db.commit()
        return records

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_to_file_record(row: sqlite3.Row) -> FileRecord:
        kind_value = str(row["kind"]) if "kind" in row.keys() else "asset"
        size_value = row["size_bytes"] if "size_bytes" in row.keys() else None
        return FileRecord(
            file_id=str(row["file_id"]),
            oss_key=str(row["oss_key"]),
            kind=kind_value if kind_value in {"asset", "user_audio"} else "asset",  # type: ignore[arg-type]
            size_bytes=int(size_value) if size_value is not None else None,
            created_at=str(row["created_at"]),
            expires_at=row["expires_at"] if "expires_at" in row.keys() else None,
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TaskRecord:
        task_kind_value = str(row["task_kind"]) if "task_kind" in row.keys() else "template"
        return TaskRecord(
            id=str(row["id"]),
            task_kind=task_kind_value if task_kind_value in {"template", "pipeline"} else "template",
            source_name=str(row["template_id"]),
            status=row["status"],
            progress=int(row["progress"]),
            attempt=int(row["attempt"]),
            payload=json.loads(row["variables"]),
            oss_key=row["oss_key"],
            error=row["error"],
            last_error=row["last_error"] if "last_error" in row.keys() else None,
            last_error_at=row["last_error_at"] if "last_error_at" in row.keys() else None,
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _row_to_external_job(row: sqlite3.Row) -> ExternalJobRecord:
        status = str(row["status"])
        if status not in {"unknown", "submitted", "processing", "succeeded", "failed"}:
            status = "unknown"
        return ExternalJobRecord(
            task_id=str(row["task_id"]),
            provider=str(row["provider"]),
            job_kind=str(row["job_kind"]),
            external_task_id=str(row["external_task_id"]),
            submitted_attempt=int(row["submitted_attempt"]) if row["submitted_attempt"] is not None else None,
            status=status,  # type: ignore[arg-type]
            provider_status=row["provider_status"],
            error_code=row["error_code"],
            error_code_ext=row["error_code_ext"],
            message=row["message"],
            submitted_at=row["submitted_at"],
            last_polled_at=row["last_polled_at"],
            completed_at=row["completed_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
