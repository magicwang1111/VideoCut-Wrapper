import type { TemplateInfo } from '../registry/index.js';
import type { ResolutionPreset } from '../presets.js';

export interface ProbedVideoInfo extends ResolutionPreset {
  duration: number;
}

export interface RenderRequest {
  taskId?: string;
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
  duration?: number;
  error?: string;
}

export interface VideoClip {
  key: string;
  src: string;
  duration: number;
}
