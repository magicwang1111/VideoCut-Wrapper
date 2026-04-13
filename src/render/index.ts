import path from 'node:path';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import type { TemplateInfo } from '../registry/index.js';
import {
  getResolutionPreset,
  getQualityPreset,
  AUTO_PRESET,
  type ResolutionPreset,
  type QualityPreset,
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

function resolveFfmpegPath(rootDir: string): string | null {
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

/** 检查视频是否包含音频流 */
function checkHasAudio(ffprobePath: string, videoPath: string): boolean {
  try {
    const raw = execFileSync(
      ffprobePath,
      [
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-select_streams', 'a:0',
        videoPath,
      ],
      { encoding: 'utf-8', timeout: 10000 },
    );
    const info = JSON.parse(raw) as { streams?: unknown[] };
    return (info.streams?.length ?? 0) > 0;
  } catch {
    return false;
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

  /**
   * 用 FFmpeg 直接完成"裁去开头 N 秒 + 拼接"，不经过 Revideo。
   * 两步法：先逐条裁剪到 temp，再用 concat demuxer 合并。
   */
  private ffmpegTrimConcat(
    ffmpegPath: string,
    ffprobePath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    outputPath: string,
    qualPreset: QualityPreset,
    task: RenderTask,
    trimStart: number,
  ): void {
    const tempDir = path.join(this.rootDir, 'temp');
    fs.mkdirSync(tempDir, { recursive: true });

    const sessionId = Date.now().toString(36);
    const tempFiles: string[] = [];

    try {
      console.log(`\n[2/3] FFmpeg 裁剪 ${clips.length} 个片段...`);

      for (let i = 0; i < clips.length; i++) {
        const clip = clips[i];
        const tempFile = path.join(tempDir, `trim_${sessionId}_${i}.mp4`);
        tempFiles.push(tempFile);

        const hasAudio = checkHasAudio(ffprobePath, clip.src);

        const args: string[] = [
          '-ss', String(trimStart),
          '-i', clip.src,
        ];

        if (!hasAudio) {
          // 注入静音音轨，确保所有片段音频格式一致
          args.push('-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100');
        }

        args.push(
          '-c:v', 'libx264',
          '-preset', qualPreset.ffmpegPreset,
          '-crf', String(qualPreset.crf),
          '-c:a', 'aac',
          '-b:a', '192k',
          '-ar', '44100',
          '-ac', '2',
        );

        if (!hasAudio) {
          args.push('-map', '0:v', '-map', '1:a', '-shortest');
        }

        args.push('-y', tempFile);

        console.log(`  [${i + 1}/${clips.length}] 裁剪 ${path.basename(clip.src)}（跳过前 ${trimStart}s）`);
        execFileSync(ffmpegPath, args, { timeout: 300_000 });

        updateProgress(task, (i + 1) / (clips.length + 1));
      }

      // 生成 concat 列表文件
      const listFile = path.join(tempDir, `concat_${sessionId}.txt`);
      const listContent = tempFiles
        .map((f) => `file '${f.replace(/\\/g, '/')}'`)
        .join('\n');
      fs.writeFileSync(listFile, listContent, 'utf-8');

      console.log(`  拼接 ${clips.length} 个片段 → ${path.basename(outputPath)}`);
      execFileSync(
        ffmpegPath,
        [
          '-f', 'concat',
          '-safe', '0',
          '-i', listFile,
          '-c', 'copy',
          '-y', outputPath,
        ],
        { timeout: 600_000 },
      );

      // 清理 concat 列表
      try { fs.unlinkSync(listFile); } catch { /* ignore */ }
    } finally {
      for (const f of tempFiles) {
        try { fs.unlinkSync(f); } catch { /* ignore */ }
      }
    }
  }

  /**
   * 用 FFmpeg filter_complex 完成叠化（cross-dissolve）拼接，单 pass 直出。
   * Video: xfade 链；Audio: acrossfade 链；无音频片段自动补静音。
   */
  private ffmpegXfadeConcat(
    ffmpegPath: string,
    ffprobePath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    outputPath: string,
    qualPreset: QualityPreset,
    task: RenderTask,
    transitionDuration: number,
  ): void {
    const n = clips.length;
    const D = transitionDuration;

    // ── 构造输入参数与音视频输入索引 ──────────────────────────────────────
    interface ClipMeta {
      videoIdx: number;  // ffmpeg -i 的序号
      audioIdx: number;  // 对应音频序号（无音频时为 -1，用 anullsrc 代替）
      duration: number;
    }

    const inputArgs: string[] = [];
    const clipMeta: ClipMeta[] = [];
    let inputCounter = 0;

    for (const clip of clips) {
      const hasAudio = checkHasAudio(ffprobePath, clip.src);
      inputArgs.push('-i', clip.src);
      clipMeta.push({
        videoIdx: inputCounter,
        audioIdx: hasAudio ? inputCounter : -1,
        duration: clip.duration,
      });
      inputCounter++;
    }

    // ── 构建 filter_complex ────────────────────────────────────────────────
    const filterParts: string[] = [];

    // 单片段：无需转场
    if (n === 1) {
      const { videoIdx, audioIdx, duration } = clipMeta[0];
      const aLabel = audioIdx >= 0
        ? `[${audioIdx}:a]`
        : `anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration=${duration}[anull0];[anull0]`;

      filterParts.push(`[${videoIdx}:v]copy[vout]`);
      filterParts.push(`${aLabel}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[aout]`);
    } else {
      // 视频 xfade 链
      let prevVLabel = `[${clipMeta[0].videoIdx}:v]`;
      let durationSum = clipMeta[0].duration;

      for (let i = 1; i < n; i++) {
        const offset = Math.max(0.01, durationSum - i * D);
        const outLabel = i === n - 1 ? '[vout]' : `[v${i}]`;

        if (clipMeta[i].duration < D * 2) {
          console.warn(
            `  ⚠ 片段 ${clips[i].key} 时长 ${clipMeta[i].duration.toFixed(1)}s 短于两倍叠化时长（${(D * 2).toFixed(1)}s），转场可能异常`,
          );
        }

        filterParts.push(
          `${prevVLabel}[${clipMeta[i].videoIdx}:v]xfade=transition=fade:duration=${D}:offset=${offset.toFixed(3)}${outLabel}`,
        );
        prevVLabel = outLabel;
        durationSum += clipMeta[i].duration;
      }

      // 音频 acrossfade 链
      // 为无音频片段先生成带独立 label 的 anullsrc 源，再链入 acrossfade
      const audioLabels: string[] = clipMeta.map((m, i) => {
        if (m.audioIdx >= 0) return `[${m.audioIdx}:a]`;
        const label = `[anull${i}]`;
        filterParts.push(
          `anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration=${m.duration},asetpts=PTS-STARTPTS${label}`,
        );
        return label;
      });

      let prevALabel = audioLabels[0];
      for (let i = 1; i < n; i++) {
        const outLabel = i === n - 1 ? '[aout]' : `[a${i}]`;
        filterParts.push(`${prevALabel}${audioLabels[i]}acrossfade=d=${D}:c1=tri:c2=tri${outLabel}`);
        prevALabel = outLabel;
      }
    }

    const filterComplex = filterParts.join(';');

    const args: string[] = [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-map', '[aout]',
      '-c:v', 'libx264',
      '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf),
      '-pix_fmt', 'yuv420p',
      '-c:a', 'aac',
      '-b:a', '192k',
      '-ar', '44100',
      '-ac', '2',
      '-y', outputPath,
    ];

    console.log(`\n[2/3] FFmpeg 叠化拼接 ${n} 个片段（叠化时长 ${D}s）...`);
    execFileSync(ffmpegPath, args, { timeout: 600_000 });

    updateProgress(task, 1);
  }

  async render(request: RenderRequest): Promise<RenderResult> {
    const ffprobePath = resolveFfprobePath(this.rootDir);
    const ffmpegPath = resolveFfmpegPath(this.rootDir);
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

    if (['trim-concat', 'xfade-concat'].includes(request.templateId)) {
      if (!ffmpegPath || !ffprobePath) {
        throw new RenderError(
          `${request.templateId} 模板依赖 FFmpeg，请安装 FFmpeg 或通过环境变量 FFMPEG_PATH / FFPROBE_PATH 指定路径。`,
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
      // ── trim-concat：直接走 FFmpeg，不经过 Revideo ──
      if (request.templateId === 'trim-concat') {
        const TRIM_START = 2;

        console.log(`\n[1/3] 收集并校验视频片段...`);

        // 按 manifest 变量顺序收集所有有效片段
        const clips: Array<{ key: string; src: string; duration: number }> = [];
        for (const [key, def] of Object.entries(request.templateInfo.manifest.variables)) {
          if (def.type !== 'video') continue;
          const src = request.variables[key];
          if (typeof src !== 'string' || !src.trim()) continue;
          const info = videoInfo.get(key);
          const duration = info?.duration ?? 0;
          if (duration > 0 && duration <= TRIM_START) {
            console.warn(`  ⚠ 跳过 ${key}：时长 ${duration.toFixed(1)}s 不足 ${TRIM_START}s`);
            continue;
          }
          clips.push({ key, src, duration });
        }

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（全部片段时长均不足 2 秒或未提供）');
        }

        console.log(`  共 ${clips.length} 个片段，每段跳过前 ${TRIM_START}s`);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegTrimConcat(
          ffmpegPath!,
          ffprobePath!,
          clips,
          outputPath,
          qualPreset,
          task,
          TRIM_START,
        );

        const elapsed = (Date.now() - startTime) / 1000;
        completeTask(task, outputPath);

        console.log(`[3/3] 归档产物...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // ── xfade-concat：叠化拼接，直接走 FFmpeg ──
      if (request.templateId === 'xfade-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;

        const clips: Array<{ key: string; src: string; duration: number }> = [];
        for (const [key, def] of Object.entries(request.templateInfo.manifest.variables)) {
          if (def.type !== 'video') continue;
          const src = request.variables[key];
          if (typeof src !== 'string' || !src.trim()) continue;
          const info = videoInfo.get(key);
          const duration = info?.duration ?? 0;
          clips.push({ key, src, duration });
        }

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（未提供任何视频）');
        }

        console.log(`  共 ${clips.length} 个片段，叠化时长 ${transitionDuration}s`);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegXfadeConcat(
          ffmpegPath!,
          ffprobePath!,
          clips,
          outputPath,
          qualPreset,
          task,
          transitionDuration,
        );

        const elapsed = (Date.now() - startTime) / 1000;
        completeTask(task, outputPath);

        console.log(`[3/3] 归档产物...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // ── 其他模板：走 Revideo 渲染引擎 ──
      console.log(`\n[1/3] 准备渲染环境...`);

      // 动态导入 @revideo/renderer
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
