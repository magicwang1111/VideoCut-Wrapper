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
| `POST` | `/admin/bgm-template/sync` | 是 | 同步后台模板音乐到隐藏模板音乐目录 |
| `POST` | `/upload` | 是 | 上传本地素材或临时用户音频，返回 `fileId` 和 `ossKey` |
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
| `400/404` | `2006` | `clips` 引用的上传 `fileId` 不存在，或 BGM 目录不存在 | 重新上传、修正素材 `fileId` 或检查 `BGM_DIR` |
| `400` | `2007` | `overrides.bgm` 字段结构、未知字段或混传错误 | 修正 BGM 参数 |
| `400` | `2008` | `overrides.bgm.fileId` 不存在或不是用户音频 | 重新上传音频并传音频 `fileId` |
| `404` | `3001` | 下载时任务不存在、未完成或无输出 | 先轮询到 `completed` |
| `503` | `3002` | 渲染队列已满 | 稍后重试 |
| `413` | `3003` | 上传文件过大 | 压缩或拆分文件 |
| `500` | `3004` | 任务结果 URL 配置异常，例如 `OSS_PUBLIC_ENDPOINT` 误配为 internal endpoint，或路径分隔符被编码成 `%2F` | 检查 `OSS_PUBLIC_ENDPOINT` 和服务端日志里的 `outputUrlHost`、`outputUrlPath`、`ossKey` |
| `409` | `3005` | 模板音乐同步正在运行 | 稍后重试 `POST /admin/bgm-template/sync` |
| `500` | `9001` | 未预期服务端异常 | 记录日志并联系服务方 |
| `500` | `9002` | 模板音乐 OSS 同步失败 | 检查 OSS 凭证、endpoint、`ossutil` 和响应里的 `reason/detail` |

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
  "pipelines": 12
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

