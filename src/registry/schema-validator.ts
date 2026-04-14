import type { VariableDefinition } from './index.js';

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  merged: Record<string, unknown>; // 合并后的变量（用户值 + 默认值）
}

const FILE_EXTENSIONS: Record<string, string[]> = {
  video: ['.mp4', '.mov', '.avi', '.mkv', '.webm'],
  image: ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'],
  audio: ['.mp3', '.wav', '.aac', '.ogg', '.flac', '.m4a'],
};

const COLOR_REGEX = /^#[0-9a-fA-F]{6}$/;

/**
 * 校验用户变量是否符合模板的变量 Schema
 * @param schema 模板定义的变量 schema
 * @param userVars 用户提供的变量值
 * @returns 校验结果
 */
export function validateVariables(
  schema: Record<string, VariableDefinition>,
  userVars: Record<string, unknown>,
): ValidationResult {
  const errors: string[] = [];
  const merged: Record<string, unknown> = {};

  for (const [key, def] of Object.entries(schema)) {
    const userValue = userVars[key];
    const hasUser = userValue !== undefined && userValue !== null;

    // 必填检查
    if (def.required && !hasUser && def.default === undefined) {
      errors.push(`${def.label}（${key}）: 必填项未提供`);
      continue;
    }

    // 取值：用户值 > 默认值
    const value = hasUser ? userValue : def.default;
    if (value === undefined || value === null) {
      continue; // 可选且未提供，跳过
    }

    // 类型校验
    const typeError = validateType(key, def, value);
    if (typeError) {
      errors.push(typeError);
      continue;
    }

    merged[key] = value;
  }

  // 检查用户提供了不存在的变量
  for (const key of Object.keys(userVars)) {
    if (!(key in schema)) {
      errors.push(`未知变量: "${key}"，模板中未定义此变量`);
    }
  }

  return { valid: errors.length === 0, errors, merged };
}

function validateType(
  key: string,
  def: VariableDefinition,
  value: unknown,
): string | null {
  switch (def.type) {
    case 'text': {
      if (typeof value !== 'string') {
        return `${def.label}（${key}）: 应为文本，实际是 ${typeof value}`;
      }
      if (def.required && value.trim() === '') {
        return `${def.label}（${key}）: 文本不能为空`;
      }
      return null;
    }

    case 'video_list': {
      if (!Array.isArray(value)) {
        return `${def.label}（${key}）: 应为视频路径数组，实际是 ${typeof value}`;
      }
      if (value.length === 0) {
        return `${def.label}（${key}）: 视频列表不能为空`;
      }
      const allowed = FILE_EXTENSIONS['video'];
      for (let i = 0; i < value.length; i++) {
        const item = value[i];
        if (typeof item !== 'string') {
          return `${def.label}（${key}）[${i}]: 应为文件路径，实际是 ${typeof item}`;
        }
        const ext = item.slice(item.lastIndexOf('.')).toLowerCase();
        if (!allowed.includes(ext)) {
          return `${def.label}（${key}）[${i}]: 不支持的格式 "${ext}"，支持: ${allowed.join(', ')}`;
        }
      }
      return null;
    }

    case 'video':
    case 'image':
    case 'audio': {
      if (typeof value !== 'string') {
        return `${def.label}（${key}）: 应为文件路径（文本），实际是 ${typeof value}`;
      }
      const ext = value.slice(value.lastIndexOf('.')).toLowerCase();
      const allowed = FILE_EXTENSIONS[def.type];
      if (allowed && !allowed.includes(ext)) {
        return `${def.label}（${key}）: 不支持的文件格式 "${ext}"，支持: ${allowed.join(', ')}`;
      }
      return null;
    }

    case 'color': {
      if (typeof value !== 'string') {
        return `${def.label}（${key}）: 应为颜色值（文本），实际是 ${typeof value}`;
      }
      if (!COLOR_REGEX.test(value)) {
        return `${def.label}（${key}）: 无效的颜色值 "${value}"，应为 #RRGGBB 格式`;
      }
      return null;
    }

    case 'number': {
      if (typeof value !== 'number') {
        return `${def.label}（${key}）: 应为数值，实际是 ${typeof value}`;
      }
      if (def.min !== undefined && value < def.min) {
        return `${def.label}（${key}）: 值 ${value} 小于最小值 ${def.min}`;
      }
      if (def.max !== undefined && value > def.max) {
        return `${def.label}（${key}）: 值 ${value} 大于最大值 ${def.max}`;
      }
      return null;
    }

    case 'boolean': {
      if (typeof value !== 'boolean') {
        return `${def.label}（${key}）: 应为布尔值 (true/false)，实际是 ${typeof value}`;
      }
      return null;
    }

    case 'select': {
      if (typeof value !== 'string') {
        return `${def.label}（${key}）: 应为文本，实际是 ${typeof value}`;
      }
      if (def.options && !def.options.includes(value)) {
        return `${def.label}（${key}）: 无效的选项 "${value}"，可选: ${def.options.join(', ')}`;
      }
      return null;
    }

    default:
      return `${def.label}（${key}）: 未知的变量类型 "${def.type}"`;
  }
}
