import path from 'node:path';
import type { TemplateRegistry, TemplateInfo } from '../registry/index.js';
import { validateVariables } from '../registry/schema-validator.js';
import { resolveAssets } from './asset-resolver.js';
import {
  parseProjectConfig,
  getProjectDir,
  type ProjectConfig,
} from './config-parser.js';
import { ConfigValidationError } from '../errors.js';

/** 校验并解析后的项目数据，可直接交给 RenderService */
export interface ResolvedProject {
  templateId: string;
  templateInfo: TemplateInfo;
  variables: Record<string, unknown>; // 合并 + 校验 + 路径解析后
  preset: string;
  quality: string;
  outputFilename?: string;
  projectDir: string;
  configPath: string;
}

export class ProjectManager {
  constructor(
    private registry: TemplateRegistry,
    private rootDir: string,
  ) {}

  /**
   * 从配置文件加载、校验、解析项目
   */
  resolve(configPath: string): ResolvedProject {
    const absConfigPath = path.resolve(configPath);
    const projectDir = getProjectDir(absConfigPath);

    // 1. 解析配置
    const config: ProjectConfig = parseProjectConfig(absConfigPath);

    // 2. 获取模板（不存在会抛错）
    const templateInfo = this.registry.get(config.template);
    const { manifest } = templateInfo;

    // 3. 校验变量
    const validationResult = validateVariables(
      manifest.variables,
      config.variables,
    );
    if (!validationResult.valid) {
      throw new ConfigValidationError(validationResult.errors);
    }

    // 4. 解析素材路径
    const resolvedVars = resolveAssets(
      validationResult.merged,
      manifest.variables,
      projectDir,
      templateInfo,
      this.rootDir,
      absConfigPath,
    );

    return {
      templateId: config.template,
      templateInfo,
      variables: resolvedVars,
      preset: config.preset ?? manifest.preset ?? 'auto',
      quality: config.quality ?? 'high',
      outputFilename: config.output?.filename,
      projectDir,
      configPath: absConfigPath,
    };
  }
}

export { parseProjectConfig, getProjectDir } from './config-parser.js';
export { resolveAssets } from './asset-resolver.js';
