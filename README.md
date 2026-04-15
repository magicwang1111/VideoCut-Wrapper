# VideoCut Wrapper

模板化视频剪辑系统。通过 YAML 配置文件 + 可插拔模板，用一条命令批量渲染短视频。

## 依赖

- **Node.js** >= 18
- **FFmpeg**（FFmpeg 系模板必须，需在 PATH 中或通过环境变量指定）
- **Chrome/Chromium**（Revideo 系模板必须）

```bash
# 检查依赖状态
npx tsx src/cli.ts check
```

FFmpeg 不在 PATH 时，可通过环境变量指定路径：

```bash
set FFMPEG_PATH=D:\ffmpeg\bin\ffmpeg.exe
set FFPROBE_PATH=D:\ffmpeg\bin\ffprobe.exe
```

## 安装

```bash
npm install
```

## 快速开始

```bash
# 1. 查看可用模板
npx tsx src/cli.ts list

# 2. 创建项目（以 trim-xfade-concat 为例）
npx tsx src/cli.ts init trim-xfade-concat my-project

# 3. 编辑 projects/my-project/config.yaml，填入素材路径

# 4. 渲染
npx tsx src/cli.ts render projects/my-project/config.yaml
```

输出文件位于 `output/<project-name>/final.mp4`，同目录还有 `meta.json` 记录本次渲染参数。

---

## CLI 命令

| 命令 | 说明 |
|------|------|
| `list` | 列出所有已注册模板 |
| `info <template-id>` | 查看模板变量详情 |
| `init <template-id> <project-name>` | 创建项目目录和配置文件 |
| `validate <config>` | 校验配置（不渲染） |
| `render <config>` | 渲染视频 |
| `presets` | 列出所有分辨率和质量预设 |
| `check` | 检查系统依赖 |

### render 选项

```bash
npx tsx src/cli.ts render projects/my-project/config.yaml \
  --preset douyin_vertical \   # 覆盖分辨率预设
  --quality medium \           # 覆盖质量
  --preview                    # 快速预览（半分辨率）
```

---

## 项目配置文件

每个项目一个 `config.yaml`（也支持 `.json`）：

```yaml
template: "trim-xfade-concat"   # 模板 ID（必填）

preset: "auto"                  # 分辨率预设（可选，默认 auto）
quality: "high"                 # 质量（可选，默认 high）

variables:
  clip_1: "materials/a.mp4"     # 相对路径或绝对路径
  clip_2: "D:/videos/b.mp4"
  trim_start: 2
  transition_duration: 0.5
```

素材路径解析顺序：**项目目录 → 模板目录 → 仓库根目录**。

---

## 分辨率预设

| 预设名 | 尺寸 | 帧率 | 说明 |
|--------|------|------|------|
| `auto` | — | — | 自动探测第一个视频的分辨率和帧率（默认） |
| `douyin_vertical` | 1080×1920 | 30 | 抖音/快手竖屏 |
| `douyin_horizontal` | 1920×1080 | 30 | 抖音/快手横屏 |
| `xiaohongshu_square` | 1080×1080 | 30 | 小红书正方形 |
| `xiaohongshu_vertical` | 1080×1440 | 30 | 小红书竖屏 3:4 |
| `preview` | 540×960 | 24 | 快速预览（半分辨率） |

## 质量预设

| 预设名 | CRF | 说明 |
|--------|-----|------|
| `low` | 28 | 低质量，文件小，编码快 |
| `medium` | 23 | 中等质量 |
| `high` | 18 | 高质量（默认） |

---

## 内置模板

### `trim-concat` — 裁头拼接

裁去每段视频开头固定 2 秒后顺序拼接（无转场）。直接由 FFmpeg 完成，速度快。

| 变量 | 类型 | 说明 |
|------|------|------|
| `clip_1` ~ `clip_6` | video | 视频片段，clip_1 必填 |

### `xfade-concat` — 叠化拼接

