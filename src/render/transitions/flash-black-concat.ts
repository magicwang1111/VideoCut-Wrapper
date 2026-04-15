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

export function ffmpegFlashBlackConcat(
  ffmpegPath: string,
  clips: VideoClip[],
  outputPath: string,
  qualPreset: QualityPreset,
  task: RenderTask,
  resPreset: ResolutionPreset,
  transitionDuration: number,
): void {
  const n = clips.length;
  const halfD = transitionDuration / 2;
  const fps = resPreset.fps;
  const formatSeconds = (value: number) => value.toFixed(6);

  const inputArgs: string[] = [];
  for (const clip of clips) {
    inputArgs.push('-i', clip.src);
  }

  if (n === 1) {
    const filterComplex = `[0:v]setpts=PTS-STARTPTS[vout]`;
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
    return;
  }

  const filterParts: string[] = [];

  for (let i = 0; i < n; i++) {
    const clip = clips[i];
    const duration = clip.duration;
    const isFirst = i === 0;
    const isLast = i === n - 1;

    const effectiveHalfD = Math.min(halfD, duration / 2);
    if (effectiveHalfD < halfD) {
      console.warn(
        `  ⚠ 片段 ${clip.key} 时长 ${duration.toFixed(2)}s 不足，淡黑时长已自动收缩至 ${(effectiveHalfD * 2).toFixed(2)}s`,
      );
    }

    const fadeOutStart = Math.max(0, duration - effectiveHalfD);
    let chain = `[${i}:v]setpts=PTS-STARTPTS`;

    if (!isFirst) {
      // fade in from black at the start
      chain += `,fade=t=in:st=0:d=${formatSeconds(effectiveHalfD)}`;
    }
    if (!isLast) {
      // fade out to black at the end
      chain += `,fade=t=out:st=${formatSeconds(fadeOutStart)}:d=${formatSeconds(effectiveHalfD)}`;
    }

    chain += `[v${i}]`;
    filterParts.push(chain);
  }

  const concatInputs = Array.from({ length: n }, (_, i) => `[v${i}]`).join('');
  filterParts.push(`${concatInputs}concat=n=${n}:v=1:a=0[vout]`);

  const filterComplex = filterParts.join(';');

  console.log(`\n[2/3] FFmpeg 闪黑拼接 ${n} 个片段（转场 ${transitionDuration}s，每侧 ${halfD}s）...`);
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

export function handleFlashBlackConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const { rootDir, ffmpegPath, ffprobePath, request, videoInfo, qualPreset, resPreset, task, outDir, outFile } = args;

  void ffprobePath;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const transitionDuration = typeof request.variables.transition_duration === 'number'
    ? request.variables.transition_duration
    : 0.5;

  const clips = collectClips(request.variables, request.templateInfo, videoInfo);
  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（未提供任何视频）');
  }

  console.log(`  共 ${clips.length} 个片段，闪黑转场时长 ${transitionDuration}s（每侧 ${(transitionDuration / 2).toFixed(2)}s）`);

  const normalized = normalizeClips(
    rootDir,
    ffmpegPath,
    clips,
    qualPreset,
    resPreset,
  );

  const outputPath = path.join(outDir, outFile);
  ffmpegFlashBlackConcat(
    ffmpegPath,
    normalized.clips,
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
