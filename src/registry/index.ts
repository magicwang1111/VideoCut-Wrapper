import fs from 'node:fs';
import path from 'node:path';
import { ManifestError, TemplateNotFoundError } from '../errors.js';

/** 变量类型 */
export type VariableType =
  | 'video'
  | 'video_list'
  | 'image'
  | 'audio'
  | 'text'
  | 'color'
  | 'number'
  | 'boolean'
  | 'select';

/** 变量定义 */
export interface VariableDefinition {
  type: VariableType;
  label: string;
  required?: boolean;
  default?: unknown;
  options?: string[]; // for select type
  min?: number; // for number type
  max?: number;
}

/** manifest.json 结构 */
export interface TemplateManifest {
  id: string;
  name: string;
  description: string;
  version: string;
  author?: string;
  preset?: string; // 可选，不填则默认 "auto"（探测输入视频分辨率）
  entry: string;
  variables: Record<string, VariableDefinition>;
  tags?: string[];
}

/** 注册后的模板信息（包含解析后的路径） */
export interface TemplateInfo {
  manifest: TemplateManifest;
  dir: string; // 模板目录绝对路径
  entryPath: string; // project.ts 绝对路径
  assetsDir: string; // assets/ 目录绝对路径
}

const REQUIRED_MANIFEST_FIELDS = [
  'id',
  'name',
  'description',
  'version',
  'entry',
  'variables',
] as const;

export class TemplateRegistry {
  private templates = new Map<string, TemplateInfo>();
  private templatesRoot: string;

  constructor(templatesRoot: string) {
    this.templatesRoot = path.resolve(templatesRoot);
  }

  /** 扫描 templates/ 目录，注册所有模板 */
  scan(): void {
    this.templates.clear();

    if (!fs.existsSync(this.templatesRoot)) {
      return;
    }

    const entries = fs.readdirSync(this.templatesRoot, {
      withFileTypes: true,
    });

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;

      const templateDir = path.join(this.templatesRoot, entry.name);
      const manifestPath = path.join(templateDir, 'manifest.json');

      if (!fs.existsSync(manifestPath)) continue;

      try {
        this.registerFromManifest(templateDir, manifestPath);
      } catch (err) {
        // 跳过无效模板，但打印警告
        console.warn(
          `⚠ 跳过模板 "${entry.name}": ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  }

  private registerFromManifest(
    templateDir: string,
    manifestPath: string,
  ): void {
    const raw = fs.readFileSync(manifestPath, 'utf-8');
    let manifest: TemplateManifest;

    try {
      manifest = JSON.parse(raw);
    } catch {
      throw new ManifestError(templateDir, 'manifest.json 不是有效的 JSON');
    }

    // 校验必填字段
    for (const field of REQUIRED_MANIFEST_FIELDS) {
      if (!(field in manifest)) {
        throw new ManifestError(templateDir, `缺少必填字段: "${field}"`);
      }
    }

    // 校验 entry 文件存在
    const entryPath = path.join(templateDir, manifest.entry);
    if (!fs.existsSync(entryPath)) {
      throw new ManifestError(
        templateDir,
        `入口文件不存在: "${manifest.entry}"`,
      );
    }

    // 校验 variables
    for (const [key, def] of Object.entries(manifest.variables)) {
      if (!def.type) {
        throw new ManifestError(
          templateDir,
          `变量 "${key}" 缺少 type 字段`,
        );
      }
      if (!def.label) {
        throw new ManifestError(
          templateDir,
          `变量 "${key}" 缺少 label 字段`,
        );
      }
    }

    const info: TemplateInfo = {
      manifest,
      dir: templateDir,
      entryPath,
      assetsDir: path.join(templateDir, 'assets'),
    };

    if (this.templates.has(manifest.id)) {
      console.warn(
        `⚠ 模板 ID "${manifest.id}" 重复注册，后者覆盖前者`,
      );
    }

    this.templates.set(manifest.id, info);
  }

  /** 获取模板信息，不存在时抛错 */
  get(templateId: string): TemplateInfo {
    const info = this.templates.get(templateId);
    if (!info) {
      throw new TemplateNotFoundError(templateId, this.listIds());
    }
    return info;
  }

  /** 检查模板是否存在 */
  has(templateId: string): boolean {
    return this.templates.has(templateId);
  }

  /** 列出所有模板 ID */
  listIds(): string[] {
    return Array.from(this.templates.keys());
  }

  /** 列出所有模板信息 */
  listAll(): TemplateInfo[] {
    return Array.from(this.templates.values());
  }

  /** 获取模板数量 */
  get size(): number {
    return this.templates.size;
  }
}
