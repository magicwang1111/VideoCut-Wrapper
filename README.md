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
| `avatar-bgm-concat` | 直切 + BGM | 固定裁掉开头 1 秒 |
| `avatar-bgm-concat-2s` | 直切 + BGM | 固定裁掉开头 2 秒 |
| `avatar-bgm-concat-3s` | 直切 + BGM | 固定裁掉开头 3 秒 |
| `bgm-concat` | 直切 + BGM | — |
| `segment-5-6-then-3-5-concat` | 直切 + BGM | 固定取 5 段素材的 5-6 秒和 3-5 秒 |
| `subtitle-burn` | 腾讯 MPS 识别 + 本地 ASS 压制 | 单个 `subtitle-input/` OSS 视频；可选口播、模板或用户上传音乐 |
| `trim-2-5-concat` | 直切 + BGM | 固定取每段 2-5 秒 |
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

`variables` 字段定义 pipeline 配置中的参数元数据：

| type | 校验规则 | 额外字段 |
|---|---|---|
| `number` | 数值类型，可设 `min` / `max` | `min`, `max` |
| `boolean` | `true` / `false`，或 `0` / `1` | — |
| `select` | 枚举值，必须提供 `options` | `options` |

字段说明：

- `required: true` — 参数是否要求提供
- `default` — 默认值
- `overridable` — 可覆盖参数名清单

注意：当前 HTTP API 运行时没有把 `variables` 自动映射成渲染参数。对接时请使用下面结构化的 `clip_overrides`、`transition_overrides`、`default_transition`、`bgm` 等字段；完整说明见 [docs/API.md](docs/API.md)。

### Overrides

运行时覆盖变量，HTTP API 当前支持结构化覆盖字段：

```json
// API POST /render body
{
  "pipeline": "trim-mixed-concat",
  "clips": ["file1", "file2", "file3"],
  "overrides": {
    "quality": "medium",
    "clip_overrides": [
      { "index": 0, "trim_start": 3 },
      { "index": 1, "trim_start": 5 }
    ],
    "transition_overrides": [
      { "index": 0, "type": "dissolve", "duration": 0.8 },
      { "index": 1, "type": "cut", "duration": 0 }
    ],
    "bgm": {
      "category": "calm",
      "filename": "1"
    }
  }
}
```

`GET /bgm` 用来查询实时音乐清单，只展示 `BGM_DIR` 的当前歌曲，不展示 `BGM_BACKUP_DIR` 的归档歌曲；`overrides.bgm.category + filename` 用来指定某一首 BGM，其中 `filename` 是不带扩展名的歌曲 ID。只指定 `category` 时，会在该分类目录下随机选择一首；都不传时保持全目录随机选择。

后台模板音乐使用隐藏目录 `BGM_TEMPLATE_DIR=/app/input/bgm-templete` 和 OSS 前缀 `BGM_TEMPLATE_OSS_URI=oss://goumee-coze/GouMei-Video-Cut/bgm-templete/`。口播音乐使用 `BGM_AVATAR_DIR=/app/input/bgm-avatar` 和 `BGM_AVATAR_OSS_URI=oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/`。`SYNC_BGM_ON_STARTUP=1` 会按普通、备份、模板、口播顺序统一增量同步；后台发布模板后仍可调用 `POST /admin/bgm-template/sync` 立即同步。

## API

完整、按当前代码校对过的对接文档见 [docs/API.md](docs/API.md)。下面是仓库 README 内的概要说明。

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
7. 清理超过 `TASK_TTL_DAYS` 的历史已完成/失败任务，以及过期的用户临时音频记录。

代码入口见 [app.py](videocut/api/app.py)、[task_queue.py](videocut/queue/task_queue.py)、[task_store.py](videocut/store/task_store.py)。

### 接口列表

- `GET /health`
- `GET /bgm`
- `POST /upload`
- `POST /admin/bgm-template/sync`
- `POST /render`
- `GET /tasks/summary`
- `GET /tasks/active`
- `GET /tasks/{id}`
- `GET /tasks/{id}/download`

注意：当前 `videocut/api/app.py` 没有实现 `GET /pipelines`。如需查看已注册 pipeline，请使用 `videocut pipelines` 或查看 `pipelines/*/config.json`。

### 鉴权

所有业务接口都要求请求头里带 `X-Api-Key`：

