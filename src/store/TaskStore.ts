import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';

export type TaskStatus = 'pending' | 'rendering' | 'completed' | 'failed';

export interface TaskRow {
  id: string;
  template_id: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  variables: string;
  oss_key: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskRecord {
  id: string;
  templateId: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  variables: Record<string, unknown>;
  ossKey: string | null;
  error: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

function rowToRecord(row: TaskRow): TaskRecord {
  return {
    id: row.id,
    templateId: row.template_id,
    status: row.status,
    progress: row.progress,
    attempt: row.attempt,
    variables: JSON.parse(row.variables),
    ossKey: row.oss_key,
    error: row.error,
    createdAt: row.created_at,
    startedAt: row.started_at,
    completedAt: row.completed_at,
  };
}

export class TaskStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 5000');
    this.migrate();
  }

  private migrate(): void {
    this.db.exec(`
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
    `);
  }

  create(record: Omit<TaskRecord, 'progress' | 'attempt' | 'ossKey' | 'error' | 'startedAt' | 'completedAt'>): TaskRecord {
    const full: TaskRecord = {
      ...record,
      progress: 0,
      attempt: 0,
      ossKey: null,
      error: null,
      startedAt: null,
      completedAt: null,
    };
    this.db.prepare(`
      INSERT INTO tasks (id, template_id, status, progress, attempt, variables, oss_key, error, created_at, started_at, completed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(full.id, full.templateId, full.status, full.progress, full.attempt,
           JSON.stringify(full.variables), full.ossKey, full.error,
           full.createdAt, full.startedAt, full.completedAt);
    return full;
  }

  get(id: string): TaskRecord | null {
    const row = this.db.prepare('SELECT * FROM tasks WHERE id = ?').get(id) as TaskRow | undefined;
    return row ? rowToRecord(row) : null;
  }

  markRendering(id: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='rendering', started_at=?, attempt=attempt+1 WHERE id=?
    `).run(new Date().toISOString(), id);
  }

  updateProgress(id: string, progress: number): void {
    this.db.prepare('UPDATE tasks SET progress=? WHERE id=?').run(progress, id);
  }

  markCompleted(id: string, ossKey: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='completed', progress=100, oss_key=?, completed_at=? WHERE id=?
    `).run(ossKey, new Date().toISOString(), id);
  }

  markFailed(id: string, error: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?
    `).run(error, new Date().toISOString(), id);
  }

  resetToQueue(id: string): void {
    this.db.prepare(`UPDATE tasks SET status='pending', started_at=NULL WHERE id=?`).run(id);
  }

  getPendingAndStalled(): TaskRecord[] {
    const rows = this.db.prepare(
      "SELECT * FROM tasks WHERE status IN ('pending', 'rendering') ORDER BY created_at ASC"
    ).all() as TaskRow[];
    return rows.map(rowToRecord);
  }

  cleanupOldTasks(ttlDays: number): number {
    const cutoff = new Date(Date.now() - ttlDays * 86400_000).toISOString();
    const result = this.db.prepare(
      "DELETE FROM tasks WHERE status IN ('completed','failed') AND created_at < ?"
    ).run(cutoff);
    return result.changes;
  }

  close(): void {
    this.db.close();
  }
}
