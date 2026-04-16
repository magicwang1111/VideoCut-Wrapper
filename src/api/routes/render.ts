// src/api/routes/render.ts
import { FastifyInstance } from 'fastify';
import { authMiddleware } from '../middleware/auth.js';
import { requireContentType } from '../middleware/contentType.js';
import { TaskStore } from '../../store/TaskStore.js';
import { TaskQueue } from '../../queue/TaskQueue.js';
import { OssClient } from '../../oss/OssClient.js';
import crypto from 'node:crypto';

const MAX_RENDER_BODY_BYTES = 1024 * 1024;

export async function renderRoutes(
  app: FastifyInstance,
  { store, queue }: { store: TaskStore; queue: TaskQueue; oss: OssClient },
): Promise<void> {
  app.post<{
    Body: {
      template: string;
      clips: string[];
      params?: Record<string, unknown>;
    };
  }>('/render', {
    preHandler: [authMiddleware, requireContentType('application/json')],
    bodyLimit: MAX_RENDER_BODY_BYTES,
  }, async (request, reply) => {
    const { template, clips, params = {} } = request.body;

    if (!template || !Array.isArray(clips) || clips.length === 0) {
      return reply.code(400).send({ error: 'invalid_body' });
    }

    const ossKeys: string[] = [];
    for (const id of clips) {
      if (id.includes('/')) {
        ossKeys.push(id);
        continue;
      }
      const ossKey = store.getOssKey(id);
      if (!ossKey) return reply.code(400).send({ error: 'file_not_found', fileId: id });
      ossKeys.push(ossKey);
    }

    const taskId = `t_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}`;
    const variables: Record<string, unknown> = {
      clips: ossKeys,
      ...params,
      _preset: (params['preset'] as string) ?? 'auto',
      _quality: (params['quality'] as string) ?? 'high',
    };

    store.create({
      id: taskId,
      templateId: template,
      status: 'pending',
      variables,
      createdAt: new Date().toISOString(),
    });

    const enqueued = queue.enqueue({
      taskId,
      templateId: template,
      variables,
      preset: (params['preset'] as string) ?? 'auto',
      quality: (params['quality'] as string) ?? 'high',
    });

    if (!enqueued) {
      store.markFailed(taskId, '队列已满');
      return reply.code(503).send({ error: 'queue_full', queueSize: queue.queueSize });
    }

    return reply.code(202).send({ taskId });
  });
}
