import fs from 'node:fs';
import path from 'node:path';
import type { TemplateInfo, VariableDefinition } from '../registry/index.js';
import { AssetNotFoundError } from '../errors.js';

const FILE_TYPES = new Set(['video', 'video_list', 'image', 'audio']);

/**
 * 解析素材路径：相对路径按 项目目录 → 模板目录 → 项目根目录 的顺序查找
 * 返回所有变量的绝对路径版本
 */
export function resolveAssets(
  variables: Record<string, unknown>,
  schema: Record<string, VariableDefinition>,
  projectDir: string,
  templateInfo: TemplateInfo,
  rootDir: string,
  configPath?: string,
): Record<string, unknown> {
  const resolved: Record<string, unknown> = { ...variables };

  for (const [key, value] of Object.entries(variables)) {
    const def = schema[key];
    if (!def || !FILE_TYPES.has(def.type)) continue;

    // video_list：逐项解析路径
    if (def.type === 'video_list') {
      if (!Array.isArray(value)) continue;
      const resolvedList: string[] = [];
      for (let i = 0; i < value.length; i++) {
        const item = value[i];
        if (typeof item !== 'string' || item === '') continue;
        const absPath = resolveFilePath(item, [projectDir, templateInfo.dir, rootDir]);
        if (!absPath) throw new AssetNotFoundError(item, `${key}[${i}]`, configPath);
        resolvedList.push(absPath);
      }
      resolved[key] = resolvedList;
      continue;
    }

    if (typeof value !== 'string' || value === '') continue;

    const absPath = resolveFilePath(value, [
      projectDir,
      templateInfo.dir,
      rootDir,
    ]);

    if (!absPath) {
      throw new AssetNotFoundError(value, key, configPath);
    }

    resolved[key] = absPath;
  }

  return resolved;
}

/**
 * 按优先级在多个目录中查找文件
 */
function resolveFilePath(
  filePath: string,
  searchDirs: string[],
): string | null {
  // 绝对路径直接检查
  if (path.isAbsolute(filePath)) {
    return fs.existsSync(filePath) ? filePath : null;
  }

  // 相对路径按优先级查找
  for (const dir of searchDirs) {
    const candidate = path.resolve(dir, filePath);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return null;
}
