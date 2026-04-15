import path from 'node:path';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import type { TemplateInfo } from '../../registry/index.js';
import type { QualityPreset, ResolutionPreset } from '../../presets.js';
import { updateProgress, type RenderTask } from '../task.js';
import type { ProbedVideoInfo, RenderRequest, VideoClip } from '../types.js';

export interface TransitionHandlerArgs {
  rootDir: string;
  ffmpegPath: string;
  ffprobePath: string;
  request: RenderRequest;
  videoInfo: Map<string, ProbedVideoInfo>;
  qualPreset: QualityPreset;
  resPreset: ResolutionPreset;
  task: RenderTask;
  outDir: string;
  outFile: string;
}

export interface TransitionHandlerResult {
  cleanup: () => void;
  outputPath: string;
}

export function collectClips(
  variables: Record<string, unknown>,
  templateInfo: TemplateInfo,
  videoInfo: Map<string, ProbedVideoInfo>,
): VideoClip[] {
  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video_list') continue;
    const list = variables[key];
    if (!Array.isArray(list)) continue;
    const durations = (variables[`${key}_source_durations`] as number[] | undefined) ?? [];
    return list.map((src, index) => ({
      key: `clip_${index + 1}`,
      src: src as string,
      duration: durations[index] ?? 0,
    }));
  }

  const clips: VideoClip[] = [];
  for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
    if (def.type !== 'video') continue;
    const src = variables[key];
    if (typeof src !== 'string' || !src.trim()) continue;
    const info = videoInfo.get(key);
    clips.push({ key, src, duration: info?.duration ?? 0 });
  }
  return clips;
}

export function buildNormalizeVideoFilter(resPreset: ResolutionPreset): string {
  return [
    `fps=${resPreset.fps}`,
    `scale=${resPreset.width}:${resPreset.height}:force_original_aspect_ratio=decrease:flags=lanczos`,
    `pad=${resPreset.width}:${resPreset.height}:(ow-iw)/2:(oh-ih)/2:color=black`,
    'setsar=1',
    'format=yuv420p',
  ].join(',');
}

export function normalizeClips(
  rootDir: string,
  ffmpegPath: string,
  clips: VideoClip[],
  qualPreset: QualityPreset,
  resPreset: ResolutionPreset,
): {
  clips: VideoClip[];
  cleanup: () => void;
} {
  const tempDir = path.join(rootDir, 'temp');
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
  } catch (error) {
    cleanup();
    throw error;
  }
}

export function ffmpegTrimConcat(
  rootDir: string,
  ffmpegPath: string,
  ffprobePath: string,
  clips: VideoClip[],
  outputPath: string,
  qualPreset: QualityPreset,
  task: RenderTask,
  trimStart: number,
): void {
  const tempDir = path.join(rootDir, 'temp');
  fs.mkdirSync(tempDir, { recursive: true });

  const sessionId = Date.now().toString(36);
  const tempFiles: string[] = [];

  try {
    void ffprobePath;

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

    const listFile = path.join(tempDir, `concat_${sessionId}.txt`);
    const listContent = tempFiles
      .map((file) => `file '${file.replace(/\\/g, '/')}'`)
      .join('\n');
    fs.writeFileSync(listFile, listContent, 'utf-8');

    console.log(`  拼接 ${clips.length} 个片段 -> ${path.basename(outputPath)}`);
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

    try {
      fs.unlinkSync(listFile);
    } catch {
      // ignore temp cleanup errors
    }
  } finally {
    for (const file of tempFiles) {
      try {
        fs.unlinkSync(file);
      } catch {
        // ignore temp cleanup errors
      }
    }
  }
}
