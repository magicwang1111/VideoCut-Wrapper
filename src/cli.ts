#!/usr/bin/env node
import { Command } from 'commander';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TemplateRegistry } from './registry/index.js';
import { ProjectManager } from './project/index.js';
import {
  RESOLUTION_PRESETS,
  QUALITY_PRESETS,
  getResolutionPreset,
} from './presets.js';
import { GoumeiError } from './errors.js';

const ROOT_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);
const TEMPLATES_DIR = path.join(ROOT_DIR, 'templates');
const PROJECTS_DIR = path.join(ROOT_DIR, 'projects');

function createRegistry(): TemplateRegistry {
  const registry = new TemplateRegistry(TEMPLATES_DIR);
  registry.scan();
  return registry;
}

const program = new Command();
program
  .name('goumei')
  .description('模板化视频剪辑系统')
  .version('0.1.0');

// ─── list ───
program
  .command('list')
  .description('列出所有已注册的模板')
  .action(() => {
    const registry = createRegistry();
    const templates = registry.listAll();

    if (templates.length === 0) {
      console.log('暂无已注册的模板');
      console.log(`模板目录: ${TEMPLATES_DIR}`);
      return;
    }

    console.log(`\n已注册模板 (${templates.length} 个):\n`);
    console.log(
      '  ID'.padEnd(24) +
        '名称'.padEnd(16) +
        '版本'.padEnd(10) +
        '预设'.padEnd(20) +
        '变量数',
    );
    console.log('  ' + '─'.repeat(80));

    for (const t of templates) {
      const { manifest: m } = t;
      const varCount = Object.keys(m.variables).length;
      console.log(
        `  ${m.id.padEnd(22)}${m.name.padEnd(14)}${m.version.padEnd(8)}${m.preset.padEnd(18)}${varCount}`,
      );
    }

    console.log('');
  });

// ─── info ───
program
  .command('info <template-id>')
  .description('查看模板详情和变量说明')
  .action((templateId: string) => {
    const registry = createRegistry();
    const info = registry.get(templateId);
    const { manifest: m } = info;

    console.log(`\n模板: ${m.name}`);
    console.log(`ID:   ${m.id}`);
    console.log(`版本: ${m.version}`);
    console.log(`描述: ${m.description}`);
    console.log(`预设: ${m.preset ?? 'auto（探测输入视频）'}`);
    if (m.author) console.log(`作者: ${m.author}`);
    if (m.tags?.length) console.log(`标签: ${m.tags.join(', ')}`);

    console.log(`\n变量 (${Object.keys(m.variables).length} 个):\n`);

    for (const [key, def] of Object.entries(m.variables)) {
      const required = def.required ? ' [必填]' : '';
      const defaultVal =
        def.default !== undefined ? ` (默认: ${def.default})` : '';
      console.log(`  ${key}`);
      console.log(`    类型: ${def.type}${required}`);
      console.log(`    说明: ${def.label}${defaultVal}`);
      if (def.options) {
        console.log(`    选项: ${def.options.join(', ')}`);
      }
    }

    console.log('');
  });

