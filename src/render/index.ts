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

/** 鐢?ffprobe 鎺㈡祴瑙嗛鐨勫垎杈ㄧ巼銆佸抚鐜囧拰鏃堕暱 */
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

    // 瑙ｆ瀽甯х巼锛氭牸寮忎负 "30/1" 鎴?"30000/1001"
    const fpsStr = stream.r_frame_rate ?? stream.avg_frame_rate ?? '30/1';
    const [num, den] = fpsStr.split('/').map(Number);
    const fps = den ? Math.round(num / den) : num;
    const duration = Number(stream.duration ?? info.format?.duration ?? 0);

    return {
      width: stream.width,
      height: stream.height,
      fps: fps || 30,
      duration: Number.isFinite(duration) ? duration : 0,
      label: `鍘熷鍒嗚鲸鐜?${stream.width}脳${stream.height} ${fps}fps锛堣嚜鍔ㄦ帰娴嬶級`,
    };
  } catch {
    return null;
  }
}

/** 妫€鏌ヨ棰戞槸鍚﹀寘鍚煶棰戞祦 */
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

/** 浠庡彉閲忎腑鎵剧涓€涓棰戝彉閲?key锛岀敤浜庤嚜鍔ㄦ帰娴嬪垎杈ㄧ巼 */
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
      // 鎺㈡祴鏁扮粍涓瘡涓棰戯紝鐢ㄧ涓€涓缃垎杈ㄧ巼鍩哄噯锛宒urations 娉ㄥ叆鍒?variables
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

