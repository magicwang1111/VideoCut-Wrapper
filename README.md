# VideoCut Wrapper

基于 `Python + FFmpeg` 的 Pipeline 视频渲染工具，支持：

- CLI 渲染
- HTTP API 异步渲染
- SQLite 任务持久化
- 阿里云 OSS 或本地 OSS 模式

当前仓库已经移除 Node.js/Revideo 运行时与 Template 系统，默认只保留 Python Pipeline 版本。

## 依赖

- Python `>= 3.10`
- `ffmpeg`
- `ffprobe`
- 字体文件放在 `fonts/` 下

如果 `ffmpeg` / `ffprobe` 不在 `PATH`，可以通过环境变量指定：

```powershell
$env:FFMPEG_PATH = 'D:\ffmpeg\bin\ffmpeg.exe'
$env:FFPROBE_PATH = 'D:\ffmpeg\bin\ffprobe.exe'
$env:FFMPEG_ENCODER = 'auto'
```

默认 `FFMPEG_ENCODER=auto`，会优先尝试 `h264_nvenc -> h264_qsv -> h264_amf`，都不可用时自动回退到 `libx264`。

## 安装

```bash
pip install -e .
```

如果只想在仓库内直接运行，也可以不安装脚本入口，直接使用：

```bash
python -m videocut --help
```

## 快速开始

```bash
# 1. 查看已注册的 Pipeline
videocut pipelines

# 2. 渲染（直接传 pipeline 名称和素材路径）
videocut render trim-mixed-concat D:/input/1.mp4 D:/input/2.mp4 D:/input/3.mp4

# 3. 覆盖变量（可选）
videocut render trim-mixed-concat D:/input/1.mp4 D:/input/2.mp4 D:/input/3.mp4 \
  --override trim_start=3 --override transition_1=dissolve
```

输出文件默认位于 `output/<pipeline-name>/final.mp4`。

## CLI

支持的命令：

- `videocut pipelines` — 列出所有已注册的 pipeline（名称 + 配置路径）
- `videocut render <pipeline> <clip1> [clip2 ...] [--override key=val ...]` — 执行渲染
- `videocut presets` — 列出分辨率预设
- `videocut check` — 检查 ffmpeg / ffprobe 可用性
- `videocut serve` — 启动 HTTP API 服务

示例：

```bash
videocut render trim-xfade-concat D:/a.mp4 D:/b.mp4 D:/c.mp4
videocut render flash-black-concat D:/a.mp4 D:/b.mp4 --preset douyin_vertical --quality medium
videocut render zoom-dissolve-concat D:/a.mp4 D:/b.mp4 --override zoom_scale=1.3
videocut serve --host 0.0.0.0 --port 3000
```

## 支持的 Pipeline

当前已注册的 Pipeline：

| Pipeline ID | 转场 | 主要变量 |
|---|---|---|
| `trim-concat` | 直切 | `trim_start` |
| `xfade-concat` | dissolve | `transition_duration` |
| `trim-xfade-concat` | dissolve + trim | `trim_start`, `transition_duration` |
| `flash-black-concat` | flash-black | `transition_duration` |
| `zoom-dissolve-concat` | zoom-dissolve | `transition_duration`, `zoom_scale` |
| `trim-mixed-concat` | 每段独立转场 | `trim_start`, `transition_1`…`transition_5`, `transition_duration` |
| `trim-mixed-dissolve-v1` | flash-black + dissolve | — |

运行 `videocut pipelines` 查看完整列表和配置文件路径。

## Pipeline 配置文件

每个 pipeline 对应 `pipelines/<id>/config.json`，格式如下：

```json
{
  "name": "my-pipeline",
  "mode": "pipeline",
  "preset": "auto",
  "quality": "high",
  "clips": [
    { "trim_start": 2, "trim_end": 0 },
    { "trim_start": 2, "trim_end": 0 },
    { "trim_start": 2, "trim_end": 0 }
  ],
  "default_transition": { "type": "dissolve", "duration": 0.5 },
  "transitions": [
    { "type": "flash-black", "duration": 0.4 },
    { "type": "dissolve",    "duration": 0.6 }
  ],
  "variables": {
    "trim_start": {
      "type": "number",
      "required": false,
      "default": 2,
      "min": 0,
      "max": 30
    },
    "transition_duration": {
      "type": "number",
      "default": 0.5,
      "min": 0.1,
      "max": 2.0
    }
  },
  "overridable": ["trim_start", "transition_duration"]
}
```

