import crypto from 'node:crypto';

export type TaskStatus = 'pending' | 'rendering' | 'completed' | 'failed';

export interface RenderTask {
  id: string;
  templateId: string;
  status: TaskStatus;
  progress: number; // 0-100
  createdAt: Date;
  startedAt?: Date;
  completedAt?: Date;
  outputPath?: string;
  error?: string;
  variables: Record<string, unknown>; // 快照
}

export function createTask(
  templateId: string,
  variables: Record<string, unknown>,
): RenderTask {
  return {
    id: crypto.randomUUID().slice(0, 8),
    templateId,
    status: 'pending',
    progress: 0,
    createdAt: new Date(),
    variables: { ...variables },
  };
}

export function startTask(task: RenderTask): void {
  task.status = 'rendering';
  task.startedAt = new Date();
}

export function updateProgress(task: RenderTask, progress: number): void {
  task.progress = Math.round(Math.max(0, Math.min(100, progress * 100)));
}

export function completeTask(task: RenderTask, outputPath: string): void {
  task.status = 'completed';
  task.completedAt = new Date();
  task.outputPath = outputPath;
  task.progress = 100;
}

export function failTask(task: RenderTask, error: string): void {
  task.status = 'failed';
  task.completedAt = new Date();
  task.error = error;
}
