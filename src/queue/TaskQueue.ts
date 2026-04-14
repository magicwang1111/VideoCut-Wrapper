// src/queue/TaskQueue.ts
import { TaskStore } from '../store/TaskStore.js';
import { WorkerPool, WorkerTask, IpcMessage } from './WorkerPool.js';
import { OssClient } from '../oss/OssClient.js';

const TASK_MAX_ATTEMPT = parseInt(process.env.TASK_MAX_ATTEMPT ?? '3', 10);
const QUEUE_MAX = parseInt(process.env.QUEUE_MAX ?? '200', 10);

export type TaskEventHandler = (event: {
  type: 'completed' | 'failed' | 'progress';
  taskId: string;
  ossKey?: string;
  progress?: number;
  error?: string;
}) => void;

export class TaskQueue {
  private queue: WorkerTask[] = [];
  private store: TaskStore;
  private pool: WorkerPool;
  private oss: OssClient;
  private onEvent: TaskEventHandler;
  private progressThrottle = new Map<string, number>();

  constructor(store: TaskStore, oss: OssClient, workerCount: number, onEvent: TaskEventHandler) {
    this.store = store;
    this.oss = oss;
    this.onEvent = onEvent;
    this.pool = new WorkerPool(
      workerCount,
      this.handleIpc.bind(this),
      this.handleWorkerDead.bind(this),
    );
  }

  async start(): Promise<void> {
    await this.pool.start();
    this.replayFromStore();
    this.drain();
  }

  private replayFromStore(): void {
    const stalled = this.store.getPendingAndStalled();
    for (const task of stalled) {
      if (task.status === 'rendering') this.store.resetToQueue(task.id);
      if (task.attempt >= TASK_MAX_ATTEMPT) {
        this.store.markFailed(task.id, '超过最大重试次数');
        continue;
      }
      this.queue.push({
        taskId: task.id,
        templateId: task.templateId,
        variables: task.variables,
        preset: (task.variables['_preset'] as string) ?? 'auto',
        quality: (task.variables['_quality'] as string) ?? 'high',
      });
    }
    if (stalled.length > 0) console.log(`[Queue] 重放 ${stalled.length} 个滞留任务`);
  }

  enqueue(task: WorkerTask): boolean {
    if (this.queue.length >= QUEUE_MAX) return false;
    this.queue.push(task);
    this.drain();
    return true;
  }

  get queueSize(): number {
    return this.queue.length;
  }

  private drain(): void {
    while (this.queue.length > 0 && this.pool.idleCount > 0) {
      const task = this.queue.shift()!;
      this.store.markRendering(task.taskId);
      this.pool.dispatch(task);
    }
  }

  private handleIpc(msg: IpcMessage): void {
    if (msg.type === 'task_done') {
      this.store.markCompleted(msg.taskId, msg.ossKey);
      this.onEvent({ type: 'completed', taskId: msg.taskId, ossKey: msg.ossKey });
      this.drain();
    } else if (msg.type === 'task_failed') {
      const record = this.store.get(msg.taskId);
      if (record && record.attempt < TASK_MAX_ATTEMPT) {
        this.store.resetToQueue(msg.taskId);
        this.queue.push({
          taskId: msg.taskId,
          templateId: record.templateId,
          variables: record.variables,
          preset: (record.variables['_preset'] as string) ?? 'auto',
          quality: (record.variables['_quality'] as string) ?? 'high',
        });
      } else {
        this.store.markFailed(msg.taskId, msg.error);
        this.onEvent({ type: 'failed', taskId: msg.taskId, error: msg.error });
      }
      this.drain();
    } else if (msg.type === 'progress') {
      const now = Date.now();
      const last = this.progressThrottle.get(msg.taskId) ?? 0;
      if (now - last > 1000) {
        this.progressThrottle.set(msg.taskId, now);
        this.store.updateProgress(msg.taskId, msg.progress);
        this.onEvent({ type: 'progress', taskId: msg.taskId, progress: msg.progress });
      }
    }
  }

  private handleWorkerDead(_workerId: number, taskId: string | null): void {
    if (!taskId) return;
    const record = this.store.get(taskId);
    if (!record) return;
    if (record.attempt >= TASK_MAX_ATTEMPT) {
      this.store.markFailed(taskId, 'Worker 崩溃，超过最大重试次数');
      this.onEvent({ type: 'failed', taskId, error: 'Worker 崩溃' });
    } else {
      this.store.resetToQueue(taskId);
      this.queue.push({
        taskId,
        templateId: record.templateId,
        variables: record.variables,
        preset: (record.variables['_preset'] as string) ?? 'auto',
        quality: (record.variables['_quality'] as string) ?? 'high',
      });
      this.drain();
    }
  }
}
