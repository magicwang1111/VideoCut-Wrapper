# VideoCut HTTP API 对接文档

本文档面向外部系统对接，按当前仓库代码整理。接口实现主要来自 `videocut/api/app.py`、`videocut/queue/task_queue.py`、`videocut/store/task_store.py` 和 `videocut/oss/client.py`。

## 1. 接口总览

默认服务地址：

```text
http://127.0.0.1:3000
```

启动方式：

```bash
videocut serve --host 0.0.0.0 --port 3000
```

当前业务接口：

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| `GET` | `/health` | 否 | 服务和队列健康检查 |
| `GET` | `/bgm` | 是 | 查询当前可用 BGM 分类和文件清单 |
| `POST` | `/upload` | 是 | 上传本地素材，返回 `fileId` 和 `ossKey` |
| `POST` | `/render` | 是 | 创建异步渲染任务，返回 `taskId` |
| `GET` | `/tasks/summary` | 是 | 查询整体任务状态计数 |
| `GET` | `/tasks/active` | 是 | 查询当前未结束任务 |
| `GET` | `/tasks/{taskId}` | 是 | 查询任务状态、结果地址和失败历史 |
| `GET` | `/tasks/{taskId}/download` | 是 | 下载渲染结果，真实 OSS 模式下可能返回 `302` |

说明：

- FastAPI 默认还会提供 `/docs`、`/redoc`、`/openapi.json`，但 OpenAPI 里不会完整表达 `X-Api-Key` 鉴权规则，对接以本文档为准。
- 当前代码没有实现 `GET /pipelines`。如需查看 pipeline 列表，使用 `videocut pipelines` 或查看 `pipelines/*/config.json`。
- 当前没有任务取消、完整分页任务列表、任务删除、幂等键接口。重复调用 `/render` 会创建多个任务。

## 2. 鉴权

除 `/health` 外，业务接口都需要请求头：

```http
X-Api-Key: goumee-music
```

服务端从环境变量 `API_KEYS` 读取允许的 key，多个 key 用英文逗号分隔：

```bash
API_KEYS=goumee-music
```

鉴权失败返回：

```json
{
  "error_code": 1001,
  "message": "Unauthorized.",
  "details": {}
}
```

HTTP 状态码为 `401`。

### 错误响应约定

HTTP 状态码只表示请求失败的大类。对接方业务逻辑只需要读取响应体里的 `error_code`。

统一错误响应格式：

```json
{
  "error_code": 2002,
  "message": "Invalid clip reference.",
  "details": {
    "value": "D:/tmp/local.mp4"
  }
}
```

错误码分段：

| 分段 | 类型 | 说明 |
|---:|---|---|
| `1000-1999` | 鉴权 / 请求格式错误 | API key、Content-Type、JSON 校验等 |
| `2000-2999` | 业务参数错误 | pipeline、clips、任务 ID 等业务入参问题 |
| `3000-3999` | 任务 / 队列状态错误 | 队列满、任务未完成、下载未就绪等 |
| `9000-9999` | 服务内部错误 | 未预期异常、依赖异常等 |

错误码明细：

| HTTP | `error_code` | 场景 | 处理建议 |
|---:|---:|---|---|
| `401` | `1001` | 缺少或错误的 `X-Api-Key` | 检查 API key |
| `415` | `1002` | `Content-Type` 不符合要求 | 使用接口要求的 `Content-Type` |
| `422` | `1003` | JSON 字段类型校验失败 | 按字段类型修正 |
| `404` | `1004` | 请求路径不存在 | 检查 URL path |
| `400` | `2001` | `/render` 缺少 `pipeline` 或 `clips` | 修正请求体 |
| `400` | `2002` | `clips` 中存在本地路径、URL 或非法 OSS key | 改传合法 OSS key |
| `400` | `2003` | pipeline 不存在 | 使用已注册 pipeline |
| `404` | `2004` | 查询的 `taskId` 不存在 | 检查 `taskId` |
| `400` | `2005` | 上传格式不支持 | 更换文件格式 |
| `400/404` | `2006` | 上传引用的 `fileId` 不存在，或 BGM 目录不存在 | 重新上传、修正 `fileId` 或检查 `BGM_DIR` |
| `404` | `3001` | 下载时任务不存在、未完成或无输出 | 先轮询到 `completed` |
| `503` | `3002` | 渲染队列已满 | 稍后重试 |
| `413` | `3003` | 上传文件过大 | 压缩或拆分文件 |
| `500` | `9001` | 未预期服务端异常 | 记录日志并联系服务方 |

