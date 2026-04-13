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

interface ProbedVideoInfo extends ResolutionPreset {
  duration: number;
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

function resolveFfprobePath(rootDir: string): string | null {
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

/** 用 ffprobe 探测视频的分辨率、帧率和时长 */
function probeVideo(
  ffprobePath: string,
  videoPath: string,
): ProbedVideoInfo | null {
  try {
    const raw = execFileSync(
      ffprobePath,
      [
        '-v', 'quiet',
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
    if (!stream?.width || !stream?.height) return null;

    // 解析帧率：格式为 "30/1" 或 "30000/1001"
    const fpsStr = stream.r_frame_rate ?? stream.avg_frame_rate ?? '30/1';
    const [num, den] = fpsStr.split('/').map(Number);
    const fps = den ? Math.round(num / den) : num;
    const duration = Number(stream.duration ?? info.format?.duration ?? 0);

    return {
      width: stream.width,
      height: stream.height,
      fps: fps || 30,
      duration: Number.isFinite(duration) ? duration : 0,
      label: `原始分辨率 ${stream.width}×${stream.height} ${fps}fps（自动探测）`,
    };
  } catch {
    return null;
  }
}

/** 从变量中找第一个视频变量 key，用于自动探测分辨率 */
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
): Map<string, ProbedVideoInfo> {
  const videoInfo = new Map<string, ProbedVideoInfo>();
  if (!ffprobePath) {
    return videoInfo;
  }

  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video') continue;

    const value = variables[key];
    if (typeof value !== 'string' || value.trim() === '') continue;

    const probed = probeVideo(ffprobePath, value);
    if (probed) {
      videoInfo.set(key, probed);
    }
  }

  return videoInfo;
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

function countProvidedVideoInputs(
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
): number {
  let count = 0;

  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video') continue;

    const value = variables[key];
    if (typeof value === 'string' && value.trim() !== '') {
      count += 1;
    }
  }

  return count;
}

export interface RenderRequest {
  templateId: string;
  templateInfo: TemplateInfo;
  variables: Record<string, unknown>;
  preset: string;
  quality: string;
  outputFilename?: string;
  projectDir: string;
}

export interface RenderResult {
  taskId: string;
  status: 'completed' | 'failed';
  outputPath?: string;
  duration?: number; // 渲染耗时（秒）
  error?: string;
}

export class RenderService {
  private rootDir: string;
  private outputDir: string;

  constructor(rootDir: string) {
    this.rootDir = rootDir;
    this.outputDir = path.join(rootDir, 'output');
  }

  async render(request: RenderRequest): Promise<RenderResult> {
    const ffprobePath = resolveFfprobePath(this.rootDir);
    const videoInfo = collectVideoInfo(
      ffprobePath,
      request.variables,
      request.templateInfo,
    );
    const renderVariables = injectVideoMetadataVariables(
      request.variables,
      videoInfo,
    );
    const providedVideoCount = countProvidedVideoInputs(
      request.variables,
      request.templateInfo,
    );

    if (request.templateId === 'trim-concat') {
      if (!ffprobePath) {
        throw new RenderError(
          'trim-concat 模板依赖 ffprobe 获取视频时长，但当前环境未找到 ffprobe。请安装 FFmpeg，或设置 FFPROBE_PATH 环境变量。',
        );
      }

      if (providedVideoCount > 0 && videoInfo.size !== providedVideoCount) {
        throw new RenderError(
          'trim-concat 模板无法读取全部输入视频的时长，请确认素材可被 ffprobe 正常探测。',
        );
      }
    }

    // 分辨率：auto 则探测输入视频，否则用具名预设
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
          `  自动探测分辨率: ${probed.width}×${probed.height} ${probed.fps}fps (来源: ${path.basename(videoPath!)})`,
        );
        resPreset = probed;
      } else {
        console.warn(`  ⚠ 无法探测视频信息，回退到 douyin_vertical (1080×1920)`);
        resPreset = getResolutionPreset('douyin_vertical');
      }
    } else {
      resPreset = getResolutionPreset(request.preset);
    }

    const qualPreset = getQualityPreset(request.quality);

    // 创建任务
    const task = createTask(request.templateId, request.variables);
    startTask(task);

    // 确定输出路径
    const projectName = path.basename(request.projectDir);
    const outDir = path.join(this.outputDir, projectName);
    fs.mkdirSync(outDir, { recursive: true });
    const outFile = request.outputFilename ?? 'final.mp4';

    const startTime = Date.now();

    try {
      console.log(`\n[1/3] 准备渲染环境...`);

      // 动态导入 @revideo/renderer
      const { renderVideo } = await import('@revideo/renderer');

      console.log(`[2/3] 调用 Revideo 渲染引擎...`);

      const outputPath = await renderVideo({
        projectFile: request.templateInfo.entryPath,
        variables: renderVariables,
        settings: {
          outFile,
          outDir,
          dimensions: [resPreset.width, resPreset.height],
          logProgress: true,
          progressCallback: (_worker: number, progress: number) => {
            updateProgress(task, progress);
          },
        },
      });

      const elapsed = (Date.now() - startTime) / 1000;
      completeTask(task, outputPath);

      console.log(`[3/3] 归档产物...`);

      // 写入 meta.json
      await this.writeMeta(outDir, task, request, resPreset, elapsed);

      return {
        taskId: task.id,
        status: 'completed',
        outputPath,
        duration: elapsed,
      };
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
