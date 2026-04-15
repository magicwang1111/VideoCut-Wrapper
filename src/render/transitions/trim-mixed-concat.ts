import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { RenderError } from '../../errors.js';
import { updateProgress } from '../task.js';
import type { QualityPreset, ResolutionPreset } from '../../presets.js';
import type { RenderTask } from '../task.js';
import type { VideoClip } from '../types.js';
import {
  collectClips,
  normalizeClips,
  type TransitionHandlerArgs,
  type TransitionHandlerResult,
} from './shared.js';

export type JunctionType = 'flash-black' | 'dissolve' | 'cut';

export function parseJunctionType(raw: unknown): JunctionType {
  if (raw === 'flash-black' || raw === 'dissolve' || raw === 'cut') return raw;
  return 'cut';
}

/**
 * 混合转场 filter_complex 构建：
 *
 * 每个 clip 拆成三部分（视情况有或无）：
 *   head  — 前段 half_D 的淡入段（仅当上一个 junction 是 flash-black）
 *   body  — 主体段（head..tail 之间）
 *   tail  — 末尾转场段（取决于下一个 junction 类型）
 *
 * 连接点段：
 *   flash-black → [fb_tail_i]  [fb_head_{i+1}]  （两段独立，依次拼接）
 *   dissolve    → [diss_trans_i]                 （前段尾叠后段首帧，overlay）
 *   cut         → （无额外段）
 */
export function ffmpegTrimMixedConcat(
  ffmpegPath: string,
  clips: VideoClip[],
  junctions: JunctionType[],
  trimStart: number,
  outputPath: string,
  qualPreset: QualityPreset,
  task: RenderTask,
  resPreset: ResolutionPreset,
  transitionDuration: number,
): void {
  const n = clips.length;
  const D = transitionDuration;
  const halfD = D / 2;
  const fps = resPreset.fps;
  const frameDuration = 1 / fps;
  const minSeg = frameDuration / 2;
  const fmt = (v: number) => v.toFixed(6);

  // 每个 input 加 -ss trimStart 跳过开头
  const inputArgs: string[] = [];
  for (const clip of clips) {
    if (trimStart > 0) inputArgs.push('-ss', String(trimStart));
    inputArgs.push('-i', clip.src);
  }

  // trim 后的有效时长
  const durations = clips.map((c) => Math.max(0, c.duration - trimStart));

  // 单段直通
  if (n === 1) {
    execFileSync(
      ffmpegPath,
      [
        ...inputArgs,
        '-filter_complex', `[0:v]setpts=PTS-STARTPTS,fps=fps=${fps}[vout]`,
        '-map', '[vout]',
        '-c:v', 'libx264',
        '-preset', qualPreset.ffmpegPreset,
        '-crf', String(qualPreset.crf),
        '-pix_fmt', 'yuv420p',
        '-an',
        '-y', outputPath,
      ],
      { timeout: 600_000 },
    );
    updateProgress(task, 1);
    return;
  }

  // 计算每段首尾被转场占用的时长
  const tailConsumed: number[] = new Array(n).fill(0); // 末尾被出口转场占用
  const headConsumed: number[] = new Array(n).fill(0); // 开头被入口转场占用

  for (let j = 0; j < junctions.length; j++) {
    const t = junctions[j];
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
      // dissolve 不占用下一段的开头
    }
    // cut: 不占用任何时长
  }

  const filterParts: string[] = [];
  const segmentLabels: string[] = [];

  for (let i = 0; i < n; i++) {
    const d = durations[i];
    const bodyStart = headConsumed[i];
    const bodyDuration = d - headConsumed[i] - tailConsumed[i];

    // 主体段
    if (bodyDuration > minSeg) {
      filterParts.push(
        `[${i}:v]trim=start=${fmt(bodyStart)}:duration=${fmt(bodyDuration)},setpts=PTS-STARTPTS,fps=fps=${fps}[body${i}]`,
      );
      segmentLabels.push(`[body${i}]`);
    } else if (bodyDuration > 0) {
      console.warn(`  ⚠ 片段 ${clips[i].key} 主体过短（${bodyDuration.toFixed(3)}s），已跳过`);
    }

    // 出口转场段（在 body 之后）
    if (i < n - 1) {
      const t = junctions[i];

      if (t === 'flash-black') {
        const eff = tailConsumed[i];

        // 当前段尾部：淡出至黑
        filterParts.push(
          `[${i}:v]trim=start=${fmt(d - eff)}:duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `fade=t=out:st=0:d=${fmt(eff)},fps=fps=${fps}[fb_tail${i}]`,
        );
        segmentLabels.push(`[fb_tail${i}]`);

        // 下一段头部：从黑淡入
        filterParts.push(
          `[${i + 1}:v]trim=duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `fade=t=in:st=0:d=${fmt(eff)},fps=fps=${fps}[fb_head${i + 1}]`,
        );
        segmentLabels.push(`[fb_head${i + 1}]`);

      } else if (t === 'dissolve') {
        const eff = tailConsumed[i];
        const stillDuration = Math.max(0, eff - frameDuration);

        // 当前段尾部：alpha 淡出
        filterParts.push(
          `[${i}:v]trim=start=${fmt(d - eff)}:duration=${fmt(eff)},setpts=PTS-STARTPTS,` +
          `format=rgba,fade=t=out:st=0:d=${fmt(eff)}:alpha=1,fps=fps=${fps}[diss_tail${i}]`,
        );

        // 下一段首帧静止垫底
        filterParts.push(
          `[${i + 1}:v]trim=end_frame=1,setpts=PTS-STARTPTS,` +
          `tpad=stop_mode=clone:stop_duration=${fmt(stillDuration)},fps=fps=${fps}[diss_still${i}]`,
        );

        // 叠化：静帧在下，淡出段在上
        filterParts.push(
          `[diss_still${i}][diss_tail${i}]overlay=eof_action=pass:shortest=1,format=yuv420p,fps=fps=${fps}[diss_trans${i}]`,
        );
        segmentLabels.push(`[diss_trans${i}]`);
      }
      // cut: 无额外段
    }
  }

  if (segmentLabels.length === 0) {
    throw new RenderError('没有可用的视频段（所有片段时长过短）');
  }

  filterParts.push(
    `${segmentLabels.join('')}concat=n=${segmentLabels.length}:v=1:a=0[vout]`,
  );

  const filterComplex = filterParts.join(';');
  const junctionDesc = junctions.join(' → ');

  console.log(
    `\n[2/3] FFmpeg 混合转场拼接 ${n} 段（剪头 ${trimStart}s，转场: ${junctionDesc}）...`,
  );

  execFileSync(
    ffmpegPath,
    [
      ...inputArgs,
      '-filter_complex', filterComplex,
      '-map', '[vout]',
      '-c:v', 'libx264',
      '-preset', qualPreset.ffmpegPreset,
      '-crf', String(qualPreset.crf),
      '-pix_fmt', 'yuv420p',
      '-an',
      '-y', outputPath,
    ],
    { timeout: 600_000 },
  );

  updateProgress(task, 1);
}