### 转场类型

| type | 说明 |
|---|---|
| `cut` | 直切，无转场帧 |
| `dissolve` | alpha 淡入淡出 |
| `flash-black` | 闪黑过渡 |
| `zoom-dissolve` | 放大拉近 + dissolve，可用 `scale` 控制缩放倍率（默认 `1.18`） |

### Variables Schema

`variables` 字段定义可覆盖的参数类型：

| type | 校验规则 | 额外字段 |
|---|---|---|
| `number` | 数值类型，可设 `min` / `max` | `min`, `max` |
| `boolean` | `true` / `false`，或 `0` / `1` | — |
| `select` | 枚举值，必须提供 `options` | `options` |

字段说明：

- `required: true` — 调用时必须显式传入，无默认值时渲染会报错
- `default` — 未传入时使用的默认值
- `overridable` — 列在这里的变量才允许通过 `overrides` 覆盖

### Overrides

运行时覆盖变量，支持 CLI 和 API 两种方式：

```bash
# CLI
videocut render trim-mixed-concat a.mp4 b.mp4 c.mp4 \
  --override trim_start=3 \
  --override transition_1=dissolve \
  --override transition_duration=0.8
```

```json
// API POST /render body
{
  "pipeline": "trim-mixed-concat",
  "clips": ["file1", "file2", "file3"],
  "overrides": {
    "trim_start": 3,
    "transition_1": "dissolve",
    "transition_duration": 0.8,
    "clip_overrides": [
      { "index": 1, "trim_start": 5 }
    ],
    "transition_overrides": [
      { "index": 2, "type": "cut", "duration": 0 }
    ]
  }
}
```

## API

### 启动方式

```bash
videocut serve --host 0.0.0.0 --port 3000
```

服务启动时会做这些事：

1. 初始化日志。
2. 读取环境变量，确定数据库、临时目录、OSS、本地 OSS、worker 数量等配置。
3. 打开 SQLite 任务库，默认是 `data/tasks.db`，也可以用 `DB_PATH` 指定。
4. 初始化 `OssClient`。
5. 启动 `TaskQueue` 和多个 worker 进程。
6. 把上次异常退出时遗留的 `pending` / `rendering` 任务重新放回队列。
7. 清理超过 `TASK_TTL_DAYS` 的历史已完成/失败任务。

代码入口见 [app.py](videocut/api/app.py)、[task_queue.py](videocut/queue/task_queue.py)、[task_store.py](videocut/store/task_store.py)。

### 接口列表

- `GET /health`
- `GET /pipelines`
- `POST /upload`
- `POST /render`
- `GET /tasks/{id}`
- `GET /tasks/{id}/download`

### 鉴权

所有业务接口都要求请求头里带 `X-Api-Key`：

```http
X-Api-Key: your-api-key-here
```

服务端会把环境变量 `API_KEYS` 按逗号拆分，只有命中的 key 才能访问。

### 整体调用流程

完整链路是：

1. 客户端先调用 `/upload` 上传原始素材。
2. 服务端把上传文件写到临时目录，再上传到 OSS 或本地 OSS。
3. 服务端把 `fileId -> ossKey` 的映射写入 SQLite 的 `files` 表。
4. 客户端调用 `/render`，提交 pipeline 名称、素材列表、overrides。
5. `/render` 会把传入的 `fileId` 解析成真实 `ossKey`，生成 `taskId`，并把任务写入 SQLite 的 `tasks` 表，初始状态是 `pending`。
6. `TaskQueue` 把任务投递给空闲 worker。
7. worker 从 OSS 或本地 OSS 下载素材到 `temp/<taskId>/`。
8. worker 调用 `PipelineRunner` 执行渲染（含 trim、转场、BGM 混合）。
9. 渲染完成后，worker 把结果上传到 OSS 或本地 OSS 的 `outputs/<taskId>/final.mp4`。
10. `TaskQueue` 把任务状态更新为 `completed`，并记录输出 `ossKey`。
11. 客户端轮询 `/tasks/{id}` 获取状态。
12. 客户端在任务完成后调用 `/tasks/{id}/download` 下载结果，或者直接使用 `/tasks/{id}` 返回的 `outputUrl`。

