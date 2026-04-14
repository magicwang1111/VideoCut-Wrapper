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

function buildNormalizeVideoFilter(resPreset: ResolutionPreset): string {
  return [
    `fps=${resPreset.fps}`,
    `scale=${resPreset.width}:${resPreset.height}:force_original_aspect_ratio=decrease:flags=lanczos`,
    `pad=${resPreset.width}:${resPreset.height}:(ow-iw)/2:(oh-ih)/2:color=black`,
    'setsar=1',
    'format=yuv420p',
  ].join(',');
}

export class RenderService {
  private rootDir: string;
  private outputDir: string;

  constructor(rootDir: string) {
    this.rootDir = rootDir;
    this.outputDir = path.join(rootDir, 'output');
  }

  private normalizeClips(
    ffmpegPath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    qualPreset: QualityPreset,
    resPreset: ResolutionPreset,
  ): {
    clips: Array<{ key: string; src: string; duration: number }>;
    cleanup: () => void;
  } {
    const tempDir = path.join(this.rootDir, 'temp');
    fs.mkdirSync(tempDir, { recursive: true });

    const normalizeFilter = buildNormalizeVideoFilter(resPreset);
    const sessionId = `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    const tempFiles: string[] = [];

    const cleanup = () => {
      for (const file of tempFiles) {
        try {
          fs.unlinkSync(file);
        } catch {
          // ignore temp cleanup errors
        }
      }
    };

    try {
      console.log(
        `  [normalize] all input videos -> ${resPreset.width}x${resPreset.height} ${resPreset.fps}fps (lanczos)`,
      );

      const normalizedClips = clips.map((clip, index) => {
        const tempFile = path.join(tempDir, `normalized_${sessionId}_${index}.mp4`);
        tempFiles.push(tempFile);

        execFileSync(
          ffmpegPath,
          [
            '-i', clip.src,
            '-vf', normalizeFilter,
            '-c:v', 'libx264',
            '-preset', qualPreset.ffmpegPreset,
            '-crf', String(qualPreset.crf),
            '-pix_fmt', 'yuv420p',
            '-an',
            '-y', tempFile,
          ],
          { timeout: 600_000 },
        );

        return {
          ...clip,
          src: tempFile,
        };
      });

      return {
        clips: normalizedClips,
        cleanup,
      };
    } catch (err) {
      cleanup();
      throw err;
    }
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
    resPreset: ResolutionPreset,
    transitionDuration: number,
    trimStart: number = 0,
  ): void {
    const n = clips.length;
    const D = transitionDuration;
    const fps = resPreset.fps;
    const frameDuration = 1 / fps;
    const minSegmentDuration = frameDuration / 2;
    const formatSeconds = (value: number) => value.toFixed(6);

    // ── 构造输入参数与视频输入索引 ──────────────────────────────────────────
    const inputArgs: string[] = [];
    const clipMeta: Array<{ videoIdx: number; duration: number }> = [];
    let inputCounter = 0;

    for (const clip of clips) {
      if (trimStart > 0) inputArgs.push('-ss', String(trimStart));
      inputArgs.push('-i', clip.src);
      clipMeta.push({
        videoIdx: inputCounter,
        duration: Math.max(0, clip.duration - trimStart),
      });
      inputCounter++;
    }

    // ── 构建 filter_complex（仅视频 xfade 链）─────────────────────────────
    void ffprobePath;

    const filterParts: string[] = [];
    const segmentLabels: string[] = [];

    if (n === 1) {
      filterParts.push(
        `[${clipMeta[0].videoIdx}:v]setpts=PTS-STARTPTS,fps=fps=${fps}[vout]`,
      );
    } else {
      for (let i = 0; i < n - 1; i++) {
        const current = clipMeta[i];
        const next = clipMeta[i + 1];
        const bodyDuration = Math.max(0, current.duration - D);
        const availableTailDuration = Math.min(current.duration, D);
        const tailStart = Math.max(0, current.duration - D);
        const tailPadDuration = Math.max(0, D - availableTailDuration);
        const stillPadDuration = Math.max(0, D - frameDuration);

        if (tailPadDuration > minSegmentDuration) {
          console.warn(
            `  ⚠ 片段 ${clips[i].key} 尾部不足 ${D.toFixed(1)}s，已自动复制最后一帧补足淡出转场`,
          );
        }

        if (bodyDuration > minSegmentDuration) {
          filterParts.push(
            `[${current.videoIdx}:v]trim=duration=${formatSeconds(bodyDuration)},setpts=PTS-STARTPTS,fps=fps=${fps}[body${i}]`,
          );
          segmentLabels.push(`[body${i}]`);
        }

        filterParts.push(
          `[${current.videoIdx}:v]trim=start=${formatSeconds(tailStart)}:duration=${formatSeconds(availableTailDuration)},setpts=PTS-STARTPTS` +
          (tailPadDuration > 0
            ? `,tpad=stop_mode=clone:stop_duration=${formatSeconds(tailPadDuration)}`
            : '') +
          `,format=rgba,fade=t=out:st=0:d=${formatSeconds(D)}:alpha=1[fade${i}]`,
        );
        filterParts.push(
          `[${next.videoIdx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=${formatSeconds(stillPadDuration)},fps=fps=${fps}[still${i}]`,
        );
        filterParts.push(
          `[still${i}][fade${i}]overlay=eof_action=pass:shortest=1,format=yuv420p,fps=fps=${fps}[transition${i}]`,
        );
        segmentLabels.push(`[transition${i}]`);
      }

      filterParts.push(
        `[${clipMeta[n - 1].videoIdx}:v]setpts=PTS-STARTPTS,fps=fps=${fps}[last]`,
      );
      segmentLabels.push('[last]');
      filterParts.push(
        `${segmentLabels.join('')}concat=n=${segmentLabels.length}:v=1:a=0[vout]`,
      );
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

    console.log(`\n[2/3] FFmpeg 前段淡出拼接 ${n} 个片段（转场 ${D}s，长度不足自动补边缘帧）...`);
    execFileSync(ffmpegPath, args, { timeout: 600_000 });

    updateProgress(task, 1);
  }

  /**
   * 中心拉近叠化拼接：每段出画片段的最后 D 秒由四周向中心拉近（1→zoomScale），
   * 同时淡出；下一段首帧静帧垫底，下一段本身不淡入。
   * 叠化逻辑与 xfade-concat 保持一致。
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
    const fps = resPreset.fps;
    const frameDuration = 1 / fps;
    const minSegmentDuration = frameDuration / 2;
    const formatSeconds = (value: number) => value.toFixed(6);

    // 径向 zoom blur：fps 过采样后 tmix 多帧平均，使每帧内部呈现从中心向外的射线感
    // oversample=8 → 每个原始帧展开成 8 个不同 zoom 级别的子帧，平均后即为 zoom blur
    const oversample = 8;
    const fps_hi = fps * oversample;
    const frames_hi = Math.max(2, Math.round(fps_hi * D));
    const zDelta = (zoomScale - 1).toFixed(6);
    const frameProgress = `(n/${frames_hi - 1})`;
    // quartic ease-in: p^4 —— 前段几乎不动，后段爆炸性加速拉入
    const easedProgress = `(${frameProgress}*${frameProgress}*${frameProgress}*${frameProgress})`;
    const zoomExpr = `(1+${zDelta}*${easedProgress})`;
    const scaleW = `floor(${W}*${zoomExpr}/2)*2`;
    const scaleH = `floor(${H}*${zoomExpr}/2)*2`;
    const cropX = `${W}*${zDelta}*${easedProgress}/2`;
    const cropY = `${H}*${zDelta}*${easedProgress}/2`;

    // ── 构造输入参数 ──────────────────────────────────────────────────────────
    const inputArgs: string[] = [];
    const clipMeta: Array<{ videoIdx: number; duration: number }> = [];
    let inputCounter = 0;
    for (const clip of clips) {
      inputArgs.push('-i', clip.src);
      clipMeta.push({ videoIdx: inputCounter, duration: clip.duration });
      inputCounter++;
    }

    // ── 构建 filter_complex ───────────────────────────────────────────────────
    const filterParts: string[] = [];
    const segmentLabels: string[] = [];

    if (n === 1) {
      filterParts.push(`[0:v]setpts=PTS-STARTPTS[vout]`);
    } else {
      for (let i = 0; i < n - 1; i++) {
        const current = clipMeta[i];
        const next = clipMeta[i + 1];
        const bodyDuration = Math.max(0, current.duration - D);
        const availableTailDuration = Math.min(current.duration, D);
        const tailStart = Math.max(0, current.duration - D);
        const tailPadDuration = Math.max(0, D - availableTailDuration);
        const stillPadDuration = Math.max(0, D - frameDuration);

        if (tailPadDuration > minSegmentDuration) {
          console.warn(
            `  ⚠ 片段 ${clips[i].key} 尾部不足 ${D.toFixed(1)}s，已自动复制最后一帧补足拉近叠化转场`,
          );
        }

        // body：clip[i] 去掉最后 D 秒
        if (bodyDuration > minSegmentDuration) {
          filterParts.push(
            `[${current.videoIdx}:v]trim=duration=${formatSeconds(bodyDuration)},setpts=PTS-STARTPTS,fps=fps=${fps}[body${i}]`,
          );
          segmentLabels.push(`[body${i}]`);
        }

        // zoomfade：clip[i] 最后 D 秒，向中心拉近（1→zoomScale）并淡出
        filterParts.push(
          `[${current.videoIdx}:v]trim=start=${formatSeconds(tailStart)}:duration=${formatSeconds(availableTailDuration)},setpts=PTS-STARTPTS` +
          (tailPadDuration > 0
            ? `,tpad=stop_mode=clone:stop_duration=${formatSeconds(tailPadDuration)}`
            : '') +
          `,fps=fps=${fps_hi}` +
          `,scale='${scaleW}':'${scaleH}':eval=frame` +
          `,crop=${W}:${H}:x='${cropX}':y='${cropY}'` +
          `,tmix=frames=${oversample}` +
          `,fps=fps=${fps}` +
          `,format=rgba,fade=t=out:st=${formatSeconds(D * 0.65)}:d=${formatSeconds(D * 0.35)}:alpha=1[zoomfade${i}]`,
        );

        // still：clip[i+1] 首帧静帧，持续 D 秒作底层
        filterParts.push(
          `[${next.videoIdx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=${formatSeconds(stillPadDuration)},fps=fps=${fps}[still${i}]`,
        );

        // transition：静帧垫底，拉近淡出叠上层
        filterParts.push(
          `[still${i}][zoomfade${i}]overlay=eof_action=pass:shortest=1,format=yuv420p,fps=fps=${fps}[transition${i}]`,
        );
        segmentLabels.push(`[transition${i}]`);
      }

      // 最后一段：全段正常播放，不淡入
      filterParts.push(
        `[${clipMeta[n - 1].videoIdx}:v]setpts=PTS-STARTPTS,fps=fps=${fps}[last]`,
      );
      segmentLabels.push('[last]');

      filterParts.push(
        `${segmentLabels.join('')}concat=n=${segmentLabels.length}:v=1:a=0[vout]`,
      );
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

    console.log(`\n[2/3] FFmpeg 中心拉近叠化拼接 ${n} 个片段（拉近 ${D}s，放大 ${zoomScale}x）...`);
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
    const cleanupFns: Array<() => void> = [];

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

        const normalized = this.normalizeClips(
          ffmpegPath!,
          clips,
          qualPreset,
          resPreset,
        );
        cleanupFns.push(normalized.cleanup);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegTrimConcat(
          ffmpegPath!,
          ffprobePath!,
          normalized.clips,
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

      // ── xfade-concat：前段淡出拼接，直接走 FFmpeg ──
      if (request.templateId === 'xfade-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（未提供任何视频）');
        }

        console.log(`  共 ${clips.length} 个片段，前段淡出转场时长 ${transitionDuration}s`);

        const normalized = this.normalizeClips(
          ffmpegPath!,
          clips,
          qualPreset,
          resPreset,
        );
        cleanupFns.push(normalized.cleanup);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegXfadeConcat(
          ffmpegPath!,
          ffprobePath!,
          normalized.clips,
          outputPath,
          qualPreset,
          task,
          resPreset,
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

      // ── trim-xfade-concat：裁头 + 前段淡出拼接，直接走 FFmpeg ──
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

        console.log(`  共 ${clips.length} 个片段，裁去前 ${trimStart}s，前段淡出转场时长 ${transitionDuration}s`);

        const normalized = this.normalizeClips(
          ffmpegPath!,
          clips,
          qualPreset,
          resPreset,
        );
        cleanupFns.push(normalized.cleanup);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegXfadeConcat(
          ffmpegPath!,
          ffprobePath!,
          normalized.clips,
          outputPath,
          qualPreset,
          task,
          resPreset,
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

      // ── zoom-dissolve-concat：中心拉近 + xfade 叠化 ──
      if (request.templateId === 'zoom-dissolve-concat') {
        console.log(`\n[1/3] 收集并校验视频片段...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.4;
        const zoomScale = typeof request.variables['zoom_scale'] === 'number'
          ? request.variables['zoom_scale']
          : 1.18;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('没有可用的视频片段（未提供任何视频）');
        }

        console.log(`  共 ${clips.length} 个片段，中心拉近时长 ${transitionDuration}s，放大倍数 ${zoomScale}x`);

        const normalized = this.normalizeClips(
          ffmpegPath!,
          clips,
          qualPreset,
          resPreset,
        );
        cleanupFns.push(normalized.cleanup);

        const outputPath = path.join(outDir, outFile);
        this.ffmpegZoomDissolveConcat(
          ffmpegPath!,
          normalized.clips,
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