export function handleTrimMixedConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const {
    rootDir, ffmpegPath, ffprobePath, request, videoInfo,
    qualPreset, resPreset, task, outDir, outFile,
  } = args;

  void ffprobePath;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const trimStart = typeof request.variables.trim_start === 'number'
    ? request.variables.trim_start
    : 2;

  const transitionDuration = typeof request.variables.transition_duration === 'number'
    ? request.variables.transition_duration
    : 0.5;

  const clips = collectClips(request.variables, request.templateInfo, videoInfo);
  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（未提供任何视频）');
  }

  // 按连接点顺序读取转场类型（transition_1 = clip1→clip2，以此类推）
  const junctions: JunctionType[] = [];
  for (let j = 1; j < clips.length; j++) {
    junctions.push(parseJunctionType(request.variables[`transition_${j}`]));
  }

  console.log(
    `  共 ${clips.length} 个片段，剪头 ${trimStart}s，转场: [${junctions.join(', ')}]，时长 ${transitionDuration}s`,
  );

  const normalized = normalizeClips(rootDir, ffmpegPath, clips, qualPreset, resPreset);

  const outputPath = path.join(outDir, outFile);
  ffmpegTrimMixedConcat(
    ffmpegPath,
    normalized.clips,
    junctions,
    trimStart,
    outputPath,
    qualPreset,
    task,
    resPreset,
    transitionDuration,
  );

  return {
    cleanup: normalized.cleanup,
    outputPath,
  };
}
