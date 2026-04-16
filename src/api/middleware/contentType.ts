import { FastifyReply, FastifyRequest } from 'fastify';

function parseMediaType(contentType: string): string {
  return contentType.split(';', 1)[0].trim().toLowerCase();
}

function rejectUnsupportedContentType(
  reply: FastifyReply,
  expected: string,
  received?: string,
): void {
  reply
    .code(415)
    .send({ error: 'unsupported_content_type', expected, received: received ?? null });
}

export function requireContentType(expected: string) {
  return async function contentTypeGuard(
    request: FastifyRequest,
    reply: FastifyReply,
  ): Promise<void> {
    const header = request.raw.headers['content-type'];
    const rawContentType = Array.isArray(header) ? header[0] : header;

    if (!rawContentType) {
      rejectUnsupportedContentType(reply, expected);
      return;
    }

    // Reject leading or trailing whitespace so malformed headers never reach route logic.
    if (rawContentType !== rawContentType.trim()) {
      rejectUnsupportedContentType(reply, expected, rawContentType);
      return;
    }

    if (parseMediaType(rawContentType) !== expected) {
      rejectUnsupportedContentType(reply, expected, rawContentType);
    }
  };
}
