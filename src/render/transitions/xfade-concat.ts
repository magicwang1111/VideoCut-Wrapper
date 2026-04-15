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

export function ffmpegXfadeConcat(
  ffmpegPath: string,
  ffprobePath: string,
  clips: VideoClip[],
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

  const ffmpegArgs: string[] = [
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
  execFileSync(ffmpegPath, ffmpegArgs, { timeout: 600_000 });

  updateProgress(task, 1);
}

export function handleXfadeConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const { rootDir, ffmpegPath, ffprobePath, request, videoInfo, qualPreset, resPreset, task, outDir, outFile } = args;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const transitionDuration = typeof request.variables.transition_duration === 'number'
    ? request.variables.transition_duration
    : 0.5;

  const clips = collectClips(request.variables, request.templateInfo, videoInfo);
  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（未提供任何视频）');
  }

  console.log(`  共 ${clips.length} 个片段，前段淡出转场时长 ${transitionDuration}s`);

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
  );

  return {
    cleanup: normalized.cleanup,
    outputPath,
  };
}
