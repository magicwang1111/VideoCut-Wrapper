// src/queue/worker-entry.ts
// Worker 子进程入口，通过 IPC 与 API 进程通信

import path from 'node:path';
import fs from 'node:fs';
import { OssClient } from '../oss/OssClient.js';
import { RenderService } from '../render/index.js';
import { TemplateRegistry } from '../registry/index.js';

const ROOT_DIR = path.resolve(new URL('.', import.meta.url).pathname, '../..');
const TEMP_DIR = process.env.TEMP_DIR ? path.resolve(process.env.TEMP_DIR) : path.join(ROOT_DIR, 'temp');
const TEMPLATES_DIR = path.join(ROOT_DIR, 'templates');

const oss = new OssClient();
const renderService = new RenderService(ROOT_DIR);
const registry = new TemplateRegistry(TEMPLATES_DIR);
registry.scan();

process.send!({ type: 'worker_ready' });

interface RunTaskMessage {
  type: 'run_task';
  taskId: string;
  templateId: string;
  variables: Record<string, unknown>;
  preset: string;
  quality: string;
}

process.on('message', async (msg: RunTaskMessage) => {
  if (msg.type !== 'run_task') return;

  const { taskId, templateId, variables, preset, quality } = msg;
  const taskTempDir = path.join(TEMP_DIR, taskId);

  try {
    process.send!({ type: 'lease_start', taskId });

    // 下载 video_list 素材到 temp
    const templateInfo = registry.get(templateId);
    const videoListKey = Object.entries(templateInfo.manifest.variables)
      .find(([, def]) => def.type === 'video_list')?.[0];

    const resolvedVars: Record<string, unknown> = { ...variables };

    if (videoListKey && Array.isArray(variables[videoListKey])) {
      const ossKeys = variables[videoListKey] as string[];
      fs.mkdirSync(taskTempDir, { recursive: true });
      const localPaths: string[] = [];
      for (let i = 0; i < ossKeys.length; i++) {
        const ext = path.extname(ossKeys[i]) || '.mp4';
        const localPath = path.join(taskTempDir, `clip_${i}${ext}`);
        await oss.download(ossKeys[i], localPath);
        localPaths.push(localPath);
      }
      resolvedVars[videoListKey] = localPaths;
    }

    const result = await renderService.render({
      taskId,
      templateId,
      templateInfo,
      variables: resolvedVars,
      preset,
      quality,
      outputFilename: 'final.mp4',
      projectDir: taskTempDir,
    });

    if (result.status === 'failed') {
      process.send!({ type: 'task_failed', taskId, error: result.error ?? 'unknown' });
      return;
    }

    const ossKey = oss.outputKey(taskId);
    await oss.upload(result.outputPath!, ossKey);

    process.send!({ type: 'task_done', taskId, ossKey });
  } catch (err) {
    process.send!({
      type: 'task_failed',
      taskId,
      error: err instanceof Error ? err.message : String(err),
    });
  } finally {
    try { fs.rmSync(taskTempDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
});
