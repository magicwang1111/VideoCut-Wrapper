import fs from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';
import { VideoCutError } from '../errors.js';

/** 项目配置结构 */
export interface ProjectConfig {
  template: string;
  preset?: string;
  quality?: string;
  output?: {
    filename?: string;
  };
  variables: Record<string, unknown>;
}

/**
 * 解析项目配置文件（YAML 或 JSON）
 */
export function parseProjectConfig(configPath: string): ProjectConfig {
  const absPath = path.resolve(configPath);

  if (!fs.existsSync(absPath)) {
    throw new VideoCutError(`配置文件不存在: ${absPath}`);
  }

  const raw = fs.readFileSync(absPath, 'utf-8');
  const ext = path.extname(absPath).toLowerCase();

  let parsed: unknown;
  try {
    if (ext === '.json') {
      parsed = JSON.parse(raw);
    } else {
      // .yaml, .yml or other — treat as YAML
      parsed = YAML.parse(raw);
    }
  } catch (err) {
    throw new VideoCutError(
      `配置文件解析失败: ${absPath}\n  ${err instanceof Error ? err.message : err}`,
    );
  }

  if (!parsed || typeof parsed !== 'object') {
    throw new VideoCutError(`配置文件格式错误: 应为对象，实际为 ${typeof parsed}`);
  }

  const config = parsed as Record<string, unknown>;

  if (!config.template || typeof config.template !== 'string') {
    throw new VideoCutError('配置文件缺少 "template" 字段（模板 ID）');
  }

  return {
    template: config.template,
    preset: typeof config.preset === 'string' ? config.preset : undefined,
    quality: typeof config.quality === 'string' ? config.quality : undefined,
    output:
      config.output && typeof config.output === 'object'
        ? (config.output as { filename?: string })
        : undefined,
    variables:
      config.variables && typeof config.variables === 'object'
        ? (config.variables as Record<string, unknown>)
        : {},
  };
}

/**
 * 获取项目配置文件所在目录（用于解析素材相对路径）
 */
export function getProjectDir(configPath: string): string {
  return path.dirname(path.resolve(configPath));
}