/** 浠?manifest 鍙橀噺涓敹闆嗚棰戠墖娈碉紙鍚屾椂鏀寔鍏峰悕 video 閿拰 video_list 鏁扮粍锛?*/
function collectClips(
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
  videoInfo: Map<string, ProbedVideoInfo>,
): Array<{ key: string; src: string; duration: number }> {
  // 浼樺厛璇?video_list 瀛楁
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

  // Fallback: scan named video fields for CLI-style templates.
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
  taskId?: string; // 澶栭儴浼犲叆鍒欒鐩栧唴閮ㄧ敓鎴愮殑 ID
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
  duration?: number; // render time in seconds
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
   * 鐢?FFmpeg 鐩存帴瀹屾垚"瑁佸幓寮€澶?N 绉?+ 鎷兼帴"锛屼笉缁忚繃 Revideo銆?   * 涓ゆ娉曪細鍏堥€愭潯瑁佸壀鍒?temp锛屽啀鐢?concat demuxer 鍚堝苟銆?   */
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
      console.log(`\n[2/3] FFmpeg trimming ${clips.length} clips...`);

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

        console.log(`  [${i + 1}/${clips.length}] trimming ${path.basename(clip.src)} (skip first ${trimStart}s)`);
        execFileSync(ffmpegPath, args, { timeout: 300_000 });

        updateProgress(task, (i + 1) / (clips.length + 1));
      }

      // 鐢熸垚 concat 鍒楄〃鏂囦欢
      const listFile = path.join(tempDir, `concat_${sessionId}.txt`);
      const listContent = tempFiles
        .map((f) => `file '${f.replace(/\\/g, '/')}'`)
        .join('\n');
      fs.writeFileSync(listFile, listContent, 'utf-8');

      console.log(`  concatenating ${clips.length} clips -> ${path.basename(outputPath)}`);
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

      // 娓呯悊 concat 鍒楄〃
      try { fs.unlinkSync(listFile); } catch { /* ignore */ }
    } finally {
      for (const f of tempFiles) {
        try { fs.unlinkSync(f); } catch { /* ignore */ }
      }
    }
  }

  /**
   * 鐢?FFmpeg filter_complex 瀹屾垚鍙犲寲锛坈ross-dissolve锛夋嫾鎺ワ紝鍗?pass 鐩村嚭銆?   * Video: xfade 閾撅紱Audio: acrossfade 閾撅紱鏃犻煶棰戠墖娈佃嚜鍔ㄨˉ闈欓煶銆?   */
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

    // 鈹€鈹€ 鏋勯€犺緭鍏ュ弬鏁颁笌瑙嗛杈撳叆绱㈠紩 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    // 鈹€鈹€ 鏋勫缓 filter_complex锛堜粎瑙嗛 xfade 閾撅級鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    void ffprobePath;

    const filterParts: string[] = [];

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
            `  [warn] clip ${clips[i].key} tail is shorter than ${D.toFixed(1)}s, cloning the last frame to finish the fade-out transition.`, 
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

    console.log(`\n[2/3] FFmpeg fade-out concat ${n} clips (transition ${D}s, auto clone edge frames when needed)...`);
    execFileSync(ffmpegPath, args, { timeout: 600_000 });

    updateProgress(task, 1);
  }

  private ffmpegZoomDissolveConcat(
    ffmpegPath: string,
    clips: Array<{ key: string; src: string; duration: number }>,
    outputPath: string,
    qualPreset: QualityPreset,
    task: RenderTask,
    resPreset: ResolutionPreset,
    transitionDuration: number,
    blurStrength: number,
    numSamples: number,
    aTargetScale: number,
    bStartScale: number,
  ): void {
    const n = clips.length;
    const W = resPreset.width;
    const H = resPreset.height;
    const fps = resPreset.fps;
    const formatSeconds = (value: number) => value.toFixed(6);
    const clamp = (value: number, min: number, max: number) =>
      Math.min(max, Math.max(min, value));
    const escapeExpr = (value: string) => value.replace(/,/g, '\\,');
    const clipFrames = clips.map((clip) => Math.max(1, Math.round(clip.duration * fps)));
    const requestedTransitionFrames = Math.max(1, Math.round(transitionDuration * fps));
    const blurStrengthValue = clamp(blurStrength, 0, 1);
    const sampleCount = clamp(Math.round(numSamples), 5, 25);
    const aTargetScaleValue = clamp(aTargetScale, 0.1, 0.9);
    const bStartScaleValue = clamp(bStartScale, 1.1, 3.0);
    const aTargetScaleDelta = (aTargetScaleValue - 1).toFixed(6);
    const bStartScaleBase = bStartScaleValue.toFixed(6);
    const bStartScaleDelta = (1 - bStartScaleValue).toFixed(6);
    const blurStrengthFixed = blurStrengthValue.toFixed(6);
    const bgBlurSigma = Math.max(0.5, 1 + blurStrengthValue * 6).toFixed(6);

    type TransitionPlan = {
      transitionFrames: number;
      leftFrames: number;
      rightFrames: number;
      bodyStartFrames: number[];
      bodyFrames: number[];
      tailStartFrames: number[];
      headFrames: number[];
    };

    const buildTransitionPlan = (requestedFrames: number): TransitionPlan => {
      if (n <= 1) {
        return {
          transitionFrames: 0,
          leftFrames: 0,
          rightFrames: 0,
          bodyStartFrames: new Array(n).fill(0),
          bodyFrames: [...clipFrames],
          tailStartFrames: [],
          headFrames: [],
        };
      }

      for (let frames = requestedFrames; frames >= 2; frames--) {
        const leftFrames = Math.floor(frames / 2);
        const rightFrames = frames - leftFrames;
        const bodyStartFrames: number[] = [];
        const bodyFrames: number[] = [];
        let feasible = true;

        for (let i = 0; i < n; i++) {
          const startFrame = i === 0 ? 0 : rightFrames;
          const endFrame = clipFrames[i] - (i < n - 1 ? leftFrames : 0);
          if (endFrame < startFrame) {
            feasible = false;
            break;
          }
          bodyStartFrames.push(startFrame);
          bodyFrames.push(endFrame - startFrame);
        }

        if (!feasible) {
          continue;
        }

        return {
          transitionFrames: frames,
          leftFrames,
          rightFrames,
          bodyStartFrames,
          bodyFrames,
          tailStartFrames: clips.slice(0, -1).map((_, index) => clipFrames[index] - leftFrames),
          headFrames: clips.slice(1).map(() => rightFrames),
        };
      }

      return {
        transitionFrames: 0,
        leftFrames: 0,
        rightFrames: 0,
        bodyStartFrames: new Array(n).fill(0),
        bodyFrames: [...clipFrames],
        tailStartFrames: [],
        headFrames: [],
      };
    };

    const plan = buildTransitionPlan(requestedTransitionFrames);
    const effectiveTransitionDuration = plan.transitionFrames / fps;
    const fpsHi = fps * sampleCount;
    const hiFrames = Math.max(2, plan.transitionFrames * sampleCount);
    const normalizedProgressExpr = (frames: number, frameVar: 'n' | 'N') =>
      frames > 1 ? `(${frameVar}/${frames - 1})` : '1';
    const smoothstepExpr = (xExpr: string, edge0: number, edge1: number) => {
      const edgeDelta = (edge1 - edge0).toFixed(6);
      const tExpr = `max(0,min(1,(((${xExpr})-${edge0.toFixed(6)})/${edgeDelta})))`;
      return `((${tExpr})*(${tExpr})*(3-2*(${tExpr})))`;
    };

    const hiScaleProgressExpr = normalizedProgressExpr(hiFrames, 'n');
    const hiBlendProgressExpr = normalizedProgressExpr(hiFrames, 'N');
    const loBlendProgressExpr = normalizedProgressExpr(Math.max(2, plan.transitionFrames), 'N');
    const hiScaleTpExpr = `max(0,min(1,((${hiScaleProgressExpr})-0.1)/0.8))`;
    const hiBlendTpExpr = `max(0,min(1,((${hiBlendProgressExpr})-0.1)/0.8))`;
    const loBlendTpExpr = `max(0,min(1,((${loBlendProgressExpr})-0.1)/0.8))`;
    const fadeInExpr = smoothstepExpr(loBlendProgressExpr, 0, 0.1);
    const fadeOutExpr = smoothstepExpr(loBlendProgressExpr, 0.9, 1.0);
    const aZoomExpr = `(1/(1+(${aTargetScaleDelta})*(${hiScaleTpExpr})))`;
    const bZoomExpr = `(1/(${bStartScaleBase}+(${bStartScaleDelta})*(${hiScaleTpExpr})))`;
    const bBlurMixExpr = `(${blurStrengthFixed}*(1-(${hiBlendTpExpr})))`;
    const aScaleWExpr = escapeExpr(`max(2,trunc(${W}*${aZoomExpr}/2)*2)`);
    const aScaleHExpr = escapeExpr(`max(2,trunc(${H}*${aZoomExpr}/2)*2)`);
    const bScaleWExpr = escapeExpr(`max(2,trunc(${W}*${bZoomExpr}/2)*2)`);
    const bScaleHExpr = escapeExpr(`max(2,trunc(${H}*${bZoomExpr}/2)*2)`);
    const bBgBlendExpr = escapeExpr(`A*(1-(${bBlurMixExpr}))+B*(${bBlurMixExpr})`);
    const midBlendExpr = escapeExpr(`A*(1-(${loBlendTpExpr}))+B*(${loBlendTpExpr})`);
    const fadeInBlendExpr = escapeExpr(`A*(1-(${fadeInExpr}))+B*(${fadeInExpr})`);
    const fadeOutBlendExpr = escapeExpr(`A*(1-(${fadeOutExpr}))+B*(${fadeOutExpr})`);

    if (requestedTransitionFrames !== plan.transitionFrames) {
      if (plan.transitionFrames > 0) {
        console.warn(
          `  [warn] transition reduced from ${formatSeconds(requestedTransitionFrames / fps)}s to ${formatSeconds(effectiveTransitionDuration)}s to keep the timeline frame-accurate.`,
        );
      } else if (n > 1) {
        console.warn(
          '  [warn] clips are too short for a centered overlap transition; falling back to hard cuts while preserving total duration.',
        );
      }
    }

    const inputArgs: string[] = [];
    const clipMeta: Array<{ videoIdx: number; duration: number }> = [];
    let inputCounter = 0;
    for (const clip of clips) {
      inputArgs.push('-i', clip.src);
      clipMeta.push({ videoIdx: inputCounter, duration: clip.duration });
      inputCounter++;
    }

    const filterParts: string[] = [];
    const segmentLabels: string[] = [];

    for (let i = 0; i < n; i++) {
      const bodyFrames = plan.bodyFrames[i];
      if (bodyFrames <= 0) {
        continue;
      }

      filterParts.push(
        `[${clipMeta[i].videoIdx}:v]trim=start_frame=${plan.bodyStartFrames[i]}:end_frame=${plan.bodyStartFrames[i] + bodyFrames},setpts=PTS-STARTPTS,fps=fps=${fps},format=rgba[body${i}]`,
      );
    }

    if (plan.transitionFrames > 0) {
      for (let i = 0; i < n - 1; i++) {
        const current = clipMeta[i];
        const next = clipMeta[i + 1];
        const tailTrim = `[${current.videoIdx}:v]trim=start_frame=${plan.tailStartFrames[i]}:end_frame=${clipFrames[i]},setpts=PTS-STARTPTS`;
        const headTrim = `[${next.videoIdx}:v]trim=start_frame=0:end_frame=${plan.headFrames[i]},setpts=PTS-STARTPTS`;

        filterParts.push(
          `${tailTrim}` +
          (plan.rightFrames > 0
            ? `,tpad=stop_mode=clone:stop=${plan.rightFrames}`
            : '') +
          `,fps=fps=${fps},format=rgba,split=2[arawbase${i}][arawwork${i}]`,
        );
        filterParts.push(
          `${headTrim}` +
          (plan.leftFrames > 0
            ? `,tpad=start_mode=clone:start=${plan.leftFrames}`
            : '') +
          `,fps=fps=${fps},format=rgba,split=3[brawbase${i}][brawsharp${i}][brawfg${i}]`,
        );

        filterParts.push(
          `[arawwork${i}]fps=fps=${fpsHi},scale='${aScaleWExpr}':'${aScaleHExpr}':eval=frame,crop=${W}:${H}:x='(in_w-out_w)/2':y='(in_h-out_h)/2',tmix=frames=${sampleCount},fps=fps=${fps},format=rgba[atrans${i}]`,
        );
        filterParts.push(
          `[brawsharp${i}]fps=fps=${fpsHi},split=2[bghisharp${i}][bgblurin${i}]`,
        );
        filterParts.push(
          `[bgblurin${i}]gblur=sigma=${bgBlurSigma}[bghiblur${i}]`,
        );
        filterParts.push(
          `[bghisharp${i}][bghiblur${i}]blend=all_expr='${bBgBlendExpr}'[bgmix${i}]`,
        );
        filterParts.push(
          `[brawfg${i}]fps=fps=${fpsHi},scale='${bScaleWExpr}':'${bScaleHExpr}':eval=frame[bfghi${i}]`,
        );
        filterParts.push(
          `[bgmix${i}][bfghi${i}]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,tmix=frames=${sampleCount},fps=fps=${fps},format=rgba[btrans${i}]`,
        );
        filterParts.push(
          `[atrans${i}][btrans${i}]blend=all_expr='${midBlendExpr}'[tmid${i}]`,
        );
        filterParts.push(
          `[arawbase${i}][tmid${i}]blend=all_expr='${fadeInBlendExpr}'[tstage${i}]`,
        );
        filterParts.push(
          `[tstage${i}][brawbase${i}]blend=all_expr='${fadeOutBlendExpr}',format=rgba[transition${i}]`,
        );
      }
    }

    const orderedSegmentLabels: string[] = [];
    for (let i = 0; i < n; i++) {
      if (plan.bodyFrames[i] > 0) {
        orderedSegmentLabels.push(`[body${i}]`);
      }
      if (plan.transitionFrames > 0 && i < n - 1) {
        orderedSegmentLabels.push(`[transition${i}]`);
      }
    }

    if (orderedSegmentLabels.length === 0) {
      filterParts.push(`[0:v]setpts=PTS-STARTPTS,fps=fps=${fps},format=rgba[vout]`);
    } else if (orderedSegmentLabels.length === 1) {
      filterParts.push(`${orderedSegmentLabels[0]}copy[vout]`);
    } else {
      filterParts.push(`${orderedSegmentLabels.join('')}concat=n=${orderedSegmentLabels.length}:v=1:a=0[vout]`);
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

    console.log(
      `\n[2/3] FFmpeg centered zoom-dissolve concat ${n} clips (transition ${formatSeconds(effectiveTransitionDuration)}s, blur ${blurStrengthFixed}, samples ${sampleCount}, A target ${aTargetScaleValue.toFixed(3)}, B start ${bStartScaleValue.toFixed(3)})...`,
    );
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
          `${request.templateId} requires FFmpeg. Install FFmpeg or set FFMPEG_PATH / FFPROBE_PATH.`, 
        );
      }

      if (providedVideoCount > 0 && videoInfo.size !== providedVideoCount) {
        throw new RenderError(
          `${request.templateId} could not read every input duration via ffprobe. Please verify the source media can be probed correctly.`, 
        );
      }
    }

    // Resolution: auto probes the first input video; otherwise use the requested preset.
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
          `  auto-detected resolution ${probed.width}x${probed.height} ${probed.fps}fps (source: ${path.basename(videoPath!)})`, 
        );
        resPreset = probed;
      } else {
        console.warn(`  [warn] failed to probe video metadata, falling back to douyin_vertical (1080x1920)`);
        resPreset = getResolutionPreset('douyin_vertical');
      }
    } else {
      resPreset = getResolutionPreset(request.preset);
    }

    const qualPreset = getQualityPreset(request.quality);

    // 鍒涘缓浠诲姟锛堝閮ㄤ紶鍏?taskId 鍒欒鐩栵級
    const task = createTask(request.templateId, request.variables);
    if (request.taskId) task.id = request.taskId;
    startTask(task);

    // 纭畾杈撳嚭璺緞
    const projectName = path.basename(request.projectDir);
    const outDir = path.join(this.outputDir, projectName);
    fs.mkdirSync(outDir, { recursive: true });
    const outFile = request.outputFilename ?? 'final.mp4';

    const startTime = Date.now();
    const cleanupFns: Array<() => void> = [];

    try {
      // 鈹€鈹€ trim-concat锛氱洿鎺ヨ蛋 FFmpeg锛屼笉缁忚繃 Revideo 鈹€鈹€
      if (request.templateId === 'trim-concat') {
        const TRIM_START = 2;

        console.log(`\n[1/3] Collecting and validating clips...`);

        const allClips = collectClips(request.variables, request.templateInfo, videoInfo);
        const clips: Array<{ key: string; src: string; duration: number }> = [];
        for (const clip of allClips) {
          if (clip.duration > 0 && clip.duration <= TRIM_START) {
            console.warn(`  [warn] skip ${clip.key}: duration ${clip.duration.toFixed(1)}s is not longer than ${TRIM_START}s`);
            continue;
          }
          clips.push(clip);
        }

        if (clips.length === 0) {
          throw new RenderError('No usable video clips remain after trim filtering.');
        }

        console.log(`  ${clips.length} clips, each skipping the first ${TRIM_START}s`);

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

        console.log(`[3/3] Writing metadata...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // 鈹€鈹€ xfade-concat锛氬墠娈垫贰鍑烘嫾鎺ワ紝鐩存帴璧?FFmpeg 鈹€鈹€
      if (request.templateId === 'xfade-concat') {
        console.log(`\n[1/3] Collecting and validating clips...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 0.5;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('No usable video clips were provided.');
        }

        console.log(`  ${clips.length} clips, fade-out transition ${transitionDuration}s`);

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

        console.log(`[3/3] Writing metadata...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // 鈹€鈹€ trim-xfade-concat锛氳澶?+ 鍓嶆娣″嚭鎷兼帴锛岀洿鎺ヨ蛋 FFmpeg 鈹€鈹€
      if (request.templateId === 'trim-xfade-concat') {
        console.log(`\n[1/3] Collecting and validating clips...`);

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
            console.warn(`  [warn] skip ${clip.key}: duration ${clip.duration.toFixed(1)}s is not longer than trim_start ${trimStart}s`);
            continue;
          }
          clips.push(clip);
        }

        if (clips.length === 0) {
          throw new RenderError('No usable video clips remain after trim filtering.');
        }

        console.log(`  ${clips.length} clips, trim_start ${trimStart}s, transition ${transitionDuration}s`);

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

        console.log(`[3/3] Writing metadata...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // 鈹€鈹€ zoom-dissolve-concat锛氫腑蹇冩媺杩?+ xfade 鍙犲寲 鈹€鈹€
      if (request.templateId === 'zoom-dissolve-concat') {
        console.log(`\n[1/3] Collecting and validating clips...`);

        const transitionDuration = typeof request.variables['transition_duration'] === 'number'
          ? request.variables['transition_duration']
          : 2.0;
        const blurStrength = typeof request.variables['blurStrength'] === 'number'
          ? request.variables['blurStrength']
          : 0.3;
        const numSamples = typeof request.variables['numSamples'] === 'number'
          ? request.variables['numSamples']
          : 10;
        const aTargetScale = typeof request.variables['aTargetScale'] === 'number'
          ? request.variables['aTargetScale']
          : 0.5;
        const bStartScale = typeof request.variables['bStartScale'] === 'number'
          ? request.variables['bStartScale']
          : 1.5;

        const clips = collectClips(request.variables, request.templateInfo, videoInfo);

        if (clips.length === 0) {
          throw new RenderError('No usable video clips were provided.');
        }

        console.log(
          `  共 ${clips.length} 个片段，转场 ${transitionDuration}s，blur ${blurStrength}，samples ${numSamples}，A target ${aTargetScale}，B start ${bStartScale}`,
        );

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
          blurStrength,
          numSamples,
          aTargetScale,
          bStartScale,
        );

        const elapsed = (Date.now() - startTime) / 1000;
        completeTask(task, outputPath);

        console.log(`[3/3] Writing metadata...`);
        await this.writeMeta(outDir, task, request, resPreset, elapsed);

        return {
          taskId: task.id,
          status: 'completed',
          outputPath,
          duration: elapsed,
        };
      }

      // 鈹€鈹€ 鍏朵粬妯℃澘锛氳蛋 Revideo 娓叉煋寮曟搸 鈹€鈹€
      console.log(`\n[1/3] Preparing render environment...`);

      // 鍔ㄦ€佸鍏?@revideo/renderer
      const { renderVideo } = await import('@revideo/renderer');

      console.log(`[2/3] Running Revideo renderer...`);

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

      console.log(`[3/3] Writing metadata...`);

      // 鍐欏叆 meta.json
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