```http
X-Api-Key: goumee-music
```

服务端会把环境变量 `API_KEYS=goumee-music` 作为允许访问的 key。

### 整体调用流程

完整链路是：

1. 客户端先调用 `/upload` 上传原始素材；如果需要使用用户自带音乐，也用 `/upload` 上传音频。
2. 服务端把上传文件写到临时目录，再上传到 OSS 或本地 OSS。
3. 服务端把 `fileId -> ossKey` 的映射和文件类型写入 SQLite 的 `files` 表。
4. 如果需要指定曲库 BGM，客户端先调用 `/bgm` 查询当前音乐清单；如果使用用户上传音频，则跳过 `/bgm`。
5. 客户端调用 `/render`，提交 pipeline 名称、素材列表、overrides；视频素材放 `clips`，用户音频放 `overrides.bgm.fileId`。
6. `/render` 会把传入的 `fileId` 解析成真实 `ossKey`，生成 `taskId`，并把任务写入 SQLite 的 `tasks` 表，初始状态是 `pending`。
7. `TaskQueue` 把任务投递给空闲 worker。
8. worker 从 OSS 或本地 OSS 下载素材到 `temp/<taskId>/`。
9. worker 调用 `PipelineRunner` 执行渲染（含 trim、转场、BGM 混合）。
10. 渲染完成后，worker 把结果上传到 OSS 或本地 OSS 的 `outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4`，其中时间戳使用北京时间（Asia/Shanghai）。
11. `TaskQueue` 把任务状态更新为 `completed`，并记录输出 `ossKey`。
12. 客户端轮询 `/tasks/{id}` 获取状态。
13. 客户端在任务完成后调用 `/tasks/{id}/download` 下载结果，或者直接使用 `/tasks/{id}` 返回的 `outputUrl`。

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
  "pipelines": 14
}
```

字段说明：

- `ok`: 服务是否已启动。
- `workers`: 当前 worker 进程数量。
- `queueSize`: 当前等待中的任务数，不含正在渲染的任务。
- `pipelines`: 已注册的 pipeline 数量。

### 2. 查询音乐列表 `GET /bgm`

示例：

```bash
curl "http://127.0.0.1:3000/bgm" \
  -H "X-Api-Key: goumee-music"
