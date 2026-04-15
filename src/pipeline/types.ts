export type PipelineJunctionType = 'flash-black' | 'dissolve' | 'cut';

export interface PipelineClipConfig {
  src: string;
  trim_start?: number;   // seconds to skip from beginning, default 0
  trim_end?: number;     // seconds to remove from end, default 0
}

export interface PipelineTransitionConfig {
  type: PipelineJunctionType;
  duration: number;      // seconds
}

export interface PipelineConfig {
  mode: 'pipeline';
  preset?: string;                           // default 'auto'
  quality?: string;                          // default 'high'
  output?: { filename?: string };
  clips: PipelineClipConfig[];
  transitions?: PipelineTransitionConfig[];  // transitions[i] = junction clips[i]→clips[i+1]
  default_transition?: PipelineTransitionConfig;
}

/** Clip after path resolution + ffprobe */
export interface ResolvedPipelineClip {
  key: string;                // 'clip_1', 'clip_2', ...
  src: string;                // absolute path (may be temp normalized file)
  probedDuration: number;
  trimStart: number;
  trimEnd: number;
  effectiveDuration: number;  // probedDuration - trimStart - trimEnd
}
