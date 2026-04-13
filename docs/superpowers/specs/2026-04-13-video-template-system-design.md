# Goumei-Video-Cut: 模板化视频剪辑系统设计文档 v2

## 背景

需要一个模板化的视频剪辑平台，用于短视频/社交媒体内容的批量生产。核心诉求是：**封装一次模板，后续只需替换素材即可生成新视频**。需要支持转场、背景音乐、字幕、多层合成等能力，同时要方便管理多套模板。

**架构目标**：单镜像多模板、统一渲染 API、模板注册制。

## 技术方案

**三层架构：应用层 + Revideo 渲染层 + 基础设施层**

底层渲染引擎选用 Revideo（TypeScript，scene-based，generator-driven），通过 `@revideo/renderer` 的 `renderVideo()` API 实现 headless 渲染。应用层用 Node.js/TypeScript 统一技术栈，负责模板管理、项目配置、任务编排。

---

## 分层架构

```
┌──────────────────────────────────────────────────────────┐
│                   应用层 (Application)                     │
│  模板注册 · 项目配置 · 变量校验 · 素材管理 · 任务状态 · CLI  │
├──────────────────────────────────────────────────────────┤
│                  Revideo 渲染层 (Render)                   │
│  时间线 · 场景动画 · 转场 · 字幕 · 多层合成 · 参数注入       │
├──────────────────────────────────────────────────────────┤
│                基础设施层 (Infrastructure)                  │
│  文件存储 · 渲染队列 · Worker · 产物归档 · 回调通知         │
└──────────────────────────────────────────────────────────┘
```

### 应用层 — 你自己的业务

| 职责 | 说明 |
|------|------|
| 模板注册中心 | 扫描 `templates/*/manifest.json`，注册模板到内存 registry |
| 变量 Schema | 每个模板声明自己的变量类型、必填、默认值；渲染前用 schema 校验 |
| 项目配置 | 用户编辑 JSON/YAML 指定模板 + 变量值 + 素材路径 |
| 素材管理 | 解析路径（相对/绝对），检查文件存在性和格式 |
| 任务状态机 | pending → rendering → completed / failed |
| 产物归档 | 输出视频归档到 `output/<project>/`，保留元数据 |
| CLI 接口 | render / list / init / validate / check |

### Revideo 渲染层 — 模板执行引擎

| 职责 | 说明 |
|------|------|
| 场景定义 | 每个模板 = 一组 `.tsx` 场景文件 + `project.ts` |
| 参数注入 | 通过 `renderVideo({ variables })` 将用户变量传入场景 |
| 时间线表达 | generator 函数 `function*(view)` 控制动画时序 |
| 多层合成 | Canvas 2D：Video + Image + Text 叠加 |
| 转场动画 | 场景间自定义 fade/slide/zoom 等动画 |
| 音频处理 | `<Audio/>` 组件 + FFmpeg 后端混合 |
| 预览 | `npm run dev` 浏览器实时预览（开发时用） |
| 渲染输出 | `renderVideo()` → MP4 文件 |

### 基础设施层 — 存储和执行

| 职责 | 说明 |
|------|------|
| 文件存储 | v1: 本地文件系统；v2: 可接 S3/OSS |
| 渲染队列 | v1: 内存队列（单机顺序执行）；v2: Redis/Bull |
| Worker | v1: 主进程直接调用 `renderVideo()`；v2: 独立 worker 进程 |
| 产物归档 | 输出文件 + 渲染日志 + 元数据 JSON |
| 回调通知 | v1: CLI 输出完成/失败；v2: webhook/事件 |

---

## 目录结构

