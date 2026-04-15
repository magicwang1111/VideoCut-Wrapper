import path from 'node:path';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import type { TemplateInfo } from '../registry/index.js';
import {
  getResolutionPreset,
  getQualityPreset,
  AUTO_PRESET,
  type ResolutionPreset,
} from '../presets.js';
import { RenderError } from '../errors.js';
import {
  createTask,
  startTask,
  updateProgress,
  completeTask,
  failTask,
  type RenderTask,
} from './task.js';
import type { ProbedVideoInfo, RenderRequest, RenderResult } from './types.js';
import { handleTrimConcat } from './transitions/trim-concat.js';
import { handleXfadeConcat } from './transitions/xfade-concat.js';
import { handleTrimXfadeConcat } from './transitions/trim-xfade-concat.js';
import { handleZoomDissolveConcat } from './transitions/zoom-dissolve-concat.js';
import { handleFlashBlackConcat } from './transitions/flash-black-concat.js';
import { handleTrimMixedConcat } from './transitions/trim-mixed-concat.js';
import type {
  TransitionHandlerArgs,
  TransitionHandlerResult,
} from './transitions/shared.js';

export type { RenderRequest, RenderResult } from './types.js';

interface ProbeVideoResult {
  info: ProbedVideoInfo | null;
  reason?: string;
}

interface VideoProbeFailure {
  key: string;
  src: string;
  reason: string;
}

interface CollectedVideoInfo {
  videoInfo: Map<string, ProbedVideoInfo>;
  failures: VideoProbeFailure[];
}

function findFileRecursive(
  dir: string,
  filename: string,
  maxDepth = 3,
): string | null {
  if (!fs.existsSync(dir) || maxDepth < 0) {
    return null;
  }

  const directMatch = path.join(dir, filename);
  if (fs.existsSync(directMatch)) {
    return directMatch;
  }

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;

    const found = findFileRecursive(
      path.join(dir, entry.name),
      filename,
      maxDepth - 1,
    );
    if (found) {
      return found;
    }
  }

  return null;
}

export function resolveFfprobePath(rootDir: string): string | null {
  const explicitPath = process.env.FFPROBE_PATH;
  if (explicitPath && fs.existsSync(explicitPath)) {
    return explicitPath;
  }

  const candidateRoots = [
    path.join(rootDir, 'ffmpeg'),
    path.join(path.dirname(rootDir), 'ffmpeg'),
  ];

  for (const root of candidateRoots) {
    const found = findFileRecursive(root, 'ffprobe.exe');
    if (found) {
      return found;
    }
  }

  try {
    const resolved = execFileSync('where.exe', ['ffprobe'], {
      encoding: 'utf-8',
      timeout: 3000,
    })
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean);

    return resolved ?? null;
  } catch {
    return null;
  }
}

export function resolveFfmpegPath(rootDir: string): string | null {
  const explicitPath = process.env.FFMPEG_PATH;
  if (explicitPath && fs.existsSync(explicitPath)) {
    return explicitPath;
  }

  const candidateRoots = [
    path.join(rootDir, 'ffmpeg'),
    path.join(path.dirname(rootDir), 'ffmpeg'),
  ];

  for (const root of candidateRoots) {
    const found = findFileRecursive(root, 'ffmpeg.exe');
    if (found) {
      return found;
    }
  }

  try {
    const resolved = execFileSync('where.exe', ['ffmpeg'], {
      encoding: 'utf-8',
      timeout: 3000,
    })
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean);

    return resolved ?? null;
  } catch {
    return null;
  }
}

function probeVideo(
  ffprobePath: string,
  videoPath: string,
): ProbeVideoResult {
  try {
    const raw = execFileSync(
      ffprobePath,
      [
        '-v', 'error',
        '-print_format', 'json',
        '-show_streams',
        '-show_format',
        '-select_streams', 'v:0',
        videoPath,
      ],
      { encoding: 'utf-8', timeout: 10000 },
    );

    const info = JSON.parse(raw) as {
      streams?: Array<{
        width?: number;
        height?: number;
        r_frame_rate?: string;
        avg_frame_rate?: string;
        duration?: string;
      }>;
      format?: {
        duration?: string;
      };
    };

    const stream = info.streams?.[0];
    if (!stream?.width || !stream?.height) {
      return {
        info: null,
        reason: 'ffprobe 未返回有效的视频宽高信息',
      };
    }

    const fpsStr = stream.r_frame_rate ?? stream.avg_frame_rate ?? '30/1';
    const [num, den] = fpsStr.split('/').map(Number);
    const fps = den ? Math.round(num / den) : num;
    const duration = Number(stream.duration ?? info.format?.duration ?? 0);
    const normalizedDuration = Number.isFinite(duration) ? duration : 0;

    return {
      info: {
        width: stream.width,
        height: stream.height,
        fps: fps || 30,
        duration: normalizedDuration,
        label: `原始分辨率 ${stream.width}x${stream.height} ${fps || 30}fps（自动探测）`,
      },
      reason: normalizedDuration > 0 ? undefined : 'ffprobe 未返回有效时长',
    };
  } catch (error) {
    return {
      info: null,
      reason: formatProbeError(error),
    };
  }
}