## 3. 推荐对接流程

### 流程 A：素材已经在 OSS 中

如果调用方已经有可读的 OSS key，直接调用 `/render`。这是线上对接最简链路。

```text
GET /health
GET /bgm 可选，获取可指定的 BGM 分类和文件
POST /render
GET /tasks/{taskId} 轮询到 completed 或 failed
GET /tasks/{taskId}/download
```

`clips` 中传入的 OSS key 必须以当前服务的 `OSS_PREFIX` 开头。默认值是：

```text
GouMei-Video-Cut/
```

示例：

```json
{
  "pipeline": "trim-mixed-dissolve-v1",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4",
    "GouMei-Video-Cut/test-input/1/clip_003.mp4"
  ],
  "overrides": {}
}
```

### 流程 B：素材在调用方本地

先上传素材，再用返回的 `fileId` 提交渲染。

```text
GET /health
GET /bgm 可选，获取可指定的 BGM 分类和文件
POST /upload 多次上传素材
POST /render，clips 传 fileId 数组
GET /tasks/{taskId} 轮询到 completed 或 failed
GET /tasks/{taskId}/download
```

## 4. `GET /health`

健康检查不需要鉴权。

请求：

```bash
curl "http://127.0.0.1:3000/health"
```

成功响应：

```json
{
  "ok": true,
  "workers": 4,
  "queueSize": 0,
  "pipelines": 8
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 服务是否启动 |
| `workers` | `number` | worker 进程数量 |
| `queueSize` | `number` | 等待 worker 处理的队列长度，不含正在渲染的任务 |
| `pipelines` | `number` | 服务启动时扫描到的 pipeline 数量 |

## 5. `GET /bgm`

查询当前运行环境下可用的 BGM 分类和文件清单。接口每次请求都会动态扫描 `BGM_DIR`，返回值里的 `category + filename` 可以直接用于 `/render` 的 `overrides.bgm`。

请求：

```bash
curl "http://127.0.0.1:3000/bgm" \
  -H "X-Api-Key: goumee-music"