```
D:\Goumei-Video-Cut\
│
├── package.json                    # Node.js 项目配置
├── tsconfig.json                   # TypeScript 配置
├── vite.config.ts                  # Vite 配置（Revideo 需要）
│
├── src/                            # 应用层代码
│   ├── cli.ts                      # CLI 入口（commander）
│   ├── registry/                   # 模板注册中心
│   │   ├── index.ts                # TemplateRegistry 类
│   │   └── schema-validator.ts     # 变量 Schema 校验
│   ├── project/                    # 项目管理
│   │   ├── index.ts                # ProjectManager 类
│   │   ├── config-parser.ts        # 项目配置解析
│   │   └── asset-resolver.ts       # 素材路径解析 + 验证
│   ├── render/                     # 渲染任务管理
│   │   ├── index.ts                # RenderService 类
│   │   ├── task.ts                 # 任务状态机
│   │   └── queue.ts                # 渲染队列
│   ├── output/                     # 产物管理
│   │   └── index.ts                # OutputManager 类
│   ├── presets.ts                  # 分辨率/质量预设
│   └── errors.ts                   # 自定义错误
│
├── templates/                      # 模板库（单镜像多模板）
│   ├── product-showcase/           # 模板 A
│   │   ├── manifest.json           # 模板清单（元数据 + 变量 schema）
│   │   ├── src/
│   │   │   ├── project.ts          # Revideo project 定义
│   │   │   └── scenes/
│   │   │       ├── intro.tsx       # 场景 1
│   │   │       ├── detail.tsx      # 场景 2
│   │   │       └── outro.tsx       # 场景 3
│   │   └── assets/                 # 模板默认素材
│   │       ├── logo.png
│   │       └── default_bgm.mp3
│   │
│   └── simple-slideshow/           # 模板 B
│       ├── manifest.json
│       ├── src/
│       │   ├── project.ts
│       │   └── scenes/
│       │       └── slideshow.tsx
│       └── assets/
│
├── fonts/                          # 中文字体
│   └── SourceHanSansSC-Regular.otf
│
├── assets/                         # 全局共享素材
│   ├── music/
│   └── logos/
│
├── projects/                       # 用户项目（渲染任务）
│   └── my-video/
│       ├── config.yaml             # 项目配置
│       └── materials/              # 用户素材
│
├── output/                         # 渲染产物
│   └── my-video/
│       ├── final.mp4
│       └── meta.json               # 渲染元数据
│
└── temp/                           # 临时文件
```

---

## 模板注册制

### manifest.json（模板清单）

每个模板必须包含一个 `manifest.json`，声明模板的元数据和变量 schema：

```json
{
  "id": "product-showcase",
  "name": "产品展示",
  "description": "3 场景产品展示模板，含 Logo 叠加和背景音乐",
  "version": "1.0.0",
  "author": "Goumei",
  
  "preset": "douyin_vertical",
  
  "entry": "src/project.ts",
  
  "variables": {
    "clip_intro": {
      "type": "video",
      "label": "开场视频",
      "required": true
    },
    "clip_detail": {
      "type": "video",
      "label": "产品特写",
      "required": true
    },
    "clip_outro": {
      "type": "video",
      "label": "结尾视频",
      "required": true
    },
    "product_name": {
      "type": "text",
      "label": "产品名称",
      "default": "产品名称"
    },
    "price_text": {
      "type": "text",
      "label": "价格文案",
      "default": "¥99"
    },
    "logo": {
      "type": "image",
      "label": "品牌 Logo",
      "default": "assets/logo.png"
    },
    "bgm": {
      "type": "audio",
      "label": "背景音乐",
      "default": "assets/default_bgm.mp3"
    },
    "subtitle_color": {
      "type": "color",
      "label": "字幕颜色",
      "default": "#FFFFFF"
    },
    "enable_bgm": {
      "type": "boolean",
      "label": "启用背景音乐",
      "default": true
    },
    "enable_transition": {
      "type": "boolean",
      "label": "启用转场",
      "default": true
    }
  },

  "tags": ["电商", "产品展示", "竖屏"]
}
```

### 变量类型系统

| type | 说明 | 校验规则 |
|------|------|----------|
| `video` | 视频文件路径 | 文件存在 + 扩展名 .mp4/.mov/.avi |
| `image` | 图片文件路径 | 文件存在 + 扩展名 .png/.jpg/.webp |
| `audio` | 音频文件路径 | 文件存在 + 扩展名 .mp3/.wav/.aac |
| `text` | 文本字符串 | 非空（如果 required） |
| `color` | 颜色值 | #RRGGBB 或 CSS 颜色名 |
| `number` | 数值 | 可选 min/max 约束 |
| `boolean` | 布尔开关 | true/false |
| `select` | 枚举选择 | 必须在 options 列表中 |

