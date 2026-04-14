// src/queue/WorkerPool.ts
import { fork, ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';

export interface WorkerTask {
  taskId: string;
  templateId: string;
  variables: Record<string, unknown>;
  preset: string;
  quality: string;
}

export type IpcMessage =
  | { type: 'worker_ready' }
  | { type: 'lease_start'; taskId: string }
  | { type: 'task_done'; taskId: string; ossKey: string }
  | { type: 'task_failed'; taskId: string; error: string }
  | { type: 'progress'; taskId: string; progress: number };

type IpcHandler = (msg: IpcMessage, workerId: number) => void;

interface WorkerState {
  process: ChildProcess;
  id: number;
  currentTaskId: string | null;
  ready: boolean;
}

const WORKER_ENTRY = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  'worker-entry.ts',
);

export class WorkerPool {
  private workers: WorkerState[] = [];
  private count: number;
  private onMessage: IpcHandler;
  private onWorkerDead: (workerId: number, taskId: string | null) => void;
  private nextId = 0;

  constructor(
    count: number,
    onMessage: IpcHandler,
    onWorkerDead: (workerId: number, taskId: string | null) => void,
  ) {
    this.count = count || Math.max(1, Math.floor(os.cpus().length / 2));
    this.onMessage = onMessage;
    this.onWorkerDead = onWorkerDead;
  }

  start(): Promise<void> {
    const ready: Promise<void>[] = [];
    for (let i = 0; i < this.count; i++) {
      ready.push(this.spawnWorker());
    }
    return Promise.all(ready).then(() => {});
  }

  private spawnWorker(): Promise<void> {
    return new Promise((resolve) => {
      const id = this.nextId++;
      const proc = fork(WORKER_ENTRY, [], {
        execArgv: ['--import', 'tsx/esm'],
        env: { ...process.env },
      });

      const state: WorkerState = { process: proc, id, currentTaskId: null, ready: false };
      this.workers.push(state);

      proc.on('message', (msg: IpcMessage) => {
        if (msg.type === 'worker_ready') {
          state.ready = true;
          resolve();
          return;
        }
        if (msg.type === 'lease_start') state.currentTaskId = msg.taskId;
        if (msg.type === 'task_done' || msg.type === 'task_failed') state.currentTaskId = null;
        this.onMessage(msg, id);
      });

      proc.on('exit', () => {
        const idx = this.workers.indexOf(state);
        if (idx !== -1) this.workers.splice(idx, 1);
        this.onWorkerDead(id, state.currentTaskId);
        this.spawnWorker().catch(() => {});
      });
    });
  }

  dispatch(task: WorkerTask): boolean {
    const idle = this.workers.find((w) => w.ready && w.currentTaskId === null);
    if (!idle) return false;
    idle.currentTaskId = task.taskId;
    idle.process.send({ type: 'run_task', ...task });
    return true;
  }

  get idleCount(): number {
    return this.workers.filter((w) => w.ready && w.currentTaskId === null).length;
  }

  get size(): number {
    return this.workers.length;
  }
}
