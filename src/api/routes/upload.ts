// src/api/routes/upload.ts
import { FastifyInstance } from 'fastify';
import { OssClient } from '../../oss/OssClient.js';
import { TaskStore } from '../../store/TaskStore.js';
import { authMiddleware } from '../middleware/auth.js';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';

const ALLOWED_EXTS = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mp3', '.wav', '.aac', '.png', '.jpg', '.jpeg', '.webp']);
const MAX_BYTES = 500 * 1024 * 1024;

export async function uploadRoutes(
  app: FastifyInstance,
  { store, oss }: { store: TaskStore; oss: OssClient },
): Promise<void> {
  app.post('/upload', { preHandler: authMiddleware }, async (request, reply) => {
    const data = await request.file({ limits: { fileSize: MAX_BYTES } });
    if (!data) return reply.code(400).send({ error: 'no_file' });

    const ext = path.extname(data.filename).toLowerCase();
    if (!ALLOWED_EXTS.has(ext)) {
      return reply.code(400).send({ error: 'unsupported_format', ext });
    }

    const fileId = crypto.randomUUID().replace(/-/g, '').slice(0, 12);
    const ossKey = oss.inputKey(fileId, ext);

    const tmpPath = path.join(os.tmpdir(), `videocut_upload_${fileId}${ext}`);
    try {
      await new Promise<void>((resolve, reject) => {
        const out = fs.createWriteStream(tmpPath);
        data.file.pipe(out);
        out.on('finish', resolve);
        out.on('error', reject);
      });
      await oss.upload(tmpPath, ossKey);
      store.saveFile(fileId, ossKey);
    } finally {
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
    }

    return reply.send({ fileId, ossKey });
  });
}
