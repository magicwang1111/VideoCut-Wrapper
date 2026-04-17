from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

TaskKind = Literal["template", "pipeline"]
TaskStatus = Literal["pending", "rendering", "completed", "failed"]


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
class PipelineRecord:
    name: str
    source_path: str
    config: dict[str, Any]
    updated_at: str


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
            self._db.commit()

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
                    record.last_error_at,
                    record.created_at,
                    record.started_at,
                    record.completed_at,
                ),
            )
            self._db.commit()
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def mark_rendering(self, task_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='rendering', progress=0, started_at=?, completed_at=NULL, error=NULL, attempt=attempt+1 WHERE id=?",
                (datetime.now(UTC).isoformat(), task_id),
            )
            self._db.commit()

    def update_progress(self, task_id: str, progress: int) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET progress=? WHERE id=?", (progress, task_id))
            self._db.commit()

    def mark_completed(self, task_id: str, oss_key: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='completed', progress=100, oss_key=?, error=NULL, completed_at=? WHERE id=?",
                (oss_key, datetime.now(UTC).isoformat(), task_id),
            )
            self._db.commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        self.record_failure(task_id, error)
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, datetime.now(UTC).isoformat(), task_id),
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

    def cleanup_old_tasks(self, ttl_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
        with self._lock:
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
        created_at = datetime.now(UTC).isoformat()
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
                            item.updated_at,
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

    def save_file(self, file_id: str, oss_key: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO files (file_id, oss_key, created_at) VALUES (?, ?, ?)",
                (file_id, oss_key, datetime.now(UTC).isoformat()),
            )
            self._db.commit()

    def get_oss_key(self, file_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT oss_key FROM files WHERE file_id=?", (file_id,)).fetchone()
        return str(row["oss_key"]) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

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