// ─── init ───
program
  .command('init <template-id> <project-name>')
  .description('创建新项目（自动生成配置和素材目录）')
  .action((templateId: string, projectName: string) => {
    const registry = createRegistry();
    const info = registry.get(templateId);
    const { manifest: m } = info;

    const projectDir = path.join(PROJECTS_DIR, projectName);
    const materialsDir = path.join(projectDir, 'materials');

    if (fs.existsSync(projectDir)) {
      console.error(`错误: 项目目录已存在: ${projectDir}`);
      process.exit(1);
    }

    fs.mkdirSync(materialsDir, { recursive: true });

    // 生成带注释的 config.yaml
    const lines: string[] = [];
    lines.push(`# 项目配置 — ${m.name} 模板`);
    lines.push(`# 创建时间: ${new Date().toISOString()}`);
    lines.push('');
    lines.push(`template: "${m.id}"`);
    lines.push('');
    lines.push(`# 分辨率预设（可选，不写则用模板默认: ${m.preset}）`);
    lines.push(`# preset: "${m.preset}"`);
    lines.push('');
    lines.push(`# 输出质量（可选: low / medium / high，默认: high）`);
    lines.push('# quality: "high"');
    lines.push('');
    lines.push('# 变量 — 填入你的素材和内容');
    lines.push('variables:');

    for (const [key, def] of Object.entries(m.variables)) {
      lines.push('');
      lines.push(`  # ${def.label} [${def.type}]${def.required ? ' (必填)' : ''}`);

      if (def.type === 'video' || def.type === 'image' || def.type === 'audio') {
        const placeholder =
          def.default !== undefined
            ? String(def.default)
            : `materials/your-file.${def.type === 'video' ? 'mp4' : def.type === 'image' ? 'png' : 'mp3'}`;
        lines.push(`  ${key}: "${placeholder}"`);
      } else if (def.type === 'boolean') {
        lines.push(`  ${key}: ${def.default ?? true}`);
      } else if (def.type === 'number') {
        lines.push(`  ${key}: ${def.default ?? 0}`);
      } else {
        const value = def.default !== undefined ? String(def.default) : '';
        lines.push(`  ${key}: "${value}"`);
      }
    }

    lines.push('');

    const configPath = path.join(projectDir, 'config.yaml');
    fs.writeFileSync(configPath, lines.join('\n'), 'utf-8');

    console.log(`\n项目已创建: ${projectDir}`);
    console.log(`  配置文件: ${configPath}`);
    console.log(`  素材目录: ${materialsDir}`);
    console.log(`\n下一步:`);
    console.log(`  1. 将素材文件放入 ${materialsDir}`);
    console.log(`  2. 编辑 ${configPath} 填入变量`);
    console.log(
      `  3. 运行 npx goumei render ${path.relative(ROOT_DIR, configPath)}`,
    );
    console.log('');
  });

// ─── validate ───
program
  .command('validate <config>')
  .description('校验项目配置（不渲染）')
  .action((configPath: string) => {
    const registry = createRegistry();
    const projectManager = new ProjectManager(registry, ROOT_DIR);

    try {
      const resolved = projectManager.resolve(configPath);
      console.log(`\n✓ 配置校验通过`);
      console.log(`  模板: ${resolved.templateInfo.manifest.name} (${resolved.templateId})`);
      console.log(`  预设: ${resolved.preset}`);
      console.log(`  质量: ${resolved.quality}`);
      console.log(`  变量: ${Object.keys(resolved.variables).length} 个`);
      console.log('');
    } catch (err) {
      if (err instanceof GoumeiError) {
        console.error(`\n✗ ${err.message}\n`);
        process.exit(1);
      }
      throw err;
    }
  });

// ─── presets ───
program
  .command('presets')
  .description('查看可用的分辨率和质量预设')
  .action(() => {
    console.log('\n分辨率预设:\n');
    console.log(`  ${'auto'.padEnd(24)} 自动探测输入视频的分辨率和帧率（默认）`);
    for (const [name, preset] of Object.entries(RESOLUTION_PRESETS)) {
      console.log(
        `  ${name.padEnd(24)} ${preset.width}×${preset.height} ${preset.fps}fps  ${preset.label}`,
      );
    }

    console.log('\n质量预设:\n');
    for (const [name, preset] of Object.entries(QUALITY_PRESETS)) {
      console.log(
        `  ${name.padEnd(10)} CRF ${preset.crf}  ${preset.label}`,
      );
    }
    console.log('');
  });