### 1. 健康检查

请求：

```bash
curl "http://127.0.0.1:3000/health"
```

返回示例：

```json
{
  "ok": true,
  "workers": 4,
  "queueSize": 0,
  "pipelines": 7
}
```

字段说明：

- `ok`: 服务是否已启动。
- `workers`: 当前 worker 进程数量。
- `queueSize`: 当前等待中的任务数，不含正在渲染的任务。
- `pipelines`: 已注册的 pipeline 数量。

### 2. 查看已注册 Pipeline `GET /pipelines`

请求：

```bash
curl "http://127.0.0.1:3000/pipelines" \
  -H "X-Api-Key: your-api-key-here"
```

返回示例：

```json
[
  {
    "name": "trim-mixed-concat",
    "sourcePath": "/app/pipelines/trim-mixed-concat/config.json",
    "variables": {
      "trim_start": { "type": "number", "default": 2, "min": 0, "max": 30 },
      "transition_duration": { "type": "number", "default": 0.5 }
    },
    "overridable": ["trim_start", "transition_duration", "transition_1", "transition_2"]
  }
]
```

### 3. 上传素材 `POST /upload`

请求要求：

- `Content-Type` 必须是 `multipart/form-data`
- 表单字段名固定是 `file`
- 单文件大小上限 500 MB
- 当前允许的扩展名：`mp4 mov avi mkv webm mp3 wav aac png jpg jpeg webp`

示例：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: your-api-key-here" \
  -F "file=@input/1.mp4"
```

成功返回示例：

```json
{
  "fileId": "abc123def456",
  "ossKey": "GouMei-Video-Cut/inputs/abc123def456.mp4"
}
```

### 4. 提交渲染 `POST /render`

请求要求：

- `Content-Type` 必须是 `application/json`
- `pipeline` 是 pipeline ID（必填）
- `clips` 是素材列表（必填），每项可以是 `fileId` 或直接的 `ossKey`
- `overrides` 是运行时覆盖参数（可选）

请求体示例：

```json
{
  "pipeline": "trim-mixed-concat",
  "clips": ["abc123def456", "bbb222ccc333", "ddd444eee555"],
  "overrides": {
    "trim_start": 2,
    "transition_1": "flash-black",
    "transition_2": "dissolve",
    "transition_duration": 0.5
  }
}
```

`clips` 判断规则：

- 包含 `/` → 当成 `ossKey` 直接使用
- 否则 → 当成 `fileId`，从 `files` 表查询真实 `ossKey`

示例：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "trim-mixed-concat",
    "clips": ["abc123def456", "bbb222ccc333", "ddd444eee555"],
    "overrides": {
      "trim_start": 2,
      "transition_1": "flash-black",
      "transition_2": "dissolve"
    }
  }'
```

成功返回示例：

```json
{
  "taskId": "t_ab12cd34ef56ab78"
}
```

注意：

- `/render` 只是"创建任务并入队"，不会同步等待渲染结束。
- 任务真正执行发生在 worker 进程里。

### 5. worker 实际做了什么

worker 的逻辑在 [worker_process.py](videocut/queue/worker_process.py)。

拿到任务后，worker 会按下面的顺序执行：

1. 把所有素材从 OSS 或本地 OSS 下载到 `temp/<taskId>/`。
2. 调用 `PipelineRunner` 执行渲染（含 trim、转场、BGM 混合）。
3. 渲染成功后，把结果上传到 `GouMei-Video-Cut/outputs/<taskId>/final.mp4`。
4. 通知 `TaskQueue` 当前任务完成。
5. 删除 `temp/<taskId>/` 临时目录。

如果失败：

- worker 会把失败事件回传给 `TaskQueue`
- `TaskQueue` 会根据 `TASK_MAX_ATTEMPT` 自动重试
- 超过最大重试次数后，任务会被标记为 `failed`

### 6. 查询任务状态 `GET /tasks/{id}`

示例：

```bash
curl "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78" \
  -H "X-Api-Key: your-api-key-here"
```

返回示例：

