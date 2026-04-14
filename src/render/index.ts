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
    if (def.type === 'video_list') {
      // 探测数组中每个视频，用第一个设置分辨率基准，durations 注入到 variables
      const list = variables[key];
      if (!Array.isArray(list)) continue;
      const durations: number[] = [];
      for (const src of list as string[]) {
        if (typeof src !== 'string' || !src.trim()) { durations.push(0); continue; }
        const probed = probeVideo(ffprobePath, src);
        durations.push(probed?.duration ?? 0);
        if (probed && !videoInfo.has(key)) videoInfo.set(key, probed);
      }
      (variables as Record<string, unknown>)[`${key}_source_durations`] = durations;
      continue;
    }

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
    if (def.type === 'video_list') {
      const list = variables[key];
      if (Array.isArray(list)) count += list.length;
      continue;
    }
    if (def.type !== 'video') continue;

    const value = variables[key];
    if (typeof value === 'string' && value.trim() !== '') {
      count += 1;
    }
  }

  return count;
}

/** 从 manifest 变量中收集视频片段（同时支持具名 video 键和 video_list 数组） */
function collectClips(
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
  videoInfo: Map<string, ProbedVideoInfo>,
): Array<{ key: string; src: string; duration: number }> {
  // 优先读 video_list 字段
  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video_list') continue;
    const list = variables[key];
    if (!Array.isArray(list)) continue;
    const durations = (variables[`${key}_source_durations`] as number[] | undefined) ?? [];
    return list.map((src, i) => ({
      key: `clip_${i + 1}`,
      src: src as string,
      duration: durations[i] ?? 0,
    }));
  }

  // 回退：扫描具名 video 键（兼容现有 CLI 模板）
  const clips: Array<{ key: string; src: string; duration: number }> = [];
  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video') continue;
    const src = variables[key];
    if (typeof src !== 'string' || !src.trim()) continue;
    const info = videoInfo.get(key);
    clips.push({ key, src, duration: info?.duration ?? 0 });
  }
  return clips;
}

export interface RenderRequest {
  taskId?: string; // 外部传入则覆盖内部生成的 ID
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