### 模板注册流程

```
启动时扫描 templates/*/manifest.json
  ↓
校验 manifest 格式
  ↓
校验 entry 文件存在
  ↓
注册到内存 TemplateRegistry（Map<id, TemplateInfo>）
  ↓
CLI list 命令从 registry 读取
```

---

## 项目配置格式

### projects/<name>/config.yaml

用户只需要编辑这个文件：

```yaml
# 使用哪个模板
template: "product-showcase"

# 可选：覆盖分辨率预设
# preset: "xiaohongshu_square"

# 可选：覆盖输出设置
# output:
#   filename: "my_product_video.mp4"
#   quality: "high"

# 填入变量（只需填必填项和需要修改默认值的项）
variables:
  clip_intro: "materials/opening.mp4"
  clip_detail: "materials/closeup.mp4"
  clip_outro: "materials/ending.mp4"
  product_name: "超级面膜"
  price_text: "¥59.9 限时特价"
  # logo 不写则用模板默认的 assets/logo.png
  # bgm 不写则用模板默认的
  # enable_bgm: false        # 不要背景音乐时设为 false
  # enable_transition: false  # 不要转场时设为 false
```

### 配置校验流程

```
加载 config.yaml
  ↓
查找模板（从 registry）
  ↓
合并变量（config.variables 覆盖 manifest.variables.*.default）
  ↓
按 schema 校验每个变量（类型、必填、约束）
  ↓
解析素材路径（相对路径基于项目目录 → 模板目录 → 根目录）
  ↓
检查所有文件存在性
  ↓
返回校验结果（全部通过 or 错误列表）
```

---

## Revideo 渲染层设计

### 模板场景文件结构

以 `product-showcase` 为例，`src/project.ts`：

```typescript
import { makeProject } from '@revideo/core';
import intro from './scenes/intro?scene';
import detail from './scenes/detail?scene';
import outro from './scenes/outro?scene';

export default makeProject({
  scenes: [intro, detail, outro],
});
```

场景文件 `src/scenes/intro.tsx`：

```typescript
import { makeScene2D, Img, Txt, Video } from '@revideo/2d';
import { all, waitFor, createRef, useScene } from '@revideo/core';

export default makeScene2D(function* (view) {
  const vars = useScene().variables;
  
  const clipSrc = vars.get('clip_intro', '')();
  const productName = vars.get('product_name', '产品名称')();
  const logoSrc = vars.get('logo', '')();
  const subtitleColor = vars.get('subtitle_color', '#FFFFFF')();
  const enableTransition = vars.get('enable_transition', true)();

  // 主视频层
  const video = createRef<Video>();
  view.add(
    <Video ref={video} src={clipSrc} size={view.size()} play={true} />
  );

  // Logo 层
  if (logoSrc) {
    view.add(
      <Img src={logoSrc} 
           position={[-view.width()/2 + 100, -view.height()/2 + 100]}
           size={[120, 120]} opacity={0.8} />
    );
  }

  // 字幕层
  const title = createRef<Txt>();
  view.add(
    <Txt ref={title}
         text={productName}
         fontSize={64}
         fill={subtitleColor}
         fontFamily="Source Han Sans SC"
         y={view.height() * 0.3}
         opacity={0} />
  );

  // 动画：字幕淡入
  yield* title().opacity(1, 0.5);
  yield* waitFor(2.0);

  // 转场：淡出（如果启用）
  if (enableTransition) {
    yield* view.opacity(0, 0.5);
  }
});
```

### 参数注入机制

应用层调用 Revideo 渲染时，通过 `renderVideo()` 传入用户变量：