```json
{
  "taskId": "t_ab12cd34ef56ab78",
  "status": "completed",
  "progress": 100,
  "attempt": 1,
  "createdAt": "2026-04-16T08:00:00.000000",
  "startedAt": "2026-04-16T08:00:01.000000",
  "completedAt": "2026-04-16T08:00:15.000000",
  "outputUrl": "https://...",
  "error": null
}
```

状态字段说明：

- `pending`: 已创建，等待 worker 领取
- `rendering`: 已被 worker 领取，正在下载或渲染
- `completed`: 渲染完成，结果已上传
- `failed`: 渲染失败，且已超过重试次数或不可恢复

### 7. 下载结果 `GET /tasks/{id}/download`

示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: your-api-key-here" \
  -o final.mp4
```

服务端行为：

- 如果任务不存在、未完成、或者没有输出文件，返回 `404 not_found_or_not_ready`
- 如果启用了 `OSS_LOCAL_ROOT`，直接返回本地文件内容
- 如果使用真实 OSS，返回 `302` 跳转到预签名 URL

因此客户端最好：

1. 先轮询 `/tasks/{id}` 直到 `status=completed`
2. 再请求 `/tasks/{id}/download`
3. 或直接使用 `/tasks/{id}` 返回的 `outputUrl`

### 8. 任务状态流转

典型状态流转如下：

```text
upload
  -> fileId + ossKey

render
  -> tasks.status = pending
  -> queue enqueue

worker pick up
  -> tasks.status = rendering
  -> attempt + 1

render success
  -> output upload to OSS
  -> tasks.status = completed
  -> progress = 100
  -> oss_key = GouMei-Video-Cut/outputs/<taskId>/final.mp4

render failure
  -> retry if attempt < TASK_MAX_ATTEMPT
  -> else tasks.status = failed
```

### 9. 本地联调推荐流程

如果你不想连真实 OSS，建议启用本地 OSS 模式：

```powershell
$env:API_KEYS = 'demo-key'
$env:OSS_LOCAL_ROOT = 'D:\VideoCut-Wrapper\temp\oss-local'
$env:DB_PATH = 'D:\VideoCut-Wrapper\temp\tasks.db'
videocut serve --port 3000
```

这时：

- `/upload` 会把文件复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/inputs/...`
- worker 会从这个本地目录把素材再下载到 `temp/<taskId>/`
- 渲染结果会写到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/outputs/<taskId>/final.mp4`
- `/tasks/{id}` 返回的 `outputUrl` 是本地绝对路径
- `/tasks/{id}/download` 直接回传本地文件

完整联调顺序建议是：

1. 调 `/health` 确认服务和 worker 已就绪。
2. 调 `/upload` 上传所有素材，记录返回的 `fileId`。
3. 调 `/render` 创建任务，拿到 `taskId`。
4. 轮询 `/tasks/{id}` 直到 `status` 变成 `completed` 或 `failed`。
5. 成功后调 `/tasks/{id}/download`，或者直接取 `outputUrl`。

## 环境变量

参考 `.env.example`：

- `API_KEYS`: 允许访问 API 的 key，逗号分隔
- `FFMPEG_PATH`: 可选，指定 ffmpeg 可执行文件路径
- `FFPROBE_PATH`: 可选，指定 ffprobe 可执行文件路径
- `FFMPEG_ENCODER`: 可选，默认 `auto`，优先探测 GPU 编码器，失败回退到 `libx264`
- `FFMPEG_HWACCEL`: 可选，需要时手动指定 `cuda` 等硬件解码参数
- `PIPELINES_DIR`: 可选，pipeline 配置目录，默认 `pipelines/`
- `OSS_ENDPOINT`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`
- `OSS_PREFIX`
- `OSS_LOCAL_ROOT`: 本地 OSS 模式根目录，设置后不会访问阿里云 OSS
- `WORKER_COUNT`
- `QUEUE_MAX`
- `TASK_MAX_ATTEMPT`
- `TASK_TTL_DAYS`
- `DB_PATH`
- `TEMP_DIR`

## 本地 API 测试

使用 `api-test/http_api_test_client.py`：

```bash
# 单组素材，默认使用 trim-mixed-concat pipeline
python api-test/http_api_test_client.py --group 1

# 指定 pipeline
python api-test/http_api_test_client.py --pipeline zoom-dissolve-concat --group 2

# 批量并发，5 组
python api-test/http_api_test_client.py --groups 1,2,3,4,5
```