function formatProbeError(error: unknown): string {
  const stderr =
    typeof error === 'object' &&
    error !== null &&
    'stderr' in error &&
    typeof (error as { stderr?: unknown }).stderr === 'string'
      ? (error as { stderr: string }).stderr.trim()
      : '';

  if (stderr) {
    return (
      stderr
        .split(/\r?\n/)
        .map((line) => line.trim())
        .find(Boolean) ?? 'ffprobe 执行失败'
    );
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }

  return 'ffprobe 执行失败';
}

function findFirstVideoKey(
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
): string | null {
  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type === 'video') {
      const val = variables[key];
      if (typeof val === 'string' && val) return key;
    }
  }
  return null;
}

function collectVideoInfo(
  ffprobePath: string | null,
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
): CollectedVideoInfo {
  const videoInfo = new Map<string, ProbedVideoInfo>();
  const failures: VideoProbeFailure[] = [];
  if (!ffprobePath) {
    return { videoInfo, failures };
  }

  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type === 'video_list') {
      const list = variables[key];
      if (!Array.isArray(list)) continue;

      const durations: number[] = [];
      for (const [index, src] of (list as string[]).entries()) {
        if (typeof src !== 'string' || !src.trim()) {
          durations.push(0);
          continue;
        }

        const probed = probeVideo(ffprobePath, src);
        durations.push(probed.info?.duration ?? 0);

        if (probed.info && !videoInfo.has(key)) {
          videoInfo.set(key, probed.info);
        }

        if (!probed.info || probed.reason) {
          failures.push({
            key: `${key}[${index}]`,
            src,
            reason: probed.reason ?? 'ffprobe 未返回有效视频信息',
          });
        }
      }

      (variables as Record<string, unknown>)[`${key}_source_durations`] = durations;
      continue;
    }

    if (def.type !== 'video') continue;

    const value = variables[key];
    if (typeof value !== 'string' || value.trim() === '') continue;

    const probed = probeVideo(ffprobePath, value);
    if (probed.info) {
      videoInfo.set(key, probed.info);
    }

    if (!probed.info || probed.reason) {
      failures.push({
        key,
        src: value,
        reason: probed.reason ?? 'ffprobe 未返回有效视频信息',
      });
    }
  }

  return { videoInfo, failures };
}

function injectVideoMetadataVariables(
  variables: Record<string, unknown>,
  videoInfo: Map<string, ProbedVideoInfo>,
): Record<string, unknown> {
  if (videoInfo.size === 0) {
    return variables;
  }

  const injected = { ...variables };
  for (const [key, metadata] of videoInfo.entries()) {
    injected[`${key}_source_duration`] = metadata.duration;
  }

  return injected;
}

export class RenderService {
  private rootDir: string;
  private outputDir: string;

  constructor(rootDir: string) {
    this.rootDir = rootDir;
    this.outputDir = path.join(rootDir, 'output');
  }

  private finalizeSuccess(
    task: RenderTask,
    outputPath: string,
    startTime: number,
  ): RenderResult {
    const elapsed = (Date.now() - startTime) / 1000;
    completeTask(task, outputPath);
    return {
      taskId: task.id,
      status: 'completed',
      outputPath,
      duration: elapsed,
    };
  }

  private async finalizeTransitionSuccess(
    request: RenderRequest,
    task: RenderTask,
    outDir: string,
    resPreset: ResolutionPreset,
    startTime: number,
    result: TransitionHandlerResult,
    cleanupFns: Array<() => void>,
  ): Promise<RenderResult> {
    cleanupFns.push(result.cleanup);
    const completed = this.finalizeSuccess(task, result.outputPath, startTime);

    console.log(`[3/3] 归档产物...`);
    await this.writeMeta(
      outDir,
      task,
      request,
      resPreset,
      completed.duration ?? 0,
    );

    return completed;
  }

