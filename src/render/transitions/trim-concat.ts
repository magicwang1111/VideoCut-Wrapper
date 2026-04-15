import path from 'node:path';
import { RenderError } from '../../errors.js';
import {
  collectClips,
  ffmpegTrimConcat,
  normalizeClips,
  type TransitionHandlerArgs,
  type TransitionHandlerResult,
} from './shared.js';

export function handleTrimConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const { rootDir, ffmpegPath, ffprobePath, request, videoInfo, qualPreset, resPreset, task, outDir, outFile } = args;
  const trimStart = 2;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const allClips = collectClips(request.variables, request.templateInfo, videoInfo);
  const clips = allClips.filter((clip) => {
    if (clip.duration > 0 && clip.duration <= trimStart) {
      console.warn(`  ⚠ 跳过 ${clip.key}：时长 ${clip.duration.toFixed(1)}s 不足 ${trimStart}s`);
      return false;
    }
    return true;
  });

  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（全部片段时长均不足 2 秒或未提供）');
  }

  console.log(`  共 ${clips.length} 个片段，每段跳过前 ${trimStart}s`);

  const normalized = normalizeClips(
    rootDir,
    ffmpegPath,
    clips,
    qualPreset,
    resPreset,
  );

  const outputPath = path.join(outDir, outFile);
  ffmpegTrimConcat(
    rootDir,
    ffmpegPath,
    ffprobePath,
    normalized.clips,
    outputPath,
    qualPreset,
    task,
    trimStart,
  );

  return {
    cleanup: normalized.cleanup,
    outputPath,
  };
}
