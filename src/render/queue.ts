/**
 * v1: 内存队列 — 同步顺序执行
 * v2: 可替换为 Redis + Bull 异步队列
 */
export interface QueueJob<T> {
  id: string;
  data: T;
}

export class MemoryQueue<T> {
  private jobs: QueueJob<T>[] = [];

  enqueue(id: string, data: T): QueueJob<T> {
    const job: QueueJob<T> = { id, data };
    this.jobs.push(job);
    return job;
  }

  dequeue(): QueueJob<T> | undefined {
    return this.jobs.shift();
  }

  get size(): number {
    return this.jobs.length;
  }

  get isEmpty(): boolean {
    return this.jobs.length === 0;
  }
}
