import path from 'node:path';
import { RenderError } from '../../errors.js';
import { ffmpegXfadeConcat } from './xfade-concat.js';
import {
  collectClips,
  normalizeClips,
  type TransitionHandlerArgs,
  type TransitionHandlerResult,
} from './shared.js';

export function handleTrimXfadeConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const { rootDir, ffmpegPath, ffprobePath, request, videoInfo, qualPreset, resPreset, task, outDir, outFile } = args;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const trimStart = typeof request.variables.trim_start === 'number'
    ? request.variables.trim_start
    : 2;
  const transitionDuration = typeof request.variables.transition_duration === 'number'
    ? request.variables.transition_duration
    : 0.5;

  const allClips = collectClips(request.variables, request.templateInfo, videoInfo);
  const clips = allClips.filter((clip) => {
    if (clip.duration > 0 && clip.duration <= trimStart) {
      console.warn(`  ⚠ 跳过 ${clip.key}：时长 ${clip.duration.toFixed(1)}s 不足 ${trimStart}s`);
      return false;
    }
    return true;
  });

  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（全部片段时长均不足裁剪长度或未提供）');
  }

  console.log(`  共 ${clips.length} 个片段，裁去前 ${trimStart}s，前段淡出转场时长 ${transitionDuration}s`);

  const normalized = normalizeClips(
    rootDir,
    ffmpegPath,
    clips,
    qualPreset,
    resPreset,
  );

  const outputPath = path.join(outDir, outFile);
  ffmpegXfadeConcat(
    ffmpegPath,
    ffprobePath,
    normalized.clips,
    outputPath,
    qualPreset,
    task,
    resPreset,
    transitionDuration,
    trimStart,
  );

  return {
    cleanup: normalized.cleanup,
    outputPath,
  };
}
