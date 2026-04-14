// src/api/middleware/auth.ts
import { FastifyRequest, FastifyReply } from 'fastify';

const API_KEYS = new Set(
  (process.env.API_KEYS ?? '').split(',').map((k) => k.trim()).filter(Boolean)
);

export async function authMiddleware(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const key = request.headers['x-api-key'];
  if (!key || !API_KEYS.has(key as string)) {
    reply.code(401).send({ error: 'unauthorized' });
  }
}