```typescript
import { renderVideo } from '@revideo/renderer';

const outputPath = await renderVideo({
  projectFile: templateInfo.entryPath,  // 模板的 project.ts
  variables: resolvedVariables,          // 用户变量（已解析路径）
  settings: {
    outFile: outputFilename,
    outDir: outputDir,
    dimensions: [preset.width, preset.height],
    logProgress: true,
    progressCallback: (worker, progress) => {
      updateTaskProgress(taskId, progress);
    },
    ffmpeg: {
      ffmpegPath: ffmpegBinaryPath,
    },
  },
});
```

### 模板中的可选步骤

模板通过 boolean 变量控制功能开关：

```typescript
// 在场景中
const enableBgm = vars.get('enable_bgm', true)();
const enableTransition = vars.get('enable_transition', true)();

// 条件性添加音频
if (enableBgm) {
  view.add(<Audio src={bgmSrc} play={true} volume={0.3} />);
}

// 条件性执行转场动画
if (enableTransition) {
  yield* view.opacity(0, 0.5);
}
```

**不需要在应用层做步骤跳过** — 逻辑直接在模板场景代码中用 boolean 开关控制，更自然。

---

## 统一渲染 API

### RenderService

所有渲染请求走同一个接口：

```typescript
interface RenderRequest {
  templateId: string;          // 模板 ID
  variables: Record<string, any>;  // 用户变量
  preset?: string;             // 分辨率预设覆盖
  quality?: 'low' | 'medium' | 'high';  // 质量覆盖
  outputFilename?: string;     // 输出文件名覆盖
}

interface RenderResult {
  taskId: string;
  status: 'completed' | 'failed';
  outputPath?: string;
  duration?: number;           // 渲染耗时（秒）
  error?: string;
}

class RenderService {
  async render(request: RenderRequest): Promise<RenderResult>;
}
```

### 渲染流程

```
RenderService.render(request)
  ↓
1. 从 registry 获取模板信息
2. 合并变量（request.variables + manifest.defaults）
3. 校验变量（schema validation）
4. 解析素材路径为绝对路径
5. 创建任务（TaskManager）
6. 入渲染队列
  ↓
Queue → Worker 执行：
7. 调用 renderVideo() 传入 projectFile + variables + settings
8. 监听 progressCallback 更新任务进度
9. 渲染完成 → 归档产物（OutputManager）
10. 更新任务状态 → completed / failed
  ↓
返回 RenderResult
```

---

## 任务状态机

```
pending → rendering → completed
                  ↘ failed
```

每个任务记录：

```typescript
interface RenderTask {
  id: string;
  templateId: string;
  projectId: string;
  status: 'pending' | 'rendering' | 'completed' | 'failed';
  progress: number;            // 0-100
  createdAt: Date;
  startedAt?: Date;
  completedAt?: Date;
  outputPath?: string;
  error?: string;
  variables: Record<string, any>;  // 快照
}
```

v1 存储在内存 + JSON 文件（`output/<project>/meta.json`）。

---

## 产物归档

渲染完成后，输出目录结构：

```
output/<project-name>/
├── final.mp4                  # 渲染结果
└── meta.json                  # 元数据
```

`meta.json` 内容：

```json
{
  "taskId": "abc123",
  "templateId": "product-showcase",
  "templateVersion": "1.0.0",
  "preset": "douyin_vertical",
  "variables": { "...快照..." },
  "renderedAt": "2026-04-13T15:30:00Z",
  "duration": 45.2,
  "outputFile": "final.mp4",
  "fileSize": 12345678,
  "resolution": "1080x1920"
}
```

---

## CLI 接口

```
npx goumei <command> [options]

命令:
  render    <config.yaml>              渲染视频
  list                                 列出已注册的模板
  info      <template-id>              查看模板详情和变量说明
  init      <template-id> <name>       创建新项目（生成 config.yaml + materials/）
  validate  <config.yaml>              校验配置（不渲染）
  presets                              查看可用分辨率预设
  check                                检查系统依赖

渲染选项:
  --preset <name>     覆盖分辨率预设
  --quality <level>   覆盖质量（low/medium/high）
  --output <dir>      覆盖输出目录
  --preview           快速预览（半分辨率，低质量）
```

### 命令示例