多段视频按顺序拼接，前一段尾部淡出，下一段在转场期间只显示首帧静帧，不做淡入；若长度不足，会自动复制边缘帧创建转场并保持总时长不变。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` ~ `clip_6` | video | — | 视频片段，clip_1 必填 |
| `transition_duration` | number | 0.5 | 转场时长（秒，0.1–3.0） |

### `trim-xfade-concat` — 裁头叠化拼接

裁去每段视频开头 N 秒后，再按“前一段尾部淡出 + 下一段首帧静帧垫底”的方式拼接；若长度不足，会自动复制边缘帧创建转场并保持总时长不变。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` ~ `clip_6` | video | — | 视频片段，clip_1 必填 |
| `trim_start` | number | 2 | 裁去开头秒数（0–10） |
| `transition_duration` | number | 0.5 | 转场时长（秒，0.1–3.0） |

### `zoom-dissolve-concat` — 拉近叠化拼接

多段视频之间先做由四周向画面中心收束的拉近，再与下一段进行叠化转场，适合更明显的剪映式拉近叠化节奏。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` ~ `clip_6` | video | — | 视频片段，clip_1 必填 |
| `transition_duration` | number | 0.4 | 拉近与叠化时长（秒，0.1–3.0） |
| `zoom_scale` | number | 1.18 | 放大倍数（建议 1.1–1.3） |

### `simple-slideshow` — 简单轮播

2–3 段视频依次播放，支持可选叠化转场。由 Revideo 渲染引擎处理。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` / `clip_2` | video | — | 必填 |
| `clip_3` | video | — | 可选第三段 |
| `clip_1_duration` ~ `clip_3_duration` | number | 0 | 各段时长（0 = 使用原始时长） |
| `transition_duration` | number | 0.5 | 转场时长（秒） |
| `enable_transition` | boolean | true | 是否启用转场 |

### `product-showcase` — 产品展示

三场景产品展示，含 Logo 叠加、字幕、背景音乐。由 Revideo 渲染引擎处理。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_intro` | video | — | 开场视频（必填） |
| `clip_detail` | video | — | 产品特写（必填） |
| `clip_outro` | video | — | 结尾视频（必填） |
| `product_name` | text | 产品名称 | 产品名 |
| `price_text` | text | ¥99 | 价格文案 |
| `outro_text` | text | 关注我们… | 结尾文案 |
| `logo` | image | — | 品牌 Logo |
| `bgm` | audio | — | 背景音乐 |
| `subtitle_color` | color | #FFFFFF | 字幕颜色 |
| `intro_duration` | number | 3 | 开场时长（秒） |
| `detail_duration` | number | 5 | 特写时长（秒） |
| `outro_duration` | number | 3 | 结尾时长（秒） |
| `enable_bgm` | boolean | true | 启用背景音乐 |
| `enable_transition` | boolean | true | 启用转场 |
| `bgm_volume` | number | 0.3 | 背景音乐音量（0–1） |

### `flash-black-concat` — 闪黑拼接

每段视频末尾淡出至黑、下一段从黑淡入，营造"闪黑"节奏感。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` ~ `clip_6` | video | — | 视频片段，clip_1 必填 |
| `transition_duration` | number | 0.5 | 闪黑时长（秒，0.1–2.0） |

### `trim-mixed-concat` — 混合转场拼接

裁去每段开头，每个连接点可独立指定转场类型（flash-black / dissolve / cut），全局共享一个转场时长。最多支持 6 段。

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `clip_1` ~ `clip_6` | video | — | 视频片段，clip_1 必填 |
| `trim_start` | number | 2 | 裁去开头秒数（所有片段共用） |
| `transition_1` ~ `transition_5` | select | flash-black | 各连接点转场类型 |
| `transition_duration` | number | 0.5 | 转场时长（秒，所有连接点共用） |

---

## Pipeline 模式

Pipeline 是一种不依赖模板的灵活拼接方式。每段素材可独立设置剪头/剪尾时长，每个连接点可独立设置转场类型和时长，片段数量不限。**修改剪辑组合只需改 `config.yaml`，无需改代码。**

### 配置格式