// ─── check ───
program
  .command('check')
  .description('检查系统依赖')
  .action(async () => {
    console.log('\n系统依赖检查:\n');

    // Node.js
    const nodeVersion = process.version;
    const nodeMajor = parseInt(nodeVersion.slice(1));
    if (nodeMajor >= 18) {
      console.log(`  ✓ Node.js ${nodeVersion}`);
    } else {
      console.log(`  ✗ Node.js ${nodeVersion} — 需要 >= 18`);
    }

    // FFmpeg
    const { execSync } = await import('node:child_process');
    try {
      const ffmpegVersion = execSync('ffmpeg -version', {
        encoding: 'utf-8',
      }).split('\n')[0];
      console.log(`  ✓ FFmpeg: ${ffmpegVersion}`);
    } catch {
      console.log('  ✗ FFmpeg: 未安装或不在 PATH 中');
    }

    // Chrome / Chromium
    const chromePaths = [
      process.env.CHROME_PATH,
      'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
      'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
      '/usr/bin/google-chrome',
      '/usr/bin/chromium-browser',
    ].filter(Boolean) as string[];

    let chromeFound = false;
    for (const p of chromePaths) {
      if (fs.existsSync(p)) {
        console.log(`  ✓ Chrome: ${p}`);
        chromeFound = true;
        break;
      }
    }
    if (!chromeFound) {
      console.log(
        '  ✗ Chrome/Chromium: 未找到（Revideo 渲染需要）',
      );
    }

    // 模板
    const registry = createRegistry();
    console.log(`  ✓ 模板: 已注册 ${registry.size} 个`);

    // 字体
    const fontsDir = path.join(ROOT_DIR, 'fonts');
    if (fs.existsSync(fontsDir)) {
      const fonts = fs
        .readdirSync(fontsDir)
        .filter((f) => /\.(otf|ttf|woff2?)$/i.test(f));
      if (fonts.length > 0) {
        console.log(`  ✓ 字体: ${fonts.length} 个 (${fonts.join(', ')})`);
      } else {
        console.log('  ⚠ 字体: fonts/ 目录为空，建议添加中文字体');
      }
    } else {
      console.log('  ⚠ 字体: fonts/ 目录不存在');
    }

    console.log('');
  });

// ─── render (placeholder, 后续在 Step 10 接通) ───
program
  .command('render <config>')
  .description('渲染视频')
  .option('--preset <name>', '覆盖分辨率预设')
  .option('--quality <level>', '覆盖质量 (low/medium/high)')
  .option('--output <dir>', '覆盖输出目录')
  .option('--preview', '快速预览（半分辨率）')
  .action(async (configPath: string, options: Record<string, unknown>) => {
    const registry = createRegistry();
    const projectManager = new ProjectManager(registry, ROOT_DIR);

    try {
      // 校验
      const resolved = projectManager.resolve(configPath);

      // 应用 CLI 覆盖
      if (options.preset) resolved.preset = options.preset as string;
      if (options.quality) resolved.quality = options.quality as string;
      if (options.preview) resolved.preset = 'preview';

      console.log(`\n开始渲染:`);
      console.log(`  模板: ${resolved.templateInfo.manifest.name}`);
      if (resolved.preset === 'auto') {
        console.log(`  预设: auto（将从输入视频探测分辨率）`);
      } else {
        const preset = getResolutionPreset(resolved.preset);
        console.log(`  预设: ${resolved.preset} (${preset.width}×${preset.height})`);
      }
      console.log(`  质量: ${resolved.quality}`);

      // 动态导入 RenderService（避免非渲染命令加载重依赖）
      const { RenderService } = await import('./render/index.js');
      const renderService = new RenderService(ROOT_DIR);

      const result = await renderService.render({
        templateId: resolved.templateId,
        templateInfo: resolved.templateInfo,
        variables: resolved.variables,
        preset: resolved.preset,
        quality: resolved.quality,
        outputFilename: resolved.outputFilename,
        projectDir: resolved.projectDir,
      });

      if (result.status === 'completed') {
        console.log(`\n✓ 渲染完成!`);
        console.log(`  输出: ${result.outputPath}`);
        if (result.duration) {
          console.log(`  耗时: ${result.duration.toFixed(1)} 秒`);
        }
      } else {
        console.error(`\n✗ 渲染失败: ${result.error}`);
        process.exit(1);
      }
    } catch (err) {
      if (err instanceof GoumeiError) {
        console.error(`\n✗ ${err.message}\n`);
        process.exit(1);
      }
      throw err;
    }

    console.log('');
  });

program.parse();