```bash
# 检查环境
npx goumei check

# 列出所有模板
npx goumei list

# 查看某个模板的变量说明
npx goumei info product-showcase

# 创建新项目
npx goumei init product-showcase my-product-video
# → 生成 projects/my-product-video/config.yaml（带注释）
# → 生成 projects/my-product-video/materials/（空目录）

# 编辑配置、放入素材后渲染
npx goumei render projects/my-product-video/config.yaml

# 快速预览
npx goumei render projects/my-product-video/config.yaml --preview
```

---

## 分辨率和质量预设

```typescript
const PRESETS = {
  douyin_vertical:      { width: 1080, height: 1920, fps: 30, label: '抖音/快手竖屏' },
  douyin_horizontal:    { width: 1920, height: 1080, fps: 30, label: '抖音/快手横屏' },
  xiaohongshu_square:   { width: 1080, height: 1080, fps: 30, label: '小红书正方形' },
  xiaohongshu_vertical: { width: 1080, height: 1440, fps: 30, label: '小红书 3:4' },
  preview:              { width: 540,  height: 960,  fps: 24, label: '快速预览' },
};

const QUALITY = {
  low:    { crf: 28, ffmpegPreset: 'fast' },
  medium: { crf: 23, ffmpegPreset: 'medium' },
  high:   { crf: 18, ffmpegPreset: 'slow' },
};
```

---

## 错误处理

所有错误信息为中文，包含具体位置和修复建议：

```
错误: 找不到素材文件
  文件: materials/clip2.mp4
  变量: clip_detail
  位置: projects/my-video/config.yaml
  请确认文件已放入 projects/my-video/materials/ 目录

错误: 模板未注册
  模板 ID: product-showcase2
  可用模板: product-showcase, simple-slideshow
  运行 'npx goumei list' 查看所有模板

错误: 变量校验失败
  - clip_intro: 必填项未提供
  - subtitle_color: 无效的颜色值 "red123"，应为 #RRGGBB 格式
```

---

## v2 扩展路径（当前不实现，仅规划）

| 能力 | v1 (当前) | v2 (未来) |
|------|-----------|-----------|
| 存储 | 本地文件系统 | S3/OSS 对象存储 |
| 队列 | 内存队列，同步执行 | Redis + Bull 异步队列 |
| Worker | 主进程内执行 | 独立 worker 进程/容器 |
| 回调 | CLI 输出 | Webhook + 事件系统 |
| 接口 | CLI | REST API + CLI |
| 部署 | 本地运行 | Docker 单镜像部署 |
| 权限 | 无 | API Key + 模板级权限 |

v1 的所有设计都预留了 v2 的扩展接口（如 StorageAdapter、QueueAdapter），但只实现本地版本。

---

## 依赖

```json
{
  "dependencies": {
    "@revideo/core": "^0.10",
    "@revideo/2d": "^0.10",
    "@revideo/renderer": "^0.10",
    "@revideo/vite-plugin": "^0.10",
    "commander": "^12.0",
    "yaml": "^2.0",
    "ajv": "^8.0"
  },
  "devDependencies": {
    "typescript": "^5.0",
    "vite": "^5.0"
  }
}
```

外部依赖：
- Node.js >= 18
- FFmpeg（需安装并加入 PATH）
- Chrome/Chromium（Revideo 渲染需要）
- 中文字体（放入 fonts/ 目录）

---

## 实现顺序

1. 项目脚手架：package.json、tsconfig、vite.config、目录结构
2. 预设和错误定义：presets.ts、errors.ts
3. 模板注册中心：manifest.json schema、TemplateRegistry
4. 项目配置解析：config-parser、asset-resolver、schema-validator
5. CLI 非渲染命令：list、info、init、validate、check、presets
6. RenderService：统一渲染 API + 任务状态机
7. 渲染队列和 Worker：v1 内存队列 + 同步执行
8. 产物管理：OutputManager + meta.json
9. 第一个示例模板：simple-slideshow（纯视频拼接 + 转场）
10. 第二个示例模板：product-showcase（多层合成 + 字幕 + BGM）
11. CLI render 命令：接通全链路
12. 端到端测试：用示例模板跑通完整流程
