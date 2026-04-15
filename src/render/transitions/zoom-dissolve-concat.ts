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

export function ffmpegZoomDissolveConcat(
  ffmpegPath: string,
  clips: VideoClip[],
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

  const oversample = 8;
  const fpsHi = fps * oversample;
  const framesHi = Math.max(2, Math.round(fpsHi * D));
  const zDelta = (zoomScale - 1).toFixed(6);
  const frameProgress = `(n/${framesHi - 1})`;
  const easedProgress = `(${frameProgress}*${frameProgress}*${frameProgress}*${frameProgress})`;
  const zoomExpr = `(1+${zDelta}*${easedProgress})`;
  const scaleW = `floor(${W}*${zoomExpr}/2)*2`;
  const scaleH = `floor(${H}*${zoomExpr}/2)*2`;
  const cropX = `${W}*${zDelta}*${easedProgress}/2`;
  const cropY = `${H}*${zDelta}*${easedProgress}/2`;

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
        `,fps=fps=${fpsHi}` +
        `,scale='${scaleW}':'${scaleH}':eval=frame` +
        `,crop=${W}:${H}:x='${cropX}':y='${cropY}'` +
        `,tmix=frames=${oversample}` +
        `,fps=fps=${fps}` +
        `,format=rgba,fade=t=out:st=${formatSeconds(D * 0.65)}:d=${formatSeconds(D * 0.35)}:alpha=1[zoomfade${i}]`,
      );
      filterParts.push(
        `[${next.videoIdx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=${formatSeconds(stillPadDuration)},fps=fps=${fps}[still${i}]`,
      );
      filterParts.push(
        `[still${i}][zoomfade${i}]overlay=eof_action=pass:shortest=1,format=yuv420p,fps=fps=${fps}[transition${i}]`,
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

  console.log(`\n[2/3] FFmpeg 中心拉近叠化拼接 ${n} 个片段（拉近 ${D}s，放大 ${zoomScale}x）...`);
  execFileSync(ffmpegPath, ffmpegArgs, { timeout: 600_000 });

  updateProgress(task, 1);
}

export function handleZoomDissolveConcat(
  args: TransitionHandlerArgs,
): TransitionHandlerResult {
  const { rootDir, ffmpegPath, request, videoInfo, qualPreset, resPreset, task, outDir, outFile } = args;

  console.log(`\n[1/3] 收集并校验视频片段...`);

  const transitionDuration = typeof request.variables.transition_duration === 'number'
    ? request.variables.transition_duration
    : 0.4;
  const zoomScale = typeof request.variables.zoom_scale === 'number'
    ? request.variables.zoom_scale
    : 1.18;

  const clips = collectClips(request.variables, request.templateInfo, videoInfo);
  if (clips.length === 0) {
    throw new RenderError('没有可用的视频片段（未提供任何视频）');
  }

  console.log(`  共 ${clips.length} 个片段，中心拉近时长 ${transitionDuration}s，放大倍数 ${zoomScale}x`);

  const normalized = normalizeClips(
    rootDir,
    ffmpegPath,
    clips,
    qualPreset,
    resPreset,
  );

  const outputPath = path.join(outDir, outFile);
  ffmpegZoomDissolveConcat(
    ffmpegPath,
    normalized.clips,
    outputPath,
    qualPreset,
    task,
    resPreset,
    transitionDuration,
    zoomScale,
  );

  return {
    cleanup: normalized.cleanup,
    outputPath,
  };
}
