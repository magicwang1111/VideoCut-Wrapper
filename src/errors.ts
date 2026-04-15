export class VideoCutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VideoCutError';
  }
}

export class TemplateNotFoundError extends VideoCutError {
  constructor(templateId: string, available: string[]) {
    super(
      `模板未注册: "${templateId}"\n` +
        `  可用模板: ${available.length > 0 ? available.join(', ') : '（无）'}\n` +
        `  运行 'npx videocut list' 查看所有模板`,
    );
    this.name = 'TemplateNotFoundError';
  }
}

export class ManifestError extends VideoCutError {
  constructor(templateDir: string, detail: string) {
    super(`模板清单错误: ${templateDir}\n  ${detail}`);
    this.name = 'ManifestError';
  }
}

export class ConfigValidationError extends VideoCutError {
  errors: string[];

  constructor(errors: string[]) {
    super(
      `配置校验失败，共 ${errors.length} 个错误:\n` +
        errors.map((e) => `  - ${e}`).join('\n'),
    );
    this.name = 'ConfigValidationError';
    this.errors = errors;
  }
}

export class AssetNotFoundError extends VideoCutError {
  constructor(assetPath: string, variableName: string, configPath?: string) {
    const location = configPath ? `\n  位置: ${configPath}` : '';
    super(
      `找不到素材文件\n` +
        `  文件: ${assetPath}\n` +
        `  变量: ${variableName}` +
        location +
        `\n  请确认文件路径正确且文件存在`,
    );
    this.name = 'AssetNotFoundError';
  }
}

export class RenderError extends VideoCutError {
  constructor(detail: string) {
    super(`渲染失败\n  ${detail}`);
    this.name = 'RenderError';
  }
}

export class DependencyError extends VideoCutError {
  constructor(dependency: string, detail: string) {
    super(`缺少依赖: ${dependency}\n  ${detail}`);
    this.name = 'DependencyError';
  }
}