查询当前运行环境下可展示的 BGM 分类和文件清单。接口每次请求都会动态扫描 `BGM_DIR`，不会返回 `BGM_BACKUP_DIR` 里的归档歌曲。返回值里的 `category + filename` 可以直接用于 `/render` 的 `overrides.bgm`，其中 `filename` 是不带扩展名的歌曲 ID。

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
    {"name": "calm", "displayName": "舒缓", "count": 5},
    {"name": "intense", "displayName": "激烈", "count": 5}
  ],
  "files": [
    {"category": "calm", "displayName": "舒缓", "filename": "1", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/1.mp3"},
    {"category": "intense", "displayName": "激烈", "filename": "2", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/intense/2.mp3"}
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bgmRoot` | `string` | 服务端实际扫描的 BGM 根目录，`BGM_DIR` 优先，否则默认 `input/bgm` |
| `categories` | `array` | 分类汇总，`name` 是英文分类目录，`displayName` 是展示名，`count` 是该分类下音频数量 |
| `files` | `array` | 音乐文件清单，每项的 `category` 和不带扩展名的 `filename` 可传给 `/render`，`displayName` 是展示名，`ossUrl` 是可直接下载的 OSS HTTPS 地址 |

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

## 5.1 `POST /admin/bgm-template/sync`

同步后台模板配置使用的隐藏音乐。模板音乐不会出现在 `GET /bgm`，也不会被前端查询到。

默认 OSS 源：

```text
oss://goumee-coze/GouMei-Video-Cut/bgm-templete/
```

默认本地目录：

```text
/app/input/bgm-templete
```

请求头：

```http
POST /admin/bgm-template/sync
Content-Type: application/json
X-Api-Key: goumee-music
```

容器默认设置 `SYNC_BGM_TEMPLATE_ON_STARTUP=1`，entrypoint 会在 API 服务启动前执行一次模板音乐全量增量同步。后台发布模板后仍应调用本接口，以便运行中的容器立即获得最新音乐。

- 同一容器重启，或者 `BGM_TEMPLATE_DIR` 使用了宿主机目录/Docker volume 时，`-u` 会跳过相同文件。
- 删除容器且没有持久化 `BGM_TEMPLATE_DIR` 时，本地音乐会随容器删除；新容器创建后会在启动阶段自动从 OSS 重新下载。
- 启动同步失败会阻止 API 服务启动，可通过容器日志查看 `ossutil` 错误。

全量同步请求：

```json
{}
```

等价命令：

```bash
ossutil sync "$BGM_TEMPLATE_OSS_URI" "$BGM_TEMPLATE_DIR/" -u -f ...
```

指定分类同步请求：

```json
{"category": "测试1"}
```

等价命令：

```bash
ossutil sync "oss://goumee-coze/GouMei-Video-Cut/bgm-templete/测试1/" "/app/input/bgm-templete/测试1/" -u -f ...
```

接口固定使用 OSS 增量同步参数 `-u -f`。`-u` 会跳过本地已存在且无需更新的相同文件；接口不会加删除参数，避免误删仍被模板引用的音乐。同步不需要重启镜像或容器。

成功响应：

```json
{
  "ok": true,
  "scope": "category",
  "category": "测试1",
  "templateBgmRoot": "/app/input/bgm-templete",
  "templateBgmOssUri": "oss://goumee-coze/GouMei-Video-Cut/bgm-templete/测试1/",
  "durationSeconds": 1.234,
  "completedAt": "2026-06-25T18:30:00.000000",
  "validation": {
    "validAudioFiles": 1,
    "invalidFiles": [],
    "allowedExtensions": [".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"]
  }
}
```

同步后发现非音频文件时，接口会在 `validation.invalidFiles` 中反馈：

```json
{
  "validation": {
    "validAudioFiles": 1,
    "invalidFiles": [
      {"path": "测试1/readme.txt", "reason": "unsupported_extension", "extension": ".txt"}
    ],
    "allowedExtensions": [".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"]
  }
}
```

常见错误：

```json
{
  "error_code": 3005,
  "message": "BGM template sync is already running.",
  "details": {}
}
```

```json
{
  "error_code": 9002,
  "message": "BGM template sync failed.",
  "details": {
    "reason": "ossutil_failed",
    "detail": "ossutil sync failed output",
    "returnCode": 1
  }
}
```

## 6. `POST /upload`

上传单个素材文件或用户临时音频。服务端会写入临时目录，再上传到真实 OSS 或本地 OSS 模式目录，并保存 `fileId -> ossKey` 映射。

请求要求：

| 项 | 要求 |
|---|---|
| `Content-Type` | `multipart/form-data` |
| 表单字段名 | `file` |
| 单文件大小上限 | `500 MB` |
| 支持扩展名 | 素材：`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.png`, `.jpg`, `.jpeg`, `.webp`；用户音频：`.mp3`, `.wav`, `.aac`, `.ogg`, `.flac`, `.m4a` |

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
  "ossKey": "GouMei-Video-Cut/inputs/abc123def456.mp4",
  "kind": "asset"
}
```

上传用户音频：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/Furious.mp3"
```

用户音频成功响应：

```json
{
  "fileId": "abc123def456",
  "ossKey": "GouMei-Video-Cut/user-audio/abc123def456.mp3",
  "kind": "user_audio",
  "expiresAt": "2026-06-03T12:00:00.000000"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `fileId` | `string` | 12 位十六进制字符串，后续 `/render` 可直接引用 |
| `ossKey` | `string` | 文件实际写入的 OSS key |
| `kind` | `string` | `asset` 表示视频/图片素材，`user_audio` 表示用户临时音频 |
| `expiresAt` | `string` | 仅用户临时音频返回，表示服务记录过期时间 |

用户临时音频不会写入 `/input/bgm`，不会出现在 `GET /bgm`，也不会参与随机 BGM。它只在 `/render` 的 `overrides.bgm.fileId` 中使用。

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

使用用户上传音频的示例：

```json
{
  "pipeline": "bgm-concat",
  "clips": ["videoFileId1", "videoFileId2"],
  "overrides": {
    "bgm": {
      "fileId": "audioFileId1"
    }
  }
}
```

`clips` 只放视频/图片素材；用户上传音频放在 `overrides.bgm.fileId`。传入 `fileId` 后，服务端使用该上传音频替代曲库音乐，最终视频不保留原声。

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
| 不包含 `/` 或 `\` | 当作素材 `fileId`，从 SQLite `files` 表查询实际 `ossKey` |
| 包含 `/` | 当作 OSS key，但必须以 `OSS_PREFIX + "/"` 开头 |
| Windows 绝对路径、Linux 绝对路径、`./`、`../`、`http://` 等外部路径 | 拒绝 |

`clips` 不能传 `kind=user_audio` 的上传音频，也不能直接传 `GouMei-Video-Cut/user-audio/...` OSS key。用户音频只能通过 `overrides.bgm.fileId` 指定。

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
  "bgm": {"enabled": true, "category": "calm", "filename": "1"},
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
| `bgm` | 覆盖 BGM 设置，常用 `{"enabled": false}` 禁用 BGM，`{"category": "calm"}` 按分类随机，`{"category": "calm", "filename": "1"}` 指定公开曲库音乐，`{"source": "template", "category": "测试1", "filename": "生活感"}` 指定后台模板音乐，或 `{"fileId": "audioFileId1"}` 使用用户上传音频 |
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
- 用户上传音频不需要调用 `GET /bgm`。客户端先用 `/upload` 上传音频，再在 `/render` 的 `overrides.bgm.fileId` 里传音频 `fileId`。
- 模板音乐不需要调用 `GET /bgm`。后台先调用 `POST /admin/bgm-template/sync`，再在 `/render` 的 `overrides.bgm` 中传 `source="template"`、`category` 和 `filename`。
- `overrides.bgm.fileId` 场景只接受 `fileId` 一个字段，不能同时传 `category`、`filename`、`dir`、`volume`、`fade_out` 或其它 BGM 字段。音量使用 pipeline 默认配置。
- 用户上传音频字段必须是驼峰 `fileId`；snake_case `file_id` 是非法字段，会返回 `error_code=2007`。
- `overrides.bgm` 只接受 `fileId`、`enabled`、`source`、`dir`、`category`、`filename`、`volume`、`fade_out`，其它字段会返回 `error_code=2007`。
- 使用 `overrides.bgm.fileId` 后，服务端直接使用用户上传音频，不扫描 `/input/bgm`，不随机选择曲库音乐。
- `overrides.bgm.category` 是 `/app/input/bgm` 下的英文相对目录名，例如 `calm`。
- `source="template"` 时，`category` 是 `/app/input/bgm-templete` 下的相对目录名，例如 `测试1`；`filename` 是不带扩展名的文件名，例如 `生活感`。模板音乐只查模板目录，不查公开曲库或归档 backup。
- `overrides.bgm.filename` 是分类目录下不带扩展名的歌曲 ID，例如 `1`；真实文件仍可以是 `1.mp3`。
- 传 `category + filename` 时，服务端精确选择该分类下的文件；只传 `category` 时，服务端只在该分类目录下随机选择一首。
- 精确指定歌曲时使用 `GET /bgm` 响应里的 `files[].category + files[].filename`，按分类随机时使用 `categories[].name`。
- 支持按类型放子目录；不传 `category` 时，服务端会递归扫描 `/app/input/bgm` 并随机选择一首。
- `filename` 不允许包含扩展名、`.`、`/`、`\`、绝对路径或 `..` 路径穿越；同一分类下不允许同时存在 `1.mp3` 和 `1.wav` 这类同名 stem 文件。
- 指定文件或分类目录不存在时任务失败，不会回退随机音乐。
- BGM 文件仍由容器启动同步逻辑从 `BGM_OSS_URI` 同步到 `/app/input/bgm`，从 `BGM_BACKUP_OSS_URI` 同步归档歌曲到 `/app/input/bgm-backup`；`/render` 精确指定歌曲时先查当前目录，找不到再查 backup，不按 OSS key 单独下载音乐。

使用用户上传音频：

```json
{
  "overrides": {
    "bgm": {
      "fileId": "audioFileId1"
    }
  }
}
```

按分类随机选择 BGM：

```json
{
  "overrides": {
    "bgm": {
      "category": "calm"
    }
  }
}
```

按分类和文件名指定 BGM：

```json
{
  "overrides": {
    "bgm": {
      "category": "calm",
      "filename": "1"
    }
  }
}
```

使用后台模板音乐：

```json
{
  "overrides": {
    "bgm": {
      "source": "template",
      "category": "测试1",
      "filename": "生活感"
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

`overrides.bgm.file_id` 字段错误：

```json
{
  "error_code": 2007,
  "message": "Invalid BGM override.",
  "details": {
    "field": "overrides.bgm.file_id",
    "expected": "overrides.bgm.fileId",
    "unknown": ["file_id"]
  }
}
```

`overrides.bgm` 出现未知字段：

```json
{
  "error_code": 2007,
  "message": "Invalid BGM override.",
  "details": {
    "field": "overrides.bgm",
    "unknown": ["tempo"]
  }
}
```

`overrides.bgm.fileId` 与其它 BGM 字段混传：

```json
{
  "error_code": 2007,
  "message": "Invalid BGM override.",
  "details": {
    "field": "overrides.bgm.fileId",
    "conflicts": ["volume"]
  }
}
```

`overrides.bgm.fileId` 不存在：

```json
{
  "error_code": 2008,
  "message": "BGM file reference not found.",
  "details": {
    "fileId": "audioFileId1"
  }
}
```

`overrides.bgm.fileId` 指向非用户音频上传：

```json
{
  "error_code": 2008,
  "message": "Invalid BGM file reference.",
  "details": {
    "fileId": "videoFileId1",
    "kind": "asset",
    "expected": "user_audio"
  }
}
```

模板音乐不存在或不是音频：

```json
{
  "error_code": 2007,
  "message": "Invalid BGM override.",
  "details": {
    "field": "overrides.bgm.filename",
    "source": "template",
    "templateBgmRoot": "/app/input/bgm-templete",
    "reason": "file_not_found",
    "category": "测试1",
    "filename": "生活感",
    "allowedExtensions": [".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"]
  }
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
| `outputUrl` | `string|null` | 仅任务完成且有输出时返回。真实 OSS 模式下为 `OSS_PUBLIC_ENDPOINT` 拼接的公网对象 URL |
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
- 用户临时音频的服务记录会在启动清理中按 `UPLOAD_TTL_DAYS` 过期，默认跟随 `TASK_TTL_DAYS` 或 `7` 天。本地 OSS 模式会同步删除过期的 `user-audio/` 文件。

## 11. `GET /tasks/{taskId}/download`

下载渲染结果。调用前建议先确认 `/tasks/{taskId}` 返回 `status=completed`。

请求示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: goumee-music" \
  -o final.mp4
```

真实 OSS 模式：

- 接口返回 `302` 跳转到 `OSS_PUBLIC_ENDPOINT` 拼接的公网对象 URL。
- 客户端需要允许 redirect。
- `curl` 使用 `-L`。
- Python `requests` 使用 `allow_redirects=True`。
- 如果服务端检测到跳转地址仍是 `*-internal.aliyuncs.com` 或路径里包含 `%2F`，会返回 `500` 和 `error_code=3004`，用于定位结果 URL 配置问题。

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

任务结果 URL 配置异常：

```json
{
  "error_code": 3004,
  "message": "Task output URL is invalid.",
  "details": {
    "taskId": "t_ab12cd34ef56ab78",
    "reason": "internal_endpoint",
    "outputUrlHost": "goumee-coze.oss-cn-hangzhou-internal.aliyuncs.com",
    "outputUrlPath": "/GouMei-Video-Cut/outputs/20260606/20260606_104858/t_ab12cd34ef56ab78/final.mp4",
    "ossKey": "GouMei-Video-Cut/outputs/20260606/20260606_104858/t_ab12cd34ef56ab78/final.mp4"
  }
}
```

HTTP 状态码为 `500`。检查 `OSS_PUBLIC_ENDPOINT`，真实 OSS 结果 URL 不应使用 internal endpoint，路径分隔符也不应显示为 `%2F`。

## 12. 当前 pipeline 列表

服务启动时从 `PIPELINES_DIR` 扫描 pipeline。默认目录是仓库下的 `pipelines/`。

| Pipeline ID | 默认素材槽位 | 默认转场 | BGM | 备注 |
|---|---:|---|---|---|
| `avatar-bgm-concat` | 1 | `cut` | 是 | 单个 avatar 视频先裁掉开头 3 秒，再混入 BGM |
| `bgm-concat` | 1 | `cut` | 是 | 纯拼接不做转场，最后混入 BGM；单个视频时用于给单视频添加 BGM |
| `flash-black-concat` | 6 | `flash-black` | 是 | 闪黑转场拼接并混入 BGM |
| `segment-5-6-then-3-5-concat` | 10 | `cut` | 是 | 固定使用 5 个输入视频，先取每段 5-6 秒，再取每段 3-5 秒，按顺序直切拼接并混入 BGM |
| `subtitle-burn` | 1 | `cut` | 否 | 腾讯 MPS 模板 122 生成字幕，本地 FFmpeg 使用 `simkai.ttf` 压制，保留原音轨 |
| `trim-2-5-concat` | 5 | `cut` | 是 | 固定使用 5 个输入视频，取每段 2-5 秒，按顺序直切拼接并混入 BGM |
| `trim-concat` | 6 | `cut` | 是 | 裁剪后直切拼接并混入 BGM |
| `trim-mixed-concat` | 6 | `flash-black` | 是 | 配置内前 5 个转场交替闪黑和溶解，并混入 BGM |
| `trim-mixed-dissolve-v1` | 5 | `flash-black` | 是 | 当前测试客户端默认推荐 pipeline |
| `trim-xfade-concat` | 6 | `dissolve` | 是 | 裁剪后溶解拼接并混入 BGM |
| `xfade-concat` | 6 | `dissolve` | 是 | 溶解拼接并混入 BGM |
| `zoom-dissolve-concat` | 6 | `zoom-dissolve` | 是 | 放大溶解拼接并混入 BGM |

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
| `SUBTITLE_OSS_INPUT_SUBDIR` | `subtitle-input` | 字幕任务允许读取的 OSS 子目录 |
| `SUBTITLE_OSS_OUTPUT_SUBDIR` | `subtitle-output` | 字幕成片 OSS 输出子目录 |
| `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | 空 | 腾讯 MPS/COS 凭证，只能通过部署环境注入 |
| `TENCENT_REGION` | `ap-guangzhou` | MPS/COS 地域 |
| `TENCENT_COS_BUCKET` | `goumee-1444407842` | MPS 中间字幕输出桶 |
| `TENCENT_SUBTITLE_DEFINITION` | `122` | 智能字幕模板 ID |
| `DB_PATH` | `data/tasks.db` | SQLite 任务库 |
| `TEMP_DIR` | `temp` | 上传临时文件和 worker 临时目录 |
| `PIPELINES_DIR` | `pipelines` | pipeline 配置目录 |
| `WORKER_COUNT` | `0` | `0` 表示按 CPU 自动推导：`max(1, cpu_count // 2)` |
| `QUEUE_MAX` | `200` | 等待队列最大长度 |
| `TASK_MAX_ATTEMPT` | `3` | 最大渲染尝试次数 |
| `TASK_TTL_DAYS` | `7` | 完成和失败任务保留天数 |
| `UPLOAD_TTL_DAYS` | `TASK_TTL_DAYS` 或 `7` | 用户临时音频上传记录保留天数 |

OSS 配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OSS_ENDPOINT` | `oss-cn-hangzhou.aliyuncs.com` | 阿里云 OSS endpoint |
| `OSS_PUBLIC_ENDPOINT` | `oss-cn-hangzhou.aliyuncs.com` | `GET /bgm` 的 `ossUrl` 和任务完成后的 `outputUrl` 使用的公网 endpoint |
| `OSS_BUCKET` | `goumee-coze` | OSS bucket |
| `OSS_PREFIX` | `GouMei-Video-Cut` | 输入和输出 key 前缀 |
| `OSS_ACCESS_KEY_ID` | 空 | 真实 OSS 模式必填 |
| `OSS_ACCESS_KEY_SECRET` | 空 | 真实 OSS 模式必填 |
| `OSS_LOCAL_ROOT` | 空 | 设置后启用本地 OSS 模式，不访问真实 OSS |

真实 OSS 模式下，用户临时音频会写入 `OSS_PREFIX/user-audio/`，例如 `GouMei-Video-Cut/user-audio/abc123def456.mp3`。建议在当前 bucket 配置生命周期规则，匹配该前缀并在 `UPLOAD_TTL_DAYS` 后自动删除对象；不需要新建 bucket。

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
| `BGM_BACKUP_DIR` | `input/bgm-backup` | 归档 BGM 文件目录。`GET /bgm` 不展示，只在精确指定歌曲找不到当前文件时兜底 |
| `SYNC_BGM_ON_STARTUP` | `1` | Docker entrypoint 是否启动时同步 BGM |
| `SYNC_BGM_TEMPLATE_ON_STARTUP` | `1` | Docker entrypoint 是否启动时全量增量同步模板音乐 |
| `BGM_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm/` | BGM 同步源 |
| `BGM_BACKUP_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm-backup/` | 归档 BGM 同步源，目录结构需和当前 BGM 一致 |
| `BGM_TEMPLATE_DIR` | `/app/input/bgm-templete` | 后台模板音乐本地目录。`GET /bgm` 不展示 |
| `BGM_TEMPLATE_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm-templete/` | 后台模板音乐 OSS 同步源 |
| `BGM_TEMPLATE_SYNC_TIMEOUT_SECONDS` | `600` | `POST /admin/bgm-template/sync` 的同步超时时间 |

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

- `/upload` 会把素材复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/inputs/...`，用户临时音频复制到 `OSS_LOCAL_ROOT/GouMei-Video-Cut/user-audio/...`。
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
        "bgm": {"category": "calm", "filename": "1"},
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
- `outputUrl` 在真实 OSS 模式下是公网对象 URL，由 `OSS_PUBLIC_ENDPOINT` 拼接生成。长期保存请转存或重新调用 `/tasks/{taskId}/download`。
- `clips` 建议传 OSS key 或 `fileId`，不要传本地路径或公网 URL。
- 如果启用了带 BGM 的 pipeline，确认 `BGM_DIR` 存在并包含音频文件；否则 worker 会失败。
- 对接方遇到 HTTP 非 2xx 时，统一读取 JSON `error_code` 判断具体错误。`3002` 可以延迟重试，`2002` 这类参数错误不建议重试。
