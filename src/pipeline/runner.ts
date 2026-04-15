import path from 'node:path';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
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
  completeTask,
  failTask,
  updateProgress,
} from '../render/task.js';
import { normalizeClips } from '../render/transitions/shared.js';
import type { RenderResult } from '../render/types.js';
import type { QualityPreset } from '../presets.js';
import type {
  ResolvedPipelineClip,
  PipelineTransitionConfig,
} from './types.js';
import type { ParsedPipelineContext } from './config.js';

// ─── ffprobe helper ───────────────────────────────────────────────────────────

function probeSingleVideo(
  ffprobePath: string,
  videoPath: string,
): {
  duration: number;
  width: number;
  height: number;
  fps: number;
} {
  const raw = execFileSync(
    ffprobePath,
    [
      '-v', 'error', '-print_format', 'json',
      '-show_streams', '-show_format', '-select_streams', 'v:0',
      videoPath,
    ],
    { encoding: 'utf-8', timeout: 10000 },
  );
  const info = JSON.parse(raw) as {
    streams?: Array<{
      width?: number;
      height?: number;
      r_frame_rate?: string;
      duration?: string;
    }>;
    format?: { duration?: string };
  };
  const s = info.streams?.[0];
  const duration = Number(s?.duration ?? info.format?.duration ?? 0);
  const [num, den] = (s?.r_frame_rate ?? '30/1').split('/').map(Number);
  return {
    duration: Number.isFinite(duration) ? duration : 0,
    width: s?.width ?? 0,
    height: s?.height ?? 0,
    fps: den ? Math.round(num / den) : num || 30,
  };
}

// ─── FFmpeg filter_complex builder ────────────────────────────────────────────

/**
 * Generalised trim-mixed-concat with per-clip trim and per-junction transition duration.
 *
 * Input args layout: for each clip i:
 *   -ss {trimStart[i]}          (skipped if 0)
 *   -t  {effectiveDuration[i]}  (skipped if trimEnd is 0)
 *   -i  {clip[i].src}
 *
 * The filter_complex is structurally identical to ffmpegTrimMixedConcat
 * (src/render/transitions/trim-mixed-concat.ts) but uses:
 *   - effectiveDuration[i] per clip instead of a global (probedDuration - trimStart)
 *   - junctions[i].duration per junction instead of a shared transitionDuration
 */
