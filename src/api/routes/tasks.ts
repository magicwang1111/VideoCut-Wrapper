// src/api/routes/tasks.ts
import { FastifyInstance } from 'fastify';
import { authMiddleware } from '../middleware/auth.js';
import { TaskStore } from '../../store/TaskStore.js';
import { OssClient } from '../../oss/OssClient.js';

export async function tasksRoutes(
  app: FastifyInstance,
  { store, oss }: { store: TaskStore; oss: OssClient },
): Promise<void> {
  app.get<{ Params: { id: string } }>(
    '/tasks/:id',
    { preHandler: authMiddleware },
    async (request, reply) => {
      const task = store.get(request.params.id);
      if (!task) return reply.code(404).send({ error: 'not_found' });

      let outputUrl: string | null = null;
      if (task.status === 'completed' && task.ossKey) {
        outputUrl = oss.presignUrl(task.ossKey, 3600);
      }

      return reply.send({
        taskId: task.id,
        status: task.status,
        progress: task.progress,
        attempt: task.attempt,
        createdAt: task.createdAt,
        startedAt: task.startedAt,
        completedAt: task.completedAt,
        outputUrl,
        error: task.error,
      });
    },
  );

  app.get<{ Params: { id: string } }>(
    '/tasks/:id/download',
    { preHandler: authMiddleware },
    async (request, reply) => {
      const task = store.get(request.params.id);
      if (!task || task.status !== 'completed' || !task.ossKey) {
        return reply.code(404).send({ error: 'not_found_or_not_ready' });
      }
      const url = oss.presignUrl(task.ossKey, 3600);
      return reply.redirect(url, 302);
    },
  );
}