```

成功响应：

```json
{
  "bgmRoot": "/app/input/bgm",
  "categories": [
    {"name": "激烈", "count": 5},
    {"name": "舒缓", "count": 5}
  ],
  "files": [
    {"category": "激烈", "filename": "2.mp3"},
    {"category": "舒缓", "filename": "1.mp3"}
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bgmRoot` | `string` | 服务端实际扫描的 BGM 根目录，`BGM_DIR` 优先，否则默认 `input/bgm` |
| `categories` | `array` | 分类汇总，`name` 是分类目录，`count` 是该分类下音频数量 |
| `files` | `array` | 音乐文件清单，每项的 `category` 和 `filename` 可传给 `/render` |

目录存在但没有音频文件时，`categories` 和 `files` 返回空数组。目录不存在时返回：

```json
{
  "error_code": 2006,
  "message": "BGM directory not found.",
  "details": {
    "bgmRoot": "/app/input/bgm"
  }
}
```

## 6. `POST /upload`

上传单个素材文件。服务端会写入临时目录，再上传到真实 OSS 或本地 OSS 模式目录，并保存 `fileId -> ossKey` 映射。

请求要求：

| 项 | 要求 |
|---|---|
| `Content-Type` | `multipart/form-data` |
| 表单字段名 | `file` |
| 单文件大小上限 | `500 MB` |
| 支持扩展名 | `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.mp3`, `.wav`, `.aac`, `.png`, `.jpg`, `.jpeg`, `.webp` |

请求示例：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/demo.mp4"
```

成功响应：

```json
{
  "fileId": "abc123def456",
  "ossKey": "GouMei-Video-Cut/inputs/abc123def456.mp4"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `fileId` | `string` | 12 位十六进制字符串，后续 `/render` 可直接引用 |
| `ossKey` | `string` | 素材实际写入的 OSS key |

常见错误：

```json
{
  "error_code": 1002,
  "message": "Unsupported content type.",
  "details": {
    "expected": "multipart/form-data",
    "received": "application/json"
  }
}
```

```json
{
  "error_code": 2005,
  "message": "Unsupported upload format.",
  "details": {
    "ext": ".txt"
  }
}
```

```json
{
  "error_code": 3003,
  "message": "File is too large.",
  "details": {}
}
```

## 7. `POST /render`

创建异步渲染任务。接口只负责校验请求、解析素材引用、写入任务和入队，不会同步等待渲染完成。

请求头：

```http
X-Api-Key: goumee-music
Content-Type: application/json
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pipeline` | `string` | 是 | pipeline ID，对应 `pipelines/<id>/config.json` |
| `clips` | `string[]` | 是 | 素材数组。元素可以是 `/upload` 返回的 `fileId`，也可以是合法 OSS key |
| `overrides` | `object` | 否 | 运行时覆盖参数，默认 `{}` |

最小请求示例：

```json
{
  "pipeline": "trim-mixed-dissolve-v1",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4",
    "GouMei-Video-Cut/test-input/1/clip_003.mp4"
  ],
  "overrides": {}
}
```

使用 `fileId` 的示例：

```json
{
  "pipeline": "trim-mixed-dissolve-v1",
  "clips": ["abc123def456", "def456abc123", "789abc123def"],
  "overrides": {}
}
```

成功响应：

```json
{
  "taskId": "t_ab12cd34ef56ab78"
}
```

`taskId` 规则：`t_` 前缀加 16 位十六进制字符串，总长度 18。

### 7.1 素材引用规则

`clips` 中每个元素按以下规则解析：

| 输入形态 | 服务端行为 |
|---|---|
| 不包含 `/` 或 `\` | 当作 `fileId`，从 SQLite `files` 表查询实际 `ossKey` |
| 包含 `/` | 当作 OSS key，但必须以 `OSS_PREFIX + "/"` 开头 |
| Windows 绝对路径、Linux 绝对路径、`./`、`../`、`http://` 等外部路径 | 拒绝 |

默认合法 OSS key 示例：

```text
GouMei-Video-Cut/test-input/1/clip_001.mp4
```

非法示例：

```text
D:/tmp/local.mp4
/tmp/local.mp4
../local.mp4
https://example.com/a.mp4
other-prefix/input/a.mp4
```

非法素材引用返回：

```json
{
  "error_code": 2002,
  "message": "Invalid clip reference.",
  "details": {
    "value": "D:/tmp/local.mp4"
  }
}
```

找不到 `fileId` 返回：

```json
{
  "error_code": 2006,
  "message": "File reference not found.",
  "details": {
    "fileId": "abc123def456"
  }
}
```

### 7.2 当前支持的 `overrides`

当前运行时代码实际识别这些覆盖项：

```json
{
  "preset": "auto",
  "quality": "high",
  "clip_overrides": [
    {"index": 0, "trim_start": 2, "trim_end": 0}
  ],
  "transition_overrides": [
    {"index": 0, "type": "dissolve", "duration": 0.5, "scale": 1.18}
  ],
  "default_transition": {"type": "cut", "duration": 0},
  "bgm": {"enabled": true, "dir": "input/bgm", "category": "舒缓", "filename": "1.mp3", "volume": 0.3, "fade_out": 0},
  "output": {"filename": "final.mp4"}
}
```

索引规则：

- `clip_overrides[].index` 从 `0` 开始，`0` 表示第 1 个素材。
- `transition_overrides[].index` 从 `0` 开始，`0` 表示第 1 个和第 2 个素材之间的转场。

字段说明：

| 字段 | 说明 |
|---|---|
| `preset` | 分辨率预设：`auto`, `douyin_vertical`, `douyin_horizontal`, `xiaohongshu_square`, `xiaohongshu_vertical`, `preview` |
| `quality` | 质量预设：`low`, `medium`, `high` |
| `clip_overrides` | 覆盖单个素材的 `trim_start`、`trim_end`，单位秒 |
| `transition_overrides` | 覆盖单个转场的类型、时长和缩放参数 |
| `default_transition` | 覆盖默认转场 |
| `bgm` | 覆盖 BGM 设置，常用 `{"enabled": false}` 禁用 BGM，`{"category": "舒缓"}` 按分类随机，或 `{"category": "舒缓", "filename": "1.mp3"}` 指定某一首 |
| `output` | 覆盖渲染临时输出文件名，API 最终 OSS key 使用 `outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4`，时间戳为北京时间（Asia/Shanghai） |

转场类型：

```text
cut
dissolve
flash-black
zoom-dissolve
```

重要说明：

- `pipelines/*/config.json` 中的 `variables`、`overridable` 字段目前主要是配置元数据。当前 API 运行时不会把 `transition_duration`、`trim_start`、`transition_1`、`zoom_scale` 这类顶层变量自动映射到渲染参数。
- 对接方需要使用上面列出的结构化覆盖项，例如 `clip_overrides`、`transition_overrides`、`default_transition`。
- 未识别的 `overrides` 字段会被忽略。

BGM 指定规则：

- 对接方应先调用 `GET /bgm` 获取当前实时清单；`docs/BGM_MANIFEST.json` 是打包脚本可刷新的静态清单，适合离线对齐，不替代运行时扫描结果。
- `overrides.bgm.category` 是 `/app/input/bgm` 下的相对目录名，例如 `舒缓`。
- `overrides.bgm.filename` 是分类目录下的文件名，例如 `1.mp3`。
- 传 `category + filename` 时，服务端精确选择该分类下的文件；只传 `category` 时，服务端只在该分类目录下随机选择一首。
- 精确指定歌曲时使用 `GET /bgm` 响应里的 `files[].category + files[].filename`，按分类随机时使用 `categories[].name`。
- 支持按类型放子目录；不传 `category` 时，服务端会递归扫描 `/app/input/bgm` 并随机选择一首。
- 不允许绝对路径，也不允许 `..` 路径穿越。
- 指定文件或分类目录不存在时任务失败，不会回退随机音乐。
- BGM 文件仍由容器启动同步逻辑从 `BGM_OSS_URI` 同步到 `/app/input/bgm`，`/render` 不按 OSS key 单独下载音乐。

按分类随机选择 BGM：

```json
{
  "overrides": {
    "bgm": {
      "category": "舒缓"
    }
  }
}
```

按分类和文件名指定 BGM：

```json
{
  "overrides": {
    "bgm": {
      "category": "舒缓",
      "filename": "1.mp3"
    }
  }
}
```

### 7.3 `/render` 常见错误

空 `pipeline` 或空 `clips`：

```json
{
  "error_code": 2001,
  "message": "Invalid request body.",
  "details": {}
}
```

pipeline 不存在：

```json
{
  "error_code": 2003,
  "message": "Pipeline \"not-exists\" is not registered.",
  "details": {
    "available": ["trim-mixed-dissolve-v1"]
  }
}
```

队列满：

```json
{
  "error_code": 3002,
  "message": "Queue is full.",
  "details": {
    "queueSize": 200
  }
}
```

JSON 结构类型错误返回 `422`：

```json
{
  "error_code": 1003,
  "message": "Request validation failed.",
  "details": {
    "validation": [
      {
        "type": "string_type",
        "loc": ["body", "pipeline"],
        "msg": "Input should be a valid string",
        "input": 123
      }
    ]
  }
}
```

## 8. `GET /tasks/summary`

查询整体任务状态计数。

请求示例：

```bash
curl "http://127.0.0.1:3000/tasks/summary" \
  -H "X-Api-Key: goumee-music"
```

成功响应示例：

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

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `generatedAt` | `string` | 生成时间，北京时间 |
| `workers` | `number` | worker 进程数量 |
| `queueSize` | `number` | 等待队列长度 |
| `counts` | `object` | 当前任务库中各状态任务数量 |

## 9. `GET /tasks/active`

查询当前未结束任务，只返回 `pending` 和 `rendering`，按创建时间升序排列。

请求示例：

```bash
curl "http://127.0.0.1:3000/tasks/active" \
  -H "X-Api-Key: goumee-music"
```

成功响应示例：

```json
{
  "generatedAt": "2026-05-20T18:44:36.000000",
  "tasks": [
    {
      "taskId": "t_ab12cd34ef56ab78",
      "status": "rendering",
      "progress": 45,
      "attempt": 1,
      "createdAt": "2026-05-20T18:40:00.000000",
      "startedAt": "2026-05-20T18:40:05.000000",
      "lastError": null,
      "lastErrorAt": null,
      "taskKind": "pipeline",
      "sourceName": "bgm-concat"
    }
  ]
}
```

## 10. `GET /tasks/{taskId}`

查询任务状态。建议调用方每 `3-5` 秒轮询一次，直到 `status` 为 `completed` 或 `failed`。

请求示例：

```bash
curl "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78" \
  -H "X-Api-Key: goumee-music"
```

成功响应示例：

```json
{
  "taskId": "t_ab12cd34ef56ab78",
  "status": "completed",
  "progress": 100,
  "attempt": 1,
  "createdAt": "2026-04-17T17:00:00.000000",
  "startedAt": "2026-04-17T17:00:03.000000",
  "completedAt": "2026-04-17T17:00:21.000000",
  "outputUrl": "https://...",
  "error": null,
  "lastError": null,
  "lastErrorAt": null,
  "failureHistory": [],
  "taskKind": "pipeline",
  "sourceName": "trim-mixed-dissolve-v1"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | `string` | 任务 ID |
| `status` | `string` | `pending`, `rendering`, `completed`, `failed` |
| `progress` | `number` | 当前进度，完成时为 `100` |
| `attempt` | `number` | 当前已经执行的尝试次数 |
| `createdAt` | `string` | 任务创建时间，北京时间 |
| `startedAt` | `string|null` | 最近一次开始执行时间，北京时间 |
| `completedAt` | `string|null` | 完成或最终失败时间，北京时间 |
| `outputUrl` | `string|null` | 仅任务完成且有输出时返回。真实 OSS 模式下为 1 小时预签名 URL |
| `error` | `string|null` | 最终失败原因。任务成功时为 `null` |
| `lastError` | `string|null` | 最近一次失败原因，任务最终成功后仍可能保留 |
| `lastErrorAt` | `string|null` | 最近一次失败时间，北京时间 |
| `failureHistory` | `array` | 所有已记录的失败尝试 |
| `taskKind` | `string` | 当前固定为 `pipeline` |
| `sourceName` | `string` | pipeline 名称 |

任务不存在：

```json
{
  "error_code": 2004,
  "message": "Task not found.",
  "details": {}
}
```

HTTP 状态码为 `404`。

### 8.1 状态流转

典型状态流转：

```text
POST /render
  -> pending
  -> rendering
  -> completed
```

失败重试：

```text
rendering
  -> 记录 failureHistory
  -> pending
  -> rendering
  -> completed 或 failed
```

说明：

- `TASK_MAX_ATTEMPT` 控制最大尝试次数，默认 `3`。
- 服务重启时，会把遗留的 `pending` 和 `rendering` 任务重新放回队列。遗留的 `rendering` 任务会记录一次失败历史。
- 任务完成或最终失败后，会在服务启动清理中按 `TASK_TTL_DAYS` 删除历史记录，默认保留 `7` 天。

## 11. `GET /tasks/{taskId}/download`

下载渲染结果。调用前建议先确认 `/tasks/{taskId}` 返回 `status=completed`。

请求示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: goumee-music" \
  -o final.mp4
```

真实 OSS 模式：

- 接口返回 `302` 跳转到 OSS 预签名 URL。
- 客户端需要允许 redirect。
- `curl` 使用 `-L`。
- Python `requests` 使用 `allow_redirects=True`。

本地 OSS 模式：

- 如果配置了 `OSS_LOCAL_ROOT`，接口直接返回本地文件内容。
- `/tasks/{taskId}` 中的 `outputUrl` 是本地绝对路径。

任务不存在、未完成或没有输出：

```json
{
  "error_code": 3001,
  "message": "Task output is not ready.",
  "details": {}
}
```

HTTP 状态码为 `404`。

## 12. 当前 pipeline 列表

服务启动时从 `PIPELINES_DIR` 扫描 pipeline。默认目录是仓库下的 `pipelines/`。

| Pipeline ID | 默认素材槽位 | 默认转场 | BGM | 备注 |
|---|---:|---|---|---|
| `bgm-concat` | 1 | `cut` | 是 | 纯拼接不做转场，最后混入 BGM；单个视频时用于给单视频添加 BGM |
| `flash-black-concat` | 6 | `flash-black` | 否 | 闪黑转场拼接 |
| `trim-concat` | 6 | `cut` | 否 | 裁剪后直切拼接 |
| `trim-mixed-concat` | 6 | `flash-black` | 否 | 配置内前 5 个转场交替闪黑和溶解 |
| `trim-mixed-dissolve-v1` | 5 | `flash-black` | 是 | 当前测试客户端默认推荐 pipeline |
| `trim-xfade-concat` | 6 | `dissolve` | 否 | 裁剪后溶解拼接 |
| `xfade-concat` | 6 | `dissolve` | 否 | 溶解拼接 |
| `zoom-dissolve-concat` | 6 | `zoom-dissolve` | 否 | 放大溶解拼接 |

素材数量说明：

- API 层只要求 `clips` 非空。
- 表中的素材槽位来自 pipeline 默认配置，表示默认裁剪和转场参数的槽位数量。
- 如果传入素材数量超过默认槽位，后续素材会使用默认空裁剪和默认转场。
- 如果传入素材数量少于默认槽位，只会渲染实际传入的素材。

## 13. 环境变量

常用服务配置：

服务启动时会自动读取仓库根目录的 `.env`。如果同名变量已经通过系统环境变量、Docker `environment` 或 `env_file` 注入，则优先使用已存在的环境变量。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_KEYS` | 空 | API key 白名单；当前对接文档固定使用 `goumee-music` |
| `DB_PATH` | `data/tasks.db` | SQLite 任务库 |
| `TEMP_DIR` | `temp` | 上传临时文件和 worker 临时目录 |
| `PIPELINES_DIR` | `pipelines` | pipeline 配置目录 |
| `WORKER_COUNT` | `0` | `0` 表示按 CPU 自动推导：`max(1, cpu_count // 2)` |
| `QUEUE_MAX` | `200` | 等待队列最大长度 |
| `TASK_MAX_ATTEMPT` | `3` | 最大渲染尝试次数 |
| `TASK_TTL_DAYS` | `7` | 完成和失败任务保留天数 |

OSS 配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OSS_ENDPOINT` | `oss-cn-hangzhou.aliyuncs.com` | 阿里云 OSS endpoint |
| `OSS_BUCKET` | `goumee-coze` | OSS bucket |
| `OSS_PREFIX` | `GouMei-Video-Cut` | 输入和输出 key 前缀 |
| `OSS_ACCESS_KEY_ID` | 空 | 真实 OSS 模式必填 |
| `OSS_ACCESS_KEY_SECRET` | 空 | 真实 OSS 模式必填 |
| `OSS_LOCAL_ROOT` | 空 | 设置后启用本地 OSS 模式，不访问真实 OSS |

FFmpeg 配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FFMPEG_PATH` | 空 | 指定 ffmpeg 可执行文件 |
| `FFPROBE_PATH` | 空 | 指定 ffprobe 可执行文件 |
| `FFMPEG_ENCODER` | `auto` | 编码器选择。`auto` 会尝试硬件编码后回退 |
| `FFMPEG_HWACCEL` | 空 | 可选硬件解码参数，如 `cuda` |

BGM 配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BGM_DIR` | `input/bgm` | BGM 文件目录。设置后优先级高于 pipeline 配置里的 `bgm.dir` |
| `SYNC_BGM_ON_STARTUP` | `1` | Docker entrypoint 是否启动时同步 BGM |
| `BGM_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm/` | BGM 同步源 |

## 14. 本地联调配置

不访问真实 OSS 时，建议启用本地 OSS 模式：

```powershell
$env:API_KEYS = "goumee-music"
$env:OSS_LOCAL_ROOT = "D:\VideoCut-Wrapper\temp\oss-local"
$env:DB_PATH = "D:\VideoCut-Wrapper\temp\tasks.db"
$env:TEMP_DIR = "D:\VideoCut-Wrapper\temp"
videocut serve --host 127.0.0.1 --port 3000
```

此时：

- `/upload` 会把素材复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/inputs/...`。
- worker 会从 `OSS_LOCAL_ROOT` 下载素材到 `TEMP_DIR/<taskId>/`。
- 渲染结果会写到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/outputs/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/<taskId>/final.mp4`，时间戳为北京时间（Asia/Shanghai）。
- `/tasks/{taskId}/download` 直接返回本地文件。

## 15. Python 对接示例

```python
from __future__ import annotations

import time
from pathlib import Path

import requests


BASE_URL = "http://127.0.0.1:3000"
API_KEY = "goumee-music"


def headers(json_body: bool = False) -> dict[str, str]:
    result = {"X-Api-Key": API_KEY}
    if json_body:
        result["Content-Type"] = "application/json"
    return result


payload = {
    "pipeline": "trim-mixed-dissolve-v1",
    "clips": [
        "GouMei-Video-Cut/test-input/1/clip_001.mp4",
        "GouMei-Video-Cut/test-input/1/clip_002.mp4",
        "GouMei-Video-Cut/test-input/1/clip_003.mp4",
    ],
    "overrides": {
        "bgm": {"category": "舒缓", "filename": "1.mp3"},
        "quality": "medium",
    },
}

render_resp = requests.post(
    f"{BASE_URL}/render",
    headers=headers(json_body=True),
    json=payload,
    timeout=60,
)
render_resp.raise_for_status()
task_id = render_resp.json()["taskId"]

deadline = time.time() + 30 * 60
while True:
    task_resp = requests.get(f"{BASE_URL}/tasks/{task_id}", headers=headers(), timeout=60)
    task_resp.raise_for_status()
    task = task_resp.json()

    if task["status"] == "completed":
        break
    if task["status"] == "failed":
        raise RuntimeError(task)
    if time.time() > deadline:
        raise TimeoutError(f"task timeout: {task_id}")

    time.sleep(5)

target = Path("final.mp4")
download_resp = requests.get(
    f"{BASE_URL}/tasks/{task_id}/download",
    headers=headers(),
    allow_redirects=True,
    stream=True,
    timeout=60,
)
download_resp.raise_for_status()

with target.open("wb") as handle:
    for chunk in download_resp.iter_content(1024 * 1024):
        if chunk:
            handle.write(chunk)
```

## 16. 测试客户端

仓库内置了 HTTP API 测试客户端：

```bash
python api-test/check_health.py
python api-test/render_bgm_file.py
python api-test/render_bgm_random.py
python api-test/render_groups_1_5.py
python api-test/render_groups_1_16.py
python api-test/render_trim_mixed.py
python api-test/render_zoom_dissolve.py
```

需要先设置：

```powershell
$env:API_BASE_URL = "http://127.0.0.1:3000"
$env:API_KEY = "goumee-music"
```

测试客户端默认使用真实 OSS 测试素材组。素材组定义在 `api-test/http_test_data.py` 的 `REAL_OSS_TEST_CLIP_GROUPS` 中；每个测试脚本顶部都有自己的测试说明和参数。

## 17. 对接注意事项

- 线上建议在服务前面放 HTTPS 网关或反向代理，API 服务本身只做 `X-Api-Key` 校验。
- 渲染是 CPU/GPU 和 IO 密集任务，调用方应设置较长轮询超时，例如 30 分钟。
- `outputUrl` 在真实 OSS 模式下是 1 小时预签名 URL。长期保存请转存或重新调用 `/tasks/{taskId}/download`。
- `clips` 建议传 OSS key 或 `fileId`，不要传本地路径或公网 URL。
- 如果启用了带 BGM 的 pipeline，确认 `BGM_DIR` 存在并包含音频文件；否则 worker 会失败。
- 对接方遇到 HTTP 非 2xx 时，统一读取 JSON `error_code` 判断具体错误。`3002` 可以延迟重试，`2002` 这类参数错误不建议重试。