```yaml
mode: pipeline          # 标识为 pipeline 模式，不使用 template 字段

preset: auto            # 分辨率预设（默认 auto，探测第一个片段）
quality: high           # 输出质量（默认 high）

# output:
#   filename: my-cut.mp4  # 自定义输出文件名（默认 final.mp4）

clips:
  - src: "D:/input/1.mp4"
    trim_start: 3         # 剪去开头 N 秒（默认 0）
    trim_end: 0           # 剪去结尾 N 秒（默认 0）

  - src: "D:/input/2.mp4"
    trim_start: 2
    trim_end: 1           # 每段独立设置

  - src: "D:/input/3.mp4"
    trim_start: 1

transitions:
  # transitions[i] = clips[i] 与 clips[i+1] 之间的转场
  # 条目不足时，由 default_transition 补足
  - type: flash-black     # flash-black | dissolve | cut
    duration: 0.5

  - type: dissolve
    duration: 0.8         # 每个连接点独立时长

default_transition:       # 兜底转场（可选）
  type: cut
  duration: 0
```

### 渲染

```bash
npx tsx src/cli.ts render projects/my-pipeline/config.yaml
```

### 与 trim-mixed-concat 的对比

| | `trim-mixed-concat` 模板 | Pipeline 模式 |
|---|---|---|
| 片段数量 | 最多 6 段 | 不限 |
| 每段 trim_start | 所有片段共用一个值 | 每段独立设置 |
| trim_end | 不支持 | 支持 |
| 转场类型 | 每个连接点可独立 | 每个连接点可独立 |
| 转场时长 | 所有连接点共用一个值 | 每个连接点独立设置 |

### 新增转场类型时需要改代码

新增"剪辑/素材组合"只改 `config.yaml`。但若需要实现一种**全新的视觉转场效果**（如擦除、旋转），需同时修改以下 3 处：

1. **`src/pipeline/types.ts`** — 将新类型名加入联合类型：
   ```typescript
   export type PipelineJunctionType = 'flash-black' | 'dissolve' | 'cut' | 'wipe-left';
   ```

2. **`src/pipeline/config.ts`** — 在 `parseJunctionType` 中加入校验：
   ```typescript
   if (raw === 'flash-black' || raw === 'dissolve' || raw === 'cut' || raw === 'wipe-left') return raw;
   ```

3. **`src/pipeline/runner.ts`** — 在 `ffmpegPipelineConcat` 中添加 FFmpeg filter 逻辑（5–15 行）：
   ```typescript
   } else if (t === 'wipe-left') {
     // 实现对应的 FFmpeg filter_complex 片段
   }
   ```

---

## 新增模板

在 `templates/` 下新建目录，包含两个文件：

```
templates/
└── my-template/
    ├── manifest.json   # 模板描述和变量定义
    └── src/
        └── project.ts  # Revideo 入口（FFmpeg 模板可留空占位）
```

**manifest.json 最小示例：**

```json
{
  "id": "my-template",
  "name": "我的模板",
  "description": "模板描述",
  "version": "1.0.0",
  "entry": "src/project.ts",
  "variables": {
    "clip_1": { "type": "video", "label": "第 1 段视频", "required": true }
  }
}
```

`id` 必须唯一，与目录名保持一致。模板会在下次运行 CLI 时自动注册，无需手动配置。

FFmpeg 系模板需要在 `src/render/index.ts` 的 `render()` 方法中添加对应分支处理逻辑；Revideo 系模板直接在 `project.ts` 中实现动画即可。

---

## 仓库结构

```
VideoCut-Wrapper/
├── src/
│   ├── cli.ts              # CLI 入口，所有子命令定义
│   ├── presets.ts          # 分辨率 & 质量预设
│   ├── errors.ts           # 自定义错误类
│   ├── registry/
│   │   ├── index.ts        # 模板注册、扫描、查找
│   │   └── schema-validator.ts
│   ├── project/
│   │   ├── index.ts        # ProjectManager：配置解析 + 素材路径解析
│   │   ├── config-parser.ts
│   │   └── asset-resolver.ts
│   ├── pipeline/
│   │   ├── types.ts        # Pipeline 配置类型定义
│   │   ├── config.ts       # Pipeline YAML 解析 + 路径解析
│   │   ├── runner.ts       # PipelineRunner：FFmpeg filter_complex 构建与执行
│   │   └── index.ts        # 模块导出
│   └── render/
│       ├── index.ts        # RenderService：FFmpeg / Revideo 渲染调度
│       ├── task.ts         # 渲染任务状态管理
│       └── queue.ts
├── templates/              # 内置模板目录（每个子目录一个模板）
│   ├── trim-concat/
│   ├── xfade-concat/
│   ├── trim-xfade-concat/
│   ├── simple-slideshow/
│   └── product-showcase/
├── projects/               # 用户项目（每个子目录一个项目）
│   └── <project-name>/
│       ├── config.yaml
│       └── materials/
├── input/                  # 临时素材存放区（不纳入项目管理）
├── output/                 # 渲染产物
│   └── <project-name>/
│       ├── final.mp4
│       └── meta.json
├── assets/                 # 公共素材（字体、图片等）
├── fonts/                  # 字体文件
├── temp/                   # FFmpeg 中间文件（自动清理）
├── package.json
├── tsconfig.json
└── vite.config.ts
```