  async render(request: RenderRequest): Promise<RenderResult> {
    const ffprobePath = resolveFfprobePath(this.rootDir);
    const ffmpegPath = resolveFfmpegPath(this.rootDir);
    const { videoInfo, failures: videoProbeFailures } = collectVideoInfo(
      ffprobePath,
      request.variables,
      request.templateInfo,
    );
    const renderVariables = injectVideoMetadataVariables(
      request.variables,
      videoInfo,
    );

    if (
      ['trim-concat', 'xfade-concat', 'trim-xfade-concat', 'zoom-dissolve-concat', 'flash-black-concat', 'trim-mixed-concat']
        .includes(request.templateId)
    ) {
      if (!ffmpegPath || !ffprobePath) {
        throw new RenderError(
          `${request.templateId} 模板依赖 FFmpeg，请安装 FFmpeg 或通过环境变量 FFMPEG_PATH / FFPROBE_PATH 指定路径。`,
        );
      }

      if (videoProbeFailures.length > 0) {
        const failureDetails = videoProbeFailures
          .map(({ key, src, reason }) => `  - ${key}: ${src} (${reason})`)
          .join('\n');
        throw new RenderError(
          `${request.templateId} 模板无法读取以下输入视频的时长，请确认素材可被 ffprobe 正常探测：\n${failureDetails}`,
        );
      }
    }

    let resPreset: ResolutionPreset;
    if (request.preset === AUTO_PRESET) {
      const firstVideoKey = findFirstVideoKey(
        request.variables,
        request.templateInfo,
      );
      const videoPath =
        firstVideoKey && typeof request.variables[firstVideoKey] === 'string'
          ? (request.variables[firstVideoKey] as string)
          : null;
      const probed = firstVideoKey ? videoInfo.get(firstVideoKey) ?? null : null;
      if (probed) {
        console.log(
          `  自动探测分辨率 ${probed.width}x${probed.height} ${probed.fps}fps (来源: ${path.basename(videoPath!)})`,
        );
        resPreset = probed;
      } else {
        console.warn('  ⚠ 无法探测视频信息，回退到 douyin_vertical (1080x1920)');
        resPreset = getResolutionPreset('douyin_vertical');
      }
    } else {
      resPreset = getResolutionPreset(request.preset);
    }

    const qualPreset = getQualityPreset(request.quality);

    const task = createTask(request.templateId, request.variables);
    if (request.taskId) task.id = request.taskId;
    startTask(task);

    const projectName = path.basename(request.projectDir);
    const outDir = path.join(this.outputDir, projectName);
    fs.mkdirSync(outDir, { recursive: true });
    const outFile = request.outputFilename ?? 'final.mp4';

    const startTime = Date.now();
    const cleanupFns: Array<() => void> = [];

    try {
      const transitionArgs: TransitionHandlerArgs = {
        rootDir: this.rootDir,
        ffmpegPath: ffmpegPath ?? '',
        ffprobePath: ffprobePath ?? '',
        request,
        videoInfo,
        qualPreset,
        resPreset,
        task,
        outDir,
        outFile,
      };

      if (request.templateId === 'trim-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleTrimConcat(transitionArgs),
          cleanupFns,
        );
      }

      if (request.templateId === 'xfade-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleXfadeConcat(transitionArgs),
          cleanupFns,
        );
      }

      if (request.templateId === 'trim-xfade-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleTrimXfadeConcat(transitionArgs),
          cleanupFns,
        );
      }

      if (request.templateId === 'zoom-dissolve-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleZoomDissolveConcat(transitionArgs),
          cleanupFns,
        );
      }

      if (request.templateId === 'flash-black-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleFlashBlackConcat(transitionArgs),
          cleanupFns,
        );
      }

      if (request.templateId === 'trim-mixed-concat') {
        return await this.finalizeTransitionSuccess(
          request,
          task,
          outDir,
          resPreset,
          startTime,
          handleTrimMixedConcat(transitionArgs),
          cleanupFns,
        );
      }

      console.log(`\n[1/3] 准备渲染环境...`);

      const { renderVideo } = await import('@revideo/renderer');

      console.log(`[2/3] 调用 Revideo 渲染引擎...`);

      const outputPath = await renderVideo({
        projectFile: request.templateInfo.entryPath,
        variables: renderVariables,
        settings: {
          outFile: outFile as `${string}.mp4`,
          outDir,
          logProgress: true,
          projectSettings: {
            size: { x: resPreset.width, y: resPreset.height },
          },
          progressCallback: (_worker: number, progress: number) => {
            updateProgress(task, progress);
          },
        },
      });

      const completed = this.finalizeSuccess(task, outputPath, startTime);

      console.log(`[3/3] 归档产物...`);
      await this.writeMeta(
        outDir,
        task,
        request,
        resPreset,
        completed.duration ?? 0,
      );

      return completed;
    } catch (err) {
      const elapsed = (Date.now() - startTime) / 1000;
      const errMsg = err instanceof Error ? err.message : String(err);
      failTask(task, errMsg);

      return {
        taskId: task.id,
        status: 'failed',
        duration: elapsed,
        error: errMsg,
      };
    } finally {
      for (const cleanup of cleanupFns) {
        try {
          cleanup();
        } catch {
          // ignore temp cleanup errors
        }
      }
    }
  }

  private async writeMeta(
    outDir: string,
    task: RenderTask,
    request: RenderRequest,
    resPreset: { width: number; height: number },
    duration: number,
  ): Promise<void> {
    const meta = {
      taskId: task.id,
      templateId: request.templateId,
      templateVersion: request.templateInfo.manifest.version,
      preset: request.preset,
      quality: request.quality,
      variables: request.variables,
      renderedAt: new Date().toISOString(),
      duration: Math.round(duration * 10) / 10,
      resolution: `${resPreset.width}x${resPreset.height}`,
    };

    const metaPath = path.join(outDir, 'meta.json');
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), 'utf-8');
  }
}