```

返回示例：

```json
{
  "bgmRoot": "/app/input/bgm",
  "categories": [
    {"name": "calm", "displayName": "舒缓", "count": 5},
    {"name": "intense", "displayName": "激烈", "count": 5}
  ],
  "files": [
    {"category": "calm", "displayName": "舒缓", "filename": "1", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3"},
    {"category": "intense", "displayName": "激烈", "filename": "2", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/intense/2.mp3"}
  ]
}
```

`files[].category + files[].filename` 可直接用于 `/render` 的 `overrides.bgm`，`filename` 不带扩展名，`displayName` 是展示名，`files[].ossUrl` 是可直接下载的 OSS HTTPS 地址。

### 2.1 查询口播音乐列表 `GET /bgm-avatar`

```bash
curl "http://127.0.0.1:3000/bgm-avatar" \
  -H "X-Api-Key: goumee-music"
```

响应结构与 `GET /bgm` 相同，但只返回 `oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/` 同步到本地口播目录的歌曲。`subtitle-burn` 使用时必须显式传 `source: "bgm-avatar"`、`category` 和 `filename`。

### 3. 查看已注册 Pipeline

当前 HTTP API 未开放 pipeline 列表接口。可用下面任一方式查看：

```bash
videocut pipelines
```

或直接查看：

```text
pipelines/*/config.json
```

### 4. 上传素材或用户音频 `POST /upload`

请求要求：

- `Content-Type` 必须是 `multipart/form-data`
- 表单字段名固定是 `file`
- 单文件大小上限 500 MB
- 当前允许的素材扩展名：`mp4 mov avi mkv webm png jpg jpeg webp`
- 当前允许的用户音频扩展名：`mp3 wav aac ogg flac m4a`

示例：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@input/1.mp4"
```

成功返回示例：

```json
{
  "fileId": "abc123def456",
  "ossKey": "GouMei-Video-Cut/inputs/abc123def456.mp4",
  "kind": "asset"
}
```

上传用户音频也使用同一个接口：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/Furious.mp3"
```

用户音频返回示例：

```json
{
  "fileId": "audio123def45",
  "ossKey": "GouMei-Video-Cut/user-audio/audio123def45.mp3",
  "kind": "user_audio",
  "expiresAt": "2026-06-03T12:00:00.000000"
}
```

用户音频不会进入 `/bgm` 曲库，渲染时不要放进 `clips`。

### 5. 提交渲染 `POST /render`

请求要求：

- `Content-Type` 必须是 `application/json`
- `pipeline` 是 pipeline ID（必填）
- `clips` 是视频/图片素材列表（必填），每项可以是 `fileId` 或直接的 `ossKey`
- `overrides` 是运行时覆盖参数（可选）

请求体示例：

```json
{
  "pipeline": "trim-mixed-concat",
  "clips": ["abc123def456", "bbb222ccc333", "ddd444eee555"],
  "overrides": {
    "clip_overrides": [
      { "index": 0, "trim_start": 2 }
    ],
    "transition_overrides": [
      { "index": 0, "type": "flash-black", "duration": 0.5 },
      { "index": 1, "type": "dissolve", "duration": 0.5 }
    ],
    "bgm": {
      "category": "calm",
      "filename": "1"
    }
  }
}
```

`clips` 判断规则：

- 包含 `/` → 当成 `ossKey` 直接使用
- 否则 → 当成 `fileId`，从 `files` 表查询真实 `ossKey`
- 用户上传音频不能放进 `clips`，只能放在 `overrides.bgm.fileId`

使用用户上传音频时：

```json
{
  "pipeline": "bgm-concat",
  "clips": ["videoFileId1", "videoFileId2"],
  "overrides": {
    "bgm": {
      "fileId": "audio123def45"
    }
  }
}
```

`overrides.bgm.fileId` 只接受音频 `fileId` 一个字段；不要同时传 `volume`、`fade_out`、`category` 或 `filename`。音量使用 pipeline 默认配置。字段名必须是驼峰 `fileId`，`file_id` 是非法字段。

示例：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "trim-mixed-concat",
    "clips": ["abc123def456", "bbb222ccc333", "ddd444eee555"],
    "overrides": {
      "clip_overrides": [
        { "index": 0, "trim_start": 2 }
      ],
      "transition_overrides": [
        { "index": 0, "type": "flash-black", "duration": 0.5 },
        { "index": 1, "type": "dissolve", "duration": 0.5 }
      ],
      "bgm": {
        "category": "calm",
        "filename": "1"
      }
    }
  }'
```

成功返回示例：

```json
{
  "taskId": "t_ab12cd34ef56ab78"
}
```

常见错误码：

| HTTP | `error_code` | 场景 | 处理建议 |
|---:|---:|---|---|
| `400` | `2001` | `/render` 缺少 `pipeline` 或 `clips` | 修正请求体 |
| `400` | `2002` | `clips` 中存在本地路径、URL、非法 OSS key，或把用户音频当素材传入 | 改传合法素材 OSS key 或素材 `fileId` |
| `400` | `2007` | `overrides.bgm` 字段结构、未知字段或混传错误 | 修正 BGM 参数，用户音频字段必须是 `fileId` |
| `400` | `2008` | `overrides.bgm.fileId` 不存在或不是用户音频 | 重新上传音频并传音频 `fileId` |
| `503` | `3002` | 渲染队列已满 | 稍后重试 |

注意：

- `/render` 只是"创建任务并入队"，不会同步等待渲染结束。
- 任务真正执行发生在 worker 进程里。

### 6. worker 实际做了什么

worker 的逻辑在 [worker_process.py](videocut/queue/worker_process.py)。

拿到任务后，worker 会按下面的顺序执行：

1. 把所有素材从 OSS 或本地 OSS 下载到 `temp/<taskId>/`。
2. 调用 `PipelineRunner` 执行渲染（含 trim、转场、BGM 混合）。
3. 渲染成功后，把结果上传到 `GouMei-Video-Cut/outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4`，其中时间戳使用北京时间（Asia/Shanghai）。
4. 通知 `TaskQueue` 当前任务完成。
5. 删除 `temp/<taskId>/` 临时目录。

如果失败：

- worker 会把失败事件回传给 `TaskQueue`
- `TaskQueue` 会根据 `TASK_MAX_ATTEMPT` 自动重试
- 超过最大重试次数后，任务会被标记为 `failed`

### 7. 查询整体任务状态 `GET /tasks/summary`

示例：

```bash
curl "http://127.0.0.1:3000/tasks/summary" \
  -H "X-Api-Key: goumee-music"
```

返回示例：

```json
{
  "generatedAt": "2026-05-20T18:44:36.000000",
  "workers": 4,
  "queueSize": 2,
  "counts": {
    "total": 12,
    "pending": 2,
    "rendering": 1,
    "completed": 8,
    "failed": 1
  }
}
```

### 8. 查询未结束任务 `GET /tasks/active`

示例：

```bash
curl "http://127.0.0.1:3000/tasks/active" \
  -H "X-Api-Key: goumee-music"
```

只返回 `pending` 和 `rendering` 任务，按创建时间升序排列。

### 9. 查询任务状态 `GET /tasks/{id}`

示例：

```bash
curl "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78" \
  -H "X-Api-Key: goumee-music"
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

时间字段为北京时间。

状态字段说明：

- `pending`: 已创建，等待 worker 领取
- `rendering`: 已被 worker 领取，正在下载或渲染
- `completed`: 渲染完成，结果已上传
- `failed`: 渲染失败，且已超过重试次数或不可恢复

### 10. 下载结果 `GET /tasks/{id}/download`

示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: goumee-music" \
  -o final.mp4
```

服务端行为：

- 如果任务不存在、未完成、或者没有输出文件，返回 HTTP `404`，响应体 `error_code=3001`
- 如果启用了 `OSS_LOCAL_ROOT`，直接返回本地文件内容
- 如果使用真实 OSS，返回 `302` 跳转到 `OSS_PUBLIC_ENDPOINT` 拼接的公网对象 URL

因此客户端最好：

1. 先轮询 `/tasks/{id}` 直到 `status=completed`
2. 再请求 `/tasks/{id}/download`
3. 或直接使用 `/tasks/{id}` 返回的 `outputUrl`

### 11. 任务状态流转

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
  -> oss_key = GouMei-Video-Cut/outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4  # YYYYMMDD_HHMMSS 使用北京时间

render failure
  -> retry if attempt < TASK_MAX_ATTEMPT
  -> else tasks.status = failed
```

### 12. 本地联调推荐流程

如果你不想连真实 OSS，建议启用本地 OSS 模式：

```powershell
$env:API_KEYS = 'goumee-music'
$env:OSS_LOCAL_ROOT = 'D:\VideoCut-Wrapper\temp\oss-local'
$env:DB_PATH = 'D:\VideoCut-Wrapper\temp\tasks.db'
videocut serve --port 3000
```

这时：

- `/upload` 会把素材复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/inputs/...`，把用户音频复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/user-audio/...`
- worker 会从这个本地目录把素材再下载到 `temp/<taskId>/`
- 渲染结果会写到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4`，时间戳使用北京时间（Asia/Shanghai）
- `/tasks/{id}` 返回的 `outputUrl` 是本地绝对路径
- `/tasks/{id}/download` 直接回传本地文件

真实 OSS 模式下，用户临时音频写入当前 bucket 的 `GouMei-Video-Cut/user-audio/` 前缀。建议在 OSS 控制台给该前缀配置生命周期规则，到期自动删除，不需要新建 bucket。

完整联调顺序建议是：

1. 调 `/health` 确认服务和 worker 已就绪。
2. 调 `/upload` 上传所有素材，记录返回的 `fileId`。
3. 如需指定 BGM，调 `/bgm` 查询 `category + filename`。
4. 调 `/render` 创建任务，拿到 `taskId`。
5. 轮询 `/tasks/{id}` 直到 `status` 变成 `completed` 或 `failed`。
6. 成功后调 `/tasks/{id}/download`，或者直接取 `outputUrl`。

## 环境变量

参考 `.env.example`：

本机执行 `python -m videocut ...` 和容器内执行服务时，程序会自动读取仓库根目录的 `.env`。如果同名变量已经通过 PowerShell、Docker `environment` 或 `env_file` 注入，则以已存在的环境变量为准。

- `API_KEYS`: 允许访问 API 的 key，当前对接文档固定使用 `goumee-music`
- `FFMPEG_PATH`: 可选，指定 ffmpeg 可执行文件路径
- `FFPROBE_PATH`: 可选，指定 ffprobe 可执行文件路径
- `FFMPEG_ENCODER`: 可选，默认 `auto`，优先探测 GPU 编码器，失败回退到 `libx264`
- `FFMPEG_HWACCEL`: 可选，需要时手动指定 `cuda` 等硬件解码参数
- `PIPELINES_DIR`: 可选，pipeline 配置目录，默认 `pipelines/`
- `OSS_ENDPOINT`
- `OSS_PUBLIC_ENDPOINT`: 可选，`GET /bgm` 的 `ossUrl` 和任务完成后的 `outputUrl` 使用的公网 endpoint
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`
- `OSS_PREFIX`
- `OSS_LOCAL_ROOT`: 本地 OSS 模式根目录，设置后不会访问阿里云 OSS
- `BGM_TEMPLATE_DIR`: 后台模板音乐本地目录，默认 `/app/input/bgm-templete`
- `BGM_TEMPLATE_OSS_URI`: 后台模板音乐 OSS 同步源，默认 `oss://goumee-coze/GouMei-Video-Cut/bgm-templete/`
- `BGM_AVATAR_DIR`: 口播音乐本地目录，默认 `/app/input/bgm-avatar`
- `BGM_AVATAR_OSS_URI`: 口播音乐 OSS 同步源，默认 `oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/`
- `BGM_TEMPLATE_SYNC_TIMEOUT_SECONDS`: 模板音乐同步超时时间，默认 `600`
- `WORKER_COUNT`
- `QUEUE_MAX`
- `TASK_MAX_ATTEMPT`
- `TASK_TTL_DAYS`
- `UPLOAD_TTL_DAYS`: 用户临时音频记录保留天数，默认跟随 `TASK_TTL_DAYS` 或 `7`
- `DB_PATH`
- `TEMP_DIR`

## 本地 API 测试

测试功能已经拆成多个小脚本，每个脚本最上面都写明了测试目的和本脚本自己的配置。

```bash
# 健康检查
python api-test/check_health.py

# 指定某一首 BGM，默认使用 舒缓/1.mp3
python api-test/render_bgm_file.py

# 随机 BGM
python api-test/render_bgm_random.py

# 批量测试 1-5 组，下载结果
python api-test/render_groups_1_5.py

# 批量测试 1-16 组，不下载结果
python api-test/render_groups_1_16.py

# 换 pipeline 测试
python api-test/render_trim_mixed.py
python api-test/render_zoom_dissolve.py
```

需要先设置环境变量：

```powershell
$env:API_BASE_URL = 'http://127.0.0.1:3000'
$env:API_KEY = 'goumee-music'
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
python api-test/render_bgm_file.py
```

## Docker

推荐的容器化文件已经补齐：

- `Dockerfile`
- `docker-compose.yml`
- `docker-compose.gpu.yml`
- `.dockerignore`
- `.env.example`

Linux / image 部署建议：

详细操作手册见 [docs/DOCKER_DEPLOY.md](docs/DOCKER_DEPLOY.md)。

1. 先复制环境变量模板：

```bash
cp .env.example .env
```

2. 按实际部署方式编辑 `.env`：

- 如果接阿里云 OSS：
  - 填好 `OSS_ENDPOINT`
  - 如 `OSS_ENDPOINT` 使用内网地址，另填 `OSS_PUBLIC_ENDPOINT=oss-cn-hangzhou.aliyuncs.com`
  - 填好 `OSS_ACCESS_KEY_ID`
  - 填好 `OSS_ACCESS_KEY_SECRET`
  - 保持 `OSS_LOCAL_ROOT=` 为空
  - 保持 `SYNC_BGM_ON_STARTUP=1`，统一同步普通、备份、模板和口播音乐
- 如果只是单机或联调：
  - 设置 `OSS_LOCAL_ROOT=/srv/videocut/oss-local`
  - OSS AK/SK 可以留空
  - 如果不从真实 OSS 拉 BGM，设置 `SYNC_BGM_ON_STARTUP=0`

3. 先构建基础镜像：

```bash
docker build -f docker/base/Dockerfile -t magicwang/pytorch-base:torch210-cu128-runtime-v1 .
```

4. 构建并启动业务容器。建议每次正式打包都给业务镜像一个递增 tag，例如 `v1`、`v2`、`v3`。

如果是导出镜像包交付，建议使用打包脚本；它会先扫描本地 `input/bgm` 并刷新 `docs/BGM_MANIFEST.json`：

```bash
./docker/build_image.sh v1
```

CPU-only Linux 机器：

```bash
IMAGE_TAG=v1 IMAGE_DESCRIPTION="GPU NVENC Docker packaging fix" docker compose up -d --build
```

NVIDIA GPU Linux 机器：

```bash
IMAGE_TAG=v1 IMAGE_DESCRIPTION="GPU NVENC Docker packaging fix" docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

构建完成后可以查看这一版镜像记录的说明：

```bash
docker inspect videocut-wrapper:v1 --format '{{ index .Config.Labels "org.opencontainers.image.description" }}'
```

`docker-compose.yml` 会通过 `env_file: .env` 注入配置；如果你用 `docker run` 方式启动，也可以使用 `--env-file .env`，或者把 `.env` 挂载到 `/app/.env`，entrypoint 会在同步 BGM 前读取它。

5. 查看服务状态：

```bash
docker compose ps
docker compose logs -f videocut
```

默认挂载目录：

- `./data -> /srv/videocut/data`
- `./temp -> /srv/videocut/temp`
- `./input/bgm -> /app/input/bgm`
- `./input/bgm-backup -> /app/input/bgm-backup`
- `./input/bgm-templete -> /app/input/bgm-templete`
- `./input/bgm-avatar -> /app/input/bgm-avatar`
- `./output -> /app/output`
- `./fonts -> /app/fonts`
- `./oss-local -> /srv/videocut/oss-local`

说明：

- `data/` 保存 SQLite 任务库
- `temp/` 保存上传临时文件和 worker 下载素材
- `input/bgm/` 保存启动时从 OSS 同步的背景音乐
- `input/bgm-templete/` 保存容器启动或后台接口同步的隐藏模板音乐
- `input/bgm-avatar/` 保存容器启动时从 OSS 同步的口播专用音乐
- `output/` 保存 worker 本地渲染产物，再上传到 OSS / 本地 OSS
- `fonts/` 用于自定义字体
- `oss-local/` 只在本地 OSS 模式下使用

容器内已经安装：

- Python 3.12 virtualenv
- ffmpeg 7.x with `h264_nvenc`
- ffprobe
- ossutil
- tzdata
- tini

当前这套 Docker 配置面向 Linux 部署环境；Windows Docker Desktop / WSL 主要用于本地排查和构建验证。要点如下：

- `.env.example` 里的路径已经改成 Linux 容器绝对路径
- `Dockerfile` 默认通过 `magicwang/pytorch-base:torch210-cu128-runtime-v1` 构建业务镜像，也可以用 `BASE_IMAGE` 覆盖
- `docker-compose.yml` 可在 CPU-only Linux 机器上直接启动
- `docker-compose.gpu.yml` 负责 GPU 资源申请，并注入 `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`
- GPU Linux 部署机需要先安装 NVIDIA 驱动和 NVIDIA Container Toolkit，确保 `docker run --gpus all ... nvidia-smi` 可用
- `IMAGE_TAG` 用于业务镜像版本，例如 `videocut-wrapper:v1`
- `IMAGE_DESCRIPTION` 会写入镜像 label，方便 `docker inspect` 查看这一版做了什么
- 业务镜像启动前会自动同步公开 BGM、backup 和隐藏模板音乐；同步固定使用 `ossutil sync -u -f`
- 没有挂载持久化目录时，删除并重建容器会重新下载 OSS 音乐；挂载目录或仅重启原容器时，`-u` 会跳过相同文件
- `WORKER_COUNT` 默认使用 `0`，表示自动按 CPU 数量推导，避免空值导致启动报错
- `FFMPEG_ENCODER` 默认使用 `auto`，会优先探测 `h264_nvenc`，不可用时回退到 `libx264`
- `OSS_ENDPOINT` 示例改成公网 endpoint，适合大多数非阿里云内网环境
- 如果部署到 CPU-only 机器，可以继续保持 `FFMPEG_ENCODER=auto`，容器内会探测失败后自动使用 `libx264`

需要确认的唯一部署前提是：

- 如果不用本地 OSS，就必须在 `.env` 里填真实 OSS 凭证
- 如果部署机器不能访问公网 OSS endpoint，就要改成对应地域的内网 endpoint