需要先设置环境变量：

```powershell
$env:API_BASE_URL = 'http://127.0.0.1:3000'
$env:API_KEY = 'demo-key'
```

## 仓库结构

```text
videocut/            Python 运行时
  pipeline/          Pipeline 引擎（config、runner、registry、types）
  api/               HTTP API (FastAPI)
  queue/             TaskQueue + worker_process
  store/             SQLite TaskStore
  render/            ffmpeg 工具函数
pipelines/           Pipeline 配置目录（每个子目录一份 config.json）
input/               测试素材
output/              渲染输出
temp/                临时目录
fonts/               字体目录
api-test/            API 集成测试客户端
```

## 验证建议

```bash
videocut check
videocut pipelines
videocut render trim-xfade-concat D:/a.mp4 D:/b.mp4 D:/c.mp4 --preview
```

启动 API 后验证完整链路：

```bash
videocut serve --port 3000
# 另一个终端
python api-test/http_api_test_client.py --group 1
```

## Docker

推荐的容器化文件已经补齐：

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`

Linux / image 部署建议：

1. 先复制环境变量模板：

```bash
cp .env.example .env
```

2. 按实际部署方式编辑 `.env`：

- 如果接阿里云 OSS：
  - 填好 `OSS_ENDPOINT`
  - 填好 `OSS_ACCESS_KEY_ID`
  - 填好 `OSS_ACCESS_KEY_SECRET`
  - 保持 `OSS_LOCAL_ROOT=` 为空
  - 保持 `SYNC_BGM_ON_STARTUP=1`
- 如果只是单机或联调：
  - 设置 `OSS_LOCAL_ROOT=/srv/videocut/oss-local`
  - OSS AK/SK 可以留空
  - 如果不从真实 OSS 拉 BGM，设置 `SYNC_BGM_ON_STARTUP=0`

3. 先构建基础镜像：

```bash
docker build -f docker/base/Dockerfile -t magicwang/pytorch-base:torch210-cu128-runtime-v1 .
```

4. 启动业务容器：

```bash
docker compose up -d --build
```

5. 查看服务状态：

```bash
docker compose ps
docker compose logs -f videocut
```

默认挂载目录：

- `./data -> /srv/videocut/data`
- `./temp -> /srv/videocut/temp`
- `./input/bgm -> /app/input/bgm`
- `./output -> /app/output`
- `./fonts -> /app/fonts`
- `./oss-local -> /srv/videocut/oss-local`

说明：

- `data/` 保存 SQLite 任务库
- `temp/` 保存上传临时文件和 worker 下载素材
- `input/bgm/` 保存启动时从 OSS 同步的背景音乐
- `output/` 保存 worker 本地渲染产物，再上传到 OSS / 本地 OSS
- `fonts/` 用于自定义字体
- `oss-local/` 只在本地 OSS 模式下使用

容器内已经安装：

- Python 3.11 headers
- ffmpeg
- ffprobe
- ossutil
- tzdata
- tini

当前这套 Docker 配置适合标准 Linux 主机和通用 CPU 环境。要点如下：

- `.env.example` 里的路径已经改成 Linux 容器绝对路径
- `Dockerfile` 默认通过 `magicwang/pytorch-base:torch210-cu128-runtime-v1` 构建业务镜像，也可以用 `BASE_IMAGE` 覆盖
- 业务镜像启动前会自动把 `oss://goumee-coze/GouMei-Video-Cut/bgm/` 同步到 `BGM_DIR`
- `WORKER_COUNT` 默认使用 `0`，表示自动按 CPU 数量推导，避免空值导致启动报错
- `FFMPEG_ENCODER` 默认改成 `libx264`，更适合通用 CPU 容器
- `OSS_ENDPOINT` 示例改成公网 endpoint，适合大多数非阿里云内网环境
- 如果你后面要上 GPU 容器，可以再把 `FFMPEG_ENCODER` 改成 `auto` 或 `h264_nvenc`

需要确认的唯一部署前提是：

- 如果不用本地 OSS，就必须在 `.env` 里填真实 OSS 凭证
- 如果部署机器不能访问公网 OSS endpoint，就要改成对应地域的内网 endpoint