---

## API 服务

将渲染功能封装为异步 HTTP API，支持 OSS 文件存储、Worker 子进程池、SQLite 任务持久化。

### 启动

```bash
cp .env.example .env
# 编辑 .env 填入真实凭证（OSS、API_KEYS 等）
npm run start:api
```

服务默认监听 `:3000`，Worker 数量默认为 `max(1, floor(CPU/2))`。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `3000` | HTTP 监听端口 |
| `API_KEYS` | 必填 | 逗号分隔的 API Key 列表 |
| `OSS_ENDPOINT` | 必填 | Aliyun OSS 端点（建议内网端点） |
| `OSS_ACCESS_KEY_ID` | 必填 | OSS AccessKey ID |
| `OSS_ACCESS_KEY_SECRET` | 必填 | OSS AccessKey Secret |
| `OSS_BUCKET` | `goumee-coze` | OSS Bucket 名称 |
| `OSS_PREFIX` | `GouMei-Video-Cut` | OSS 路径前缀 |
| `WORKER_COUNT` | CPU/2 | Worker 子进程数量 |
| `QUEUE_MAX` | `200` | 内存队列容量上限，超出返回 503 |
| `TASK_MAX_ATTEMPT` | `3` | 任务最大重试次数 |
| `TASK_TTL_DAYS` | `7` | 已完成/失败任务清理周期（天） |
| `DB_PATH` | `./data/tasks.db` | SQLite 数据库路径 |
| `TEMP_DIR` | `./temp` | 渲染临时文件目录 |

### 接口速览

所有接口需携带 `X-Api-Key` Header，值为 `API_KEYS` 中的任意一个。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查（无需认证） |
| `POST` | `/upload` | 上传素材文件 → 返回 `fileId` |
| `POST` | `/render` | 提交渲染任务 → 返回 `taskId` |
| `GET` | `/tasks/:id` | 查询任务状态（含预签名下载 URL） |
| `GET` | `/tasks/:id/download` | 302 重定向到结果文件下载地址 |

### 示例流程

```bash
# 1. 上传素材
curl -X POST http://localhost:3000/upload \
  -H "X-Api-Key: your-key" \
  -F "file=@clip1.mp4"
# → {"fileId":"abc123","ossKey":"GouMei-Video-Cut/inputs/abc123.mp4"}

# 2. 提交渲染（clips 填 upload 返回的 fileId）
curl -X POST http://localhost:3000/render \
  -H "X-Api-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"template":"trim-xfade-concat","clips":["abc123","def456"],"params":{"transition":"fade","preset":"fast"}}'
# → {"taskId":"t_1a2b3c4d"}

# 3. 轮询任务状态（status: pending → rendering → completed）
curl http://localhost:3000/tasks/t_1a2b3c4d \
  -H "X-Api-Key: your-key"
# → {"taskId":"t_1a2b3c4d","status":"completed","progress":100,"outputUrl":"https://...?Expires=..."}

# 4. 下载结果（302 重定向）
curl -L http://localhost:3000/tasks/t_1a2b3c4d/download \
  -H "X-Api-Key: your-key" -o result.mp4
```

### 架构说明

- **Worker 隔离**：每个渲染任务在独立子进程中运行，Worker 崩溃不影响其他任务，自动重试（最多 `TASK_MAX_ATTEMPT` 次）
- **队列持久化**：服务重启时自动从 SQLite 重放 `pending`/`rendering` 状态的任务
- **OSS 存储**：输入素材上传到 `{OSS_PREFIX}/inputs/`，输出文件存放在 `{OSS_PREFIX}/outputs/`，下载 URL 有效期 1 小时
- **进度上报**：Worker 通过 IPC 消息上报进度，限流 1 次/秒写入 DB
