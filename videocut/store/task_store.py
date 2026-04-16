from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

TaskStatus = Literal["pending", "rendering", "completed", "failed"]


@dataclass(slots=True)
class TaskRecord:
    id: str
    template_id: str
    status: TaskStatus
    progress: int
    attempt: int
    variables: dict[str, Any]
    oss_key: str | None
    error: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


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
                  status TEXT NOT NULL DEFAULT 'pending',
                  progress INTEGER NOT NULL DEFAULT 0,
                  attempt INTEGER NOT NULL DEFAULT 0,
                  variables TEXT NOT NULL,
                  oss_key TEXT,
                  error TEXT,
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
                """
            )
            self._db.commit()

    def create(self, record: TaskRecord) -> TaskRecord:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO tasks (
                  id, template_id, status, progress, attempt, variables, oss_key, error,
                  created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.template_id,
                    record.status,
                    record.progress,
                    record.attempt,
                    json.dumps(record.variables, ensure_ascii=False),
                    record.oss_key,
                    record.error,
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
                "UPDATE tasks SET status='rendering', started_at=?, attempt=attempt+1 WHERE id=?",
                (datetime.utcnow().isoformat(), task_id),
            )
            self._db.commit()

    def update_progress(self, task_id: str, progress: int) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET progress=? WHERE id=?", (progress, task_id))
            self._db.commit()

    def mark_completed(self, task_id: str, oss_key: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='completed', progress=100, oss_key=?, completed_at=? WHERE id=?",
                (oss_key, datetime.utcnow().isoformat(), task_id),
            )
            self._db.commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                (error, datetime.utcnow().isoformat(), task_id),
            )
            self._db.commit()

    def reset_to_queue(self, task_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET status='pending', started_at=NULL WHERE id=?", (task_id,))
            self._db.commit()

    def get_pending_and_stalled(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'rendering') ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def cleanup_old_tasks(self, ttl_days: int) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=ttl_days)).isoformat()
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM tasks WHERE status IN ('completed','failed') AND created_at < ?",
                (cutoff,),
            )
            self._db.commit()
            return cursor.rowcount

    def save_file(self, file_id: str, oss_key: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO files (file_id, oss_key, created_at) VALUES (?, ?, ?)",
                (file_id, oss_key, datetime.utcnow().isoformat()),
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
        return TaskRecord(
            id=str(row["id"]),
            template_id=str(row["template_id"]),
            status=row["status"],
            progress=int(row["progress"]),
            attempt=int(row["attempt"]),
            variables=json.loads(row["variables"]),
            oss_key=row["oss_key"],
            error=row["error"],
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

