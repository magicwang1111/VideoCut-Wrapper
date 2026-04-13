export interface ResolutionPreset {
  width: number;
  height: number;
  fps: number;
  label: string;
}

export interface QualityPreset {
  crf: number;
  ffmpegPreset: string;
  label: string;
}

/** 特殊预设名：自动从输入视频探测分辨率和帧率 */
export const AUTO_PRESET = 'auto';

export const RESOLUTION_PRESETS: Record<string, ResolutionPreset> = {
  douyin_vertical: {
    width: 1080,
    height: 1920,
    fps: 30,
    label: '抖音/快手竖屏 1080×1920',
  },
  douyin_horizontal: {
    width: 1920,
    height: 1080,
    fps: 30,
    label: '抖音/快手横屏 1920×1080',
  },
  xiaohongshu_square: {
    width: 1080,
    height: 1080,
    fps: 30,
    label: '小红书正方形 1080×1080',
  },
  xiaohongshu_vertical: {
    width: 1080,
    height: 1440,
    fps: 30,
    label: '小红书竖屏 3:4 1080×1440',
  },
  preview: {
    width: 540,
    height: 960,
    fps: 24,
    label: '快速预览（半分辨率）',
  },
};

export const QUALITY_PRESETS: Record<string, QualityPreset> = {
  low: { crf: 28, ffmpegPreset: 'fast', label: '低质量（快速）' },
  medium: { crf: 23, ffmpegPreset: 'medium', label: '中等质量' },
  high: { crf: 18, ffmpegPreset: 'slow', label: '高质量' },
};

export function getResolutionPreset(name: string): ResolutionPreset {
  if (name === AUTO_PRESET) {
    throw new Error(
      `"auto" 预设需要在渲染时动态探测，不能直接调用 getResolutionPreset("auto")`,
    );
  }
  const preset = RESOLUTION_PRESETS[name];
  if (!preset) {
    const available = [AUTO_PRESET, ...Object.keys(RESOLUTION_PRESETS)].join(', ');
    throw new Error(`未知的分辨率预设: "${name}"\n  可用预设: ${available}`);
  }
  return preset;
}

export function getQualityPreset(name: string): QualityPreset {
  const preset = QUALITY_PRESETS[name];
  if (!preset) {
    const available = Object.keys(QUALITY_PRESETS).join(', ');
    throw new Error(`未知的质量预设: "${name}"\n  可用预设: ${available}`);
  }
  return preset;
}