        const args: string[] = [
          '-ss', String(trimStart),
          '-i', clip.src,
          '-c:v', 'libx264',
          '-preset', qualPreset.ffmpegPreset,
          '-crf', String(qualPreset.crf),
          '-an',
          '-y', tempFile,
        ];

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
    trimStart: number = 0,
  ): void {
    const n = clips.length;
    const D = transitionDuration;

    // ── 构造输入参数与视频输入索引 ──────────────────────────────────────────
    const inputArgs: string[] = [];
    const clipMeta: Array<{ videoIdx: number; duration: number }> = [];
    let inputCounter = 0;

    for (const clip of clips) {
      if (trimStart > 0) inputArgs.push('-ss', String(trimStart));
      inputArgs.push('-i', clip.src);
      clipMeta.push({ videoIdx: inputCounter, duration: clip.duration - trimStart });
      inputCounter++;
    }

    // ── 构建 filter_complex（仅视频 xfade 链）─────────────────────────────
    const filterParts: string[] = [];

    if (n === 1) {
      filterParts.push(`[${clipMeta[0].videoIdx}:v]copy[vout]`);
    } else {
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
    }

    const filterComplex = filterParts.join(';');

    const args: string[] = [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-c:v', 'libx264',
      '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf),
      '-pix_fmt', 'yuv420p',
      '-an',
      '-y', outputPath,
    ];

    console.log(`\n[2/3] FFmpeg 叠化拼接 ${n} 个片段（叠化时长 ${D}s）...`);
    execFileSync(ffmpegPath, args, { timeout: 600_000 });

    updateProgress(task, 1);
  }

  /**
   * 推镜叠化拼接：每段出画片段的最后 D 秒做 scale 放大（推镜），
   * 然后用 xfade 交叉叠化衔接下一段（clip1 淡出 + clip2 淡入，无黑屏）。
   */
  private ffmpegZoomDissolveConcat(
    ffmpegPath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    outputPath: string,
    qualPreset: QualityPreset,
    task: RenderTask,
    resPreset: ResolutionPreset,
    transitionDuration: number,
    zoomScale: number,
  ): void {
    const n = clips.length;
    const D = transitionDuration;
    const W = resPreset.width;
    const H = resPreset.height;

    // scale 放大表达式：t 从 0 到 D，zoom 从 1.0x 线性增长到 zoomScale
    // floor(x/2)*2 确保偶数像素（yuv420p 要求），eval=frame 逐帧计算
    const zDelta = (zoomScale - 1).toFixed(6);
    const scaleW = `floor(${W}*(1+${zDelta}*t/${D})/2)*2`;
    const scaleH = `floor(${H}*(1+${zDelta}*t/${D})/2)*2`;

    // ── 构造输入参数 ──────────────────────────────────────────────────────────
    const inputArgs: string[] = [];
    for (const clip of clips) {
      inputArgs.push('-i', clip.src);
    }

    // ── 构建 filter_complex ───────────────────────────────────────────────────
    const filterParts: string[] = [];

    if (n === 1) {
      filterParts.push(
        `[0:v]scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2[vout]`,
      );
    } else {
      // 对每段出画片段（0 到 n-2）：末尾 D 秒做推镜，与 body 重新拼接
      for (let i = 0; i < n - 1; i++) {
        const dur = clips[i].duration;
        const tailStart = Math.max(0.001, dur - D);
        const tailStartStr = tailStart.toFixed(6);

        if (dur <= D) {
          console.warn(
            `  ⚠ 片段 ${clips[i].key} 时长 ${dur.toFixed(1)}s 不长于推镜时长（${D}s），效果可能异常`,
          );
        }

        // body：正常部分，缩放至目标分辨率
        filterParts.push(
          `[${i}:v]trim=duration=${tailStartStr},setpts=PTS-STARTPTS,scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2[body${i}]`,
        );
        // tail：最后 D 秒，先归一化分辨率，再 scale 放大（eval=frame），再 crop 居中
        // 不加 fade——fade 由后续 xfade 完成
        filterParts.push(
          `[${i}:v]trim=start=${tailStartStr},setpts=PTS-STARTPTS,` +
          `scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2,` +
          `scale='${scaleW}':'${scaleH}':eval=frame,` +
          `crop=${W}:${H}:x='(iw-${W})/2':y='(ih-${H})/2'[zoomed${i}]`,
        );
        // 合并 body + zoomed tail（proc_i 时长与原片段相同）
        // fps 归一化 timebase（concat 输出 1/1000000，xfade 要求两端一致）
        filterParts.push(
          `[body${i}][zoomed${i}]concat=n=2:v=1:a=0,fps=fps=${resPreset.fps}[proc${i}]`,
        );
      }

      // 最后一段直接缩放，不做推镜；同样 fps 归一化
      filterParts.push(
        `[${n - 1}:v]scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2,fps=fps=${resPreset.fps}[last]`,
      );

      // xfade 链：proc_0 → proc_1 → ... → last（与 ffmpegXfadeConcat 相同 offset 公式）
      let prevLabel = `[proc0]`;
      let durationSum = clips[0].duration;

      for (let i = 1; i < n; i++) {
        const offset = Math.max(0.01, durationSum - i * D);
        const outLabel = i === n - 1 ? '[vout]' : `[v${i}]`;
        const nextLabel = i === n - 1 ? '[last]' : `[proc${i}]`;

        filterParts.push(
          `${prevLabel}${nextLabel}xfade=transition=fade:duration=${D}:offset=${offset.toFixed(3)}${outLabel}`,
        );

        prevLabel = outLabel;
        durationSum += clips[i].duration;
      }
    }

    const filterComplex = filterParts.join(';');

    const args: string[] = [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-c:v', 'libx264',
      '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf),
      '-pix_fmt', 'yuv420p',
      '-an',
      '-y', outputPath,
    ];

    console.log(`\n[2/3] FFmpeg 推镜叠化拼接 ${n} 个片段（叠化 ${D}s，放大 ${zoomScale}x）...`);
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

    if (['trim-concat', 'xfade-concat', 'trim-xfade-concat', 'zoom-dissolve-concat'].includes(request.templateId)) {
      if (!ffmpegPath || !ffprobePath) {
        throw new RenderError(
          `${request.templateId} 模板依赖 FFmpeg，请安装 FFmpeg 或通过环境变量 FFMPEG_PATH / FFPROBE_PATH 指定路径。`,
        );
      }

      if (providedVideoCount > 0 && videoInfo.size !== providedVideoCount) {
        throw new RenderError(
          `${request.templateId} 模板无法读取全部输入视频的时长，请确认素材可被 ffprobe 正常探测。`,
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

    // 创建任务（外部传入 taskId 则覆盖）
    const task = createTask(request.templateId, request.variables);
    if (request.taskId) task.id = request.taskId;
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

        // 按 manifest 变量顺序收集所有有效片段（支持具名键和 video_list）
        const allClips = collectClips(request.variables, request.templateInfo, videoInfo);
        const clips: Array<{ key: string; src: string; duration: number }> = [];
        for (const clip of allClips) {
          if (clip.duration > 0 && clip.duration <= TRIM_START) {
            console.warn(`  ⚠ 跳过 ${clip.key}：时长 ${clip.duration.toFixed(1)}s 不足 ${TRIM_START}s`);
            continue;
          }
          clips.push(clip);
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

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

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

      // ── trim-xfade-concat：裁头 + 叠化拼接，直接走 FFmpeg ──
      if (request.templateId === 'trim-xfade-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const trimStart = typeof request.variables['trim_start'] === 'number'
          ? request.variables['trim_start']
          : 2;
        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;

        const allClips2 = collectClips(request.variables, request.templateInfo, videoInfo);
        const clips: Array<{ key: string; src: string; duration: number }> = [];
        for (const clip of allClips2) {
          if (clip.duration > 0 && clip.duration <= trimStart) {
            console.warn(`  ⚠ 跳过 ${clip.key}：时长 ${clip.duration.toFixed(1)}s 不足 ${trimStart}s`);
            continue;
          }
          clips.push(clip);
        }

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（全部片段时长均不足裁剪长度或未提供）');
        }

        console.log(`  共 ${clips.length} 个片段，裁去前 ${trimStart}s，叠化时长 ${transitionDuration}s`);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegXfadeConcat(
          ffmpegPath!,
          ffprobePath!,
          clips,
          outputPath,
          qualPreset,
          task,
          transitionDuration,
          trimStart,
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

      // ── zoom-dissolve-concat：推镜放大+淡黑，直接拼接 ──
      if (request.templateId === 'zoom-dissolve-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;
        const zoomScale = typeof request.variables['zoom_scale'] === 'number'
          ? request.variables['zoom_scale']
          : 1.2;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（未提供任何视频）');
        }

        console.log(`  共 ${clips.length} 个片段，推镜时长 ${transitionDuration}s，放大倍数 ${zoomScale}x`);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegZoomDissolveConcat(
          ffmpegPath!,
          clips,
          outputPath,
          qualPreset,
          task,
          resPreset,
          transitionDuration,
          zoomScale,
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
