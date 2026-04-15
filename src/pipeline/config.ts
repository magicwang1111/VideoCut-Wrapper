import fs from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';
import { VideoCutError } from '../errors.js';
import type {
  PipelineConfig,
  PipelineClipConfig,
  PipelineJunctionType,
  PipelineTransitionConfig,
} from './types.js';

/** Returns true if raw YAML object has mode: 'pipeline'. Used by CLI to route. */
export function isPipelineConfig(raw: unknown): boolean {
  return (
    typeof raw === 'object' &&
    raw !== null &&
    (raw as Record<string, unknown>)['mode'] === 'pipeline'
  );
}

/** Load and YAML-parse a config file without further validation. */
export function loadRawYaml(configPath: string): unknown {
  const absPath = path.resolve(configPath);
  if (!fs.existsSync(absPath)) {
    throw new VideoCutError(`配置文件不存在: ${absPath}`);
  }
  const raw = fs.readFileSync(absPath, 'utf-8');
  try {
    return path.extname(absPath).toLowerCase() === '.json'
      ? JSON.parse(raw)
      : YAML.parse(raw);
  } catch (err) {
    throw new VideoCutError(
      `配置文件解析失败: ${absPath}\n  ${err instanceof Error ? err.message : err}`,
    );
  }
}

function parseJunctionType(raw: unknown, location: string): PipelineJunctionType {
  if (raw === 'flash-black' || raw === 'dissolve' || raw === 'cut') return raw;
  if (raw == null) return 'cut';
  throw new VideoCutError(
    `${location}: 无效的 type "${raw}"，允许值: flash-black | dissolve | cut`,
  );
}

function parseTransitionConfig(raw: unknown, location: string): PipelineTransitionConfig {
  if (typeof raw !== 'object' || raw === null) {
    throw new VideoCutError(`${location}: 转场配置必须为对象`);
  }
  const obj = raw as Record<string, unknown>;
  return {
    type: parseJunctionType(obj['type'], `${location}.type`),
    duration: typeof obj['duration'] === 'number' ? obj['duration'] : 0.5,
  };
}

export function parsePipelineConfig(raw: unknown, configPath: string): PipelineConfig {
  void configPath;
  if (typeof raw !== 'object' || raw === null) {
    throw new VideoCutError('Pipeline 配置必须为对象');
  }
  const obj = raw as Record<string, unknown>;

  if (!Array.isArray(obj['clips']) || obj['clips'].length === 0) {
    throw new VideoCutError('Pipeline 配置缺少 "clips" 数组，或数组为空');
  }

  const clips: PipelineClipConfig[] = (obj['clips'] as unknown[]).map((c, i) => {
    if (typeof c !== 'object' || c === null) {
      throw new VideoCutError(`clips[${i}]: 必须为对象，含 src 字段`);
    }
    const clip = c as Record<string, unknown>;
    if (typeof clip['src'] !== 'string' || !clip['src'].trim()) {
      throw new VideoCutError(`clips[${i}].src: 必须为非空字符串路径`);
    }
    return {
      src: clip['src'] as string,
      trim_start: typeof clip['trim_start'] === 'number' ? clip['trim_start'] : 0,
      trim_end: typeof clip['trim_end'] === 'number' ? clip['trim_end'] : 0,
    };
  });

  const transitions: PipelineTransitionConfig[] | undefined = Array.isArray(obj['transitions'])
    ? (obj['transitions'] as unknown[]).map((t, i) =>
        parseTransitionConfig(t, `transitions[${i}]`),
      )
    : undefined;

  const default_transition =
    obj['default_transition'] != null
      ? parseTransitionConfig(obj['default_transition'], 'default_transition')
      : undefined;

  return {
    mode: 'pipeline',
    preset: typeof obj['preset'] === 'string' ? obj['preset'] : 'auto',
    quality: typeof obj['quality'] === 'string' ? obj['quality'] : 'high',
    output:
      obj['output'] && typeof obj['output'] === 'object'
        ? (obj['output'] as { filename?: string })
        : undefined,
    clips,
    transitions,
    default_transition,
  };
}

function resolveVideoPath(
  src: string,
  projectDir: string,
  configPath: string,
  index: number,
): string {
  if (path.isAbsolute(src)) {
    if (!fs.existsSync(src)) {
      throw new VideoCutError(
        `clips[${index}].src: 文件不存在: ${src}\n  配置文件: ${configPath}`,
      );
    }
    return src;
  }
  const candidate = path.resolve(projectDir, src);
  if (!fs.existsSync(candidate)) {
    throw new VideoCutError(
      `clips[${index}].src: 文件不存在（相对路径展开为）: ${candidate}\n  配置文件: ${configPath}`,
    );
  }
  return candidate;
}

/**
 * Builds exactly (clipCount - 1) junction configs.
 * Missing entries are filled from default_transition or 'cut'/0s fallback.
 */
export function resolveJunctions(
  clipCount: number,
  transitions: PipelineTransitionConfig[] | undefined,
  defaultTransition: PipelineTransitionConfig | undefined,
): PipelineTransitionConfig[] {
  const fallback: PipelineTransitionConfig = defaultTransition ?? { type: 'cut', duration: 0 };
  return Array.from({ length: clipCount - 1 }, (_, j) => transitions?.[j] ?? fallback);
}

export interface ParsedPipelineContext {
  config: PipelineConfig;
  projectDir: string;
  configPath: string;
  resolvedSrcs: string[];
  junctions: PipelineTransitionConfig[];
}

/** Full entry point: parse YAML → validate → resolve paths → build junctions. */
export function resolvePipelineConfig(configPath: string): ParsedPipelineContext {
  const absConfigPath = path.resolve(configPath);
  const projectDir = path.dirname(absConfigPath);
  const raw = loadRawYaml(absConfigPath);
  const config = parsePipelineConfig(raw, absConfigPath);

  const resolvedSrcs = config.clips.map((c, i) =>
    resolveVideoPath(c.src, projectDir, absConfigPath, i),
  );

  const junctions = resolveJunctions(
    config.clips.length,
    config.transitions,
    config.default_transition,
  );

  return { config, projectDir, configPath: absConfigPath, resolvedSrcs, junctions };
}