function ffmpegPipelineConcat(
  ffmpegPath: string,
  clips: ResolvedPipelineClip[],
  junctions: PipelineTransitionConfig[],
  outputPath: string,
  qualPreset: QualityPreset,
  resPreset: ResolutionPreset,
  task: ReturnType<typeof createTask>,
): void {
  const n = clips.length;
  const fps = resPreset.fps;
  const frameDuration = 1 / fps;
  const minSeg = frameDuration / 2;
  const fmt = (v: number) => v.toFixed(6);

  // Per-clip -ss and -t input options
  const inputArgs: string[] = [];
  for (const clip of clips) {
    if (clip.trimStart > 0) inputArgs.push('-ss', String(clip.trimStart));
    if (clip.trimEnd > 0)   inputArgs.push('-t',  String(clip.effectiveDuration));
    inputArgs.push('-i', clip.src);
  }

  // Single-clip fast path
  if (n === 1) {
    execFileSync(
      ffmpegPath,
      [
        ...inputArgs,
        '-filter_complex', `[0:v]setpts=PTS-STARTPTS,fps=fps=${fps}[vout]`,
        '-map', '[vout]',
        '-c:v', 'libx264', '-preset', qualPreset.ffmpegPreset,
        '-crf', String(qualPreset.crf), '-pix_fmt', 'yuv420p', '-an',
        '-y', outputPath,
      ],
      { timeout: 600_000 },
    );
    updateProgress(task, 1);
    return;
  }

  const durations = clips.map((c) => c.effectiveDuration);

  // Compute per-clip head/tail consumed by bordering junctions
  const tailConsumed = new Array<number>(n).fill(0);
  const headConsumed = new Array<number>(n).fill(0);

  for (let j = 0; j < junctions.length; j++) {
    const { type: t, duration: D } = junctions[j];
    const halfD = D / 2;
    const dj = durations[j];
    const djn = durations[j + 1];

    if (t === 'flash-black') {
      const eff = Math.min(halfD, dj / 2, djn / 2);
      if (eff < halfD - 0.001) {
        console.warn(
          `  ⚠ 连接点 ${j + 1} (flash-black) 附近片段时长不足，闪黑已收缩至 ${(eff * 2).toFixed(2)}s`,
        );
      }
      tailConsumed[j] = eff;
      headConsumed[j + 1] = eff;
    } else if (t === 'dissolve') {
      const eff = Math.min(D, dj / 2);
      if (eff < D - 0.001) {
        console.warn(
          `  ⚠ 连接点 ${j + 1} (dissolve) 片段 ${clips[j].key} 时长不足，叠化已收缩至 ${eff.toFixed(2)}s`,
        );
      }
      tailConsumed[j] = eff;
      // dissolve does not consume next clip's head
    }
    // cut: no consumption
  }

  const filterParts: string[] = [];
  const segmentLabels: string[] = [];

  for (let i = 0; i < n; i++) {
    const d = durations[i];
    const bodyStart = headConsumed[i];
    const bodyDuration = d - headConsumed[i] - tailConsumed[i];

    if (bodyDuration > minSeg) {
      filterParts.push(
        `[${i}:v]trim=start=${fmt(bodyStart)}:duration=${fmt(bodyDuration)},setpts=PTS-STARTPTS,fps=fps=${fps}[body${i}]`,
      );
      segmentLabels.push(`[body${i}]`);
    } else if (bodyDuration > 0) {
      console.warn(`  ⚠ 片段 ${clips[i].key} 主体过短（${bodyDuration.toFixed(3)}s），已跳过`);
    }

    if (i < n - 1) {
      const { type: t, duration: D } = junctions[i];
      const halfD = D / 2;
      void halfD;

      if (t === 'flash-black') {
        const eff = tailConsumed[i];
        filterParts.push(
          `[${i}:v]trim=start=${fmt(d - eff)}:duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `fade=t=out:st=0:d=${fmt(eff)},fps=fps=${fps}[fb_tail${i}]`,
        );
        segmentLabels.push(`[fb_tail${i}]`);
        filterParts.push(
          `[${i + 1}:v]trim=duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `fade=t=in:st=0:d=${fmt(eff)},fps=fps=${fps}[fb_head${i + 1}]`,
        );
        segmentLabels.push(`[fb_head${i + 1}]`);
      } else if (t === 'dissolve') {
        const eff = tailConsumed[i];
        const stillDuration = Math.max(0, eff - frameDuration);
        filterParts.push(
          `[${i}:v]trim=start=${fmt(d - eff)}:duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `format=rgba,fade=t=out:st=0:d=${fmt(eff)}:alpha=1,fps=fps=${fps}[diss_tail${i}]`,
        );
        filterParts.push(
          `[${i + 1}:v]trim=end_frame=1,setpts=PTS-STARTPTS,` +
          `tpad=stop_mode=clone:stop_duration=${fmt(stillDuration)},fps=fps=${fps}[diss_still${i}]`,
        );
        filterParts.push(
          `[diss_still${i}][diss_tail${i}]overlay=eof_action=pass:shortest=1,format=yuv420p,fps=fps=${fps}[diss_trans${i}]`,
        );
        segmentLabels.push(`[diss_trans${i}]`);
      }
      // cut: nothing added
    }
  }

  if (segmentLabels.length === 0) {
    throw new RenderError('没有可用的视频段（所有片段时长过短）');
  }

  filterParts.push(
    `${segmentLabels.join('')}concat=n=${segmentLabels.length}:v=1:a=0[vout]`,
  );

  const filterComplex = filterParts.join(';');
  const junctionDesc = junctions.map((j) => `${j.type}(${j.duration}s)`).join(' → ');
  console.log(`\n[2/3] FFmpeg Pipeline 拼接 ${n} 段（转场: ${junctionDesc}）...`);

  execFileSync(
    ffmpegPath,
    [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-c:v', 'libx264', '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf), '-pix_fmt', 'yuv420p', '-an',
      '-y', outputPath,
    ],
    { timeout: 600_000 },
  );
  updateProgress(task, 1);
}

// ─── PipelineRunner ───────────────────────────────────────────────────────────

export class PipelineRunner {
  private outputDir: string;

  constructor(private rootDir: string) {
    this.outputDir = path.join(rootDir, 'output');
  }

  async run(
    ctx: ParsedPipelineContext,
    ffmpegPath: string,
    ffprobePath: string,
    overrides: { preset?: string; quality?: string },
  ): Promise<RenderResult> {
    const { config, projectDir, configPath, resolvedSrcs, junctions } = ctx;

    const preset = overrides.preset ?? config.preset ?? AUTO_PRESET;
    const quality = overrides.quality ?? config.quality ?? 'high';
    const outputFilename = config.output?.filename ?? 'final.mp4';
    const projectName = path.basename(projectDir);

    const task = createTask('pipeline', {});
    startTask(task);

    const startTime = Date.now();
    const outDir = path.join(this.outputDir, projectName);
    fs.mkdirSync(outDir, { recursive: true });
    const outputPath = path.join(outDir, outputFilename);

    let cleanup: (() => void) | null = null;

    try {
      // ── 1. Probe each clip ──────────────────────────────────────────────
      console.log(`\n[1/3] 探测并校验 ${resolvedSrcs.length} 个片段...`);
      let resPreset: ResolutionPreset | null = null;

      const resolvedClips: ResolvedPipelineClip[] = resolvedSrcs.map((src, i) => {
        const clipCfg = config.clips[i];
        const trimStart = clipCfg.trim_start ?? 0;
        const trimEnd = clipCfg.trim_end ?? 0;
        const probed = probeSingleVideo(ffprobePath, src);
        const effectiveDuration = Math.max(0, probed.duration - trimStart - trimEnd);

        if (effectiveDuration <= 0) {
          throw new RenderError(
            `clips[${i}] (${path.basename(src)}): trim 后有效时长 ≤ 0，` +
            `探测到 ${probed.duration.toFixed(2)}s，trim_start=${trimStart}s，trim_end=${trimEnd}s`,
          );
        }
        console.log(
          `  clip_${i + 1}: ${path.basename(src)} ` +
          `(探测 ${probed.duration.toFixed(2)}s, 有效 ${effectiveDuration.toFixed(2)}s)`,
        );

        // Capture first clip's resolution for auto preset
        if (i === 0 && preset === AUTO_PRESET && probed.width && probed.height) {
          resPreset = {
            width: probed.width,
            height: probed.height,
            fps: probed.fps,
            label: `自动探测 ${probed.width}x${probed.height} ${probed.fps}fps`,
          };
          console.log(`  自动探测分辨率 ${probed.width}x${probed.height} ${probed.fps}fps`);
        }

        return {
          key: `clip_${i + 1}`,
          src,
          probedDuration: probed.duration,
          trimStart,
          trimEnd,
          effectiveDuration,
        };
      });

      // ── 2. Finalize resolution preset ───────────────────────────────────
      if (preset !== AUTO_PRESET) {
        resPreset = getResolutionPreset(preset);
      } else if (!resPreset) {
        console.warn('  ⚠ 无法探测分辨率，回退到 douyin_vertical (1080x1920)');
        resPreset = getResolutionPreset('douyin_vertical');
      }
      const qualPreset = getQualityPreset(quality);

      // ── 3. Normalize clips (scale/pad/fps) to temp files ────────────────
      // normalizeClips does NOT apply trim. Trimming happens in ffmpegPipelineConcat
      // via per-clip -ss / -t input options on the normalized temp files.
      const videoClips = resolvedClips.map((c) => ({
        key: c.key,
        src: c.src,
        duration: c.probedDuration,
      }));

      const normalized = normalizeClips(
        this.rootDir,
        ffmpegPath,
        videoClips,
        qualPreset,
        resPreset!,
      );
      cleanup = normalized.cleanup;

      // Re-attach trim params onto the normalized (temp) srcs
      const normalizedPipelineClips: ResolvedPipelineClip[] = normalized.clips.map(
        (nc, i) => ({ ...resolvedClips[i], src: nc.src }),
      );

      // ── 4. Build filter_complex and run FFmpeg ──────────────────────────
      ffmpegPipelineConcat(
        ffmpegPath,
        normalizedPipelineClips,
        junctions,
        outputPath,
        qualPreset,
        resPreset!,
        task,
      );

      // ── 5. Write meta and return ────────────────────────────────────────
      const elapsed = (Date.now() - startTime) / 1000;
      completeTask(task, outputPath);

      const meta = {
        taskId: task.id,
        mode: 'pipeline',
        preset,
        quality,
        configPath,
        clips: resolvedClips.map((c) => ({
          src: c.src,
          trimStart: c.trimStart,
          trimEnd: c.trimEnd,
          effectiveDuration: c.effectiveDuration,
        })),
        junctions,
        renderedAt: new Date().toISOString(),
        duration: Math.round(elapsed * 10) / 10,
        resolution: `${resPreset!.width}x${resPreset!.height}`,
      };
      fs.writeFileSync(
        path.join(outDir, 'meta.json'),
        JSON.stringify(meta, null, 2),
        'utf-8',
      );

      console.log(`\n[3/3] 归档产物...`);
      return { taskId: task.id, status: 'completed', outputPath, duration: elapsed };
    } catch (err) {
      const elapsed = (Date.now() - startTime) / 1000;
      const errMsg = err instanceof Error ? err.message : String(err);
      failTask(task, errMsg);
      return { taskId: task.id, status: 'failed', duration: elapsed, error: errMsg };
    } finally {
      if (cleanup) {
        try { cleanup(); } catch { /* ignore */ }
      }
    }
  }
}
