// src/api/server.ts
import Fastify from 'fastify';
import multipart from '@fastify/multipart';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { TaskStore } from '../store/TaskStore.js';
import { OssClient } from '../oss/OssClient.js';
import { TaskQueue } from '../queue/TaskQueue.js';
import { uploadRoutes } from './routes/upload.js';
import { renderRoutes } from './routes/render.js';
import { tasksRoutes } from './routes/tasks.js';

const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const DB_PATH = process.env.DB_PATH
  ? path.resolve(process.env.DB_PATH)
  : path.join(ROOT_DIR, 'data', 'tasks.db');
const PORT = parseInt(process.env.PORT ?? '3000', 10);
const BODY_LIMIT_BYTES = parseInt(process.env.BODY_LIMIT_BYTES ?? `${500 * 1024 * 1024}`, 10);
const WORKER_COUNT =
  parseInt(process.env.WORKER_COUNT || '0', 10) || Math.max(1, Math.floor(os.cpus().length / 2));

async function main(): Promise<void> {
  const store = new TaskStore(DB_PATH);
  const oss = new OssClient();

  const queue = new TaskQueue(store, oss, WORKER_COUNT, (event) => {
    if (event.type === 'completed') console.log(`[Task] ${event.taskId} 完成，ossKey=${event.ossKey}`);
    if (event.type === 'failed') console.log(`[Task] ${event.taskId} 失败：${event.error}`);
  });

  await queue.start();
  console.log(`[Queue] ${WORKER_COUNT} 个 Worker 已启动`);

  const cleaned = store.cleanupOldTasks(parseInt(process.env.TASK_TTL_DAYS ?? '7', 10));
  if (cleaned > 0) console.log(`[Store] 清理 ${cleaned} 条过期任务`);

  const app = Fastify({
    logger: true,
    bodyLimit: BODY_LIMIT_BYTES,
  });
  await app.register(multipart, {
    limits: {
      files: 1,
      fileSize: BODY_LIMIT_BYTES,
    },
  });

  await app.register(uploadRoutes, { store, oss });
  await app.register(renderRoutes, { store, queue, oss });
  await app.register(tasksRoutes, { store, oss });

  app.get('/health', async () => ({ ok: true, workers: WORKER_COUNT, queueSize: queue.queueSize }));

  await app.listen({ port: PORT, host: '0.0.0.0' });
  console.log(`[API] 监听 :${PORT}`);

  process.on('SIGTERM', async () => {
    await app.close();
    store.close();
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
