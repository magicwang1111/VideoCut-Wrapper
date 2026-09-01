# VideoCut API 简版对接协议

Base URL 示例：

```text
http://127.0.0.1:3000
```

除 `/health` 外，所有接口都需要请求头：

```http
X-Api-Key: goumee-music
```

服务端环境变量固定为：

```bash
API_KEYS=goumee-music
```

本文支持两种素材接入方式：调用方已经把视频素材放在 OSS 中时，可在 `/render` 的 `clips` 中直接传 OSS key；调用方只有本地文件时，可先调用 `/upload` 上传。用户自带音频也通过 `/upload` 上传，但渲染时放在 `overrides.bgm.fileId`，不要放进 `clips`。

## 1. 健康检查

```http
GET /health
```

请求示例：
```bash
curl "http://127.0.0.1:3000/health"
```

响应：

```json
{
  "ok": true,
  "workers": 4,
  "queueSize": 0,
  "pipelines": 14
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 服务是否正常 |
| `workers` | `number` | worker 数量 |
| `queueSize` | `number` | 等待队列长度 |
| `pipelines` | `number` | 已注册 pipeline 数量 |

## 2. 查询音乐列表

请求示例：

```bash
curl -X GET "http://127.0.0.1:3000/bgm" \
  -H "X-Api-Key: goumee-music"
```

响应：

```json
{
  "bgmRoot": "/app/input/bgm",
  "categories": [
    {"name": "calm", "displayName": "舒缓", "count": 5},
    {"name": "intense", "displayName": "激烈", "count": 5}
  ],
  "files": [
    {"category": "calm", "displayName": "舒缓", "filename": "测试1", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/calm/%E6%B5%8B%E8%AF%951.mp3"},
    {"category": "intense", "displayName": "激烈", "filename": "测试2", "ossUrl": "https://goumee-coze.oss-cn-hangzhou.aliyuncs.com/GouMei-Video-Cut/bgm/intense/%E6%B5%8B%E8%AF%952.mp3"}
  ]
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bgmRoot` | `string` | 服务端实际扫描的 BGM 根目录 |
| `categories` | `array` | 分类汇总，`name` 是英文分类目录，`displayName` 是展示名，`count` 是该分类下音频数量 |
| `files` | `array` | 音乐文件清单，`category + filename` 可直接用于 `/render`，其中 `filename` 不带扩展名；`displayName` 是展示名，`ossUrl` 是可直接下载的 OSS HTTPS 地址 |

说明：

- 调用方需要指定某首 BGM 时，先调用 `GET /bgm` 查询实时清单。
- 精确指定歌曲使用 `files[].category + files[].filename`。
- `files[].ossUrl` 是该音乐在 OSS 上可直接下载的 HTTPS 地址。
- 按分类随机使用 `categories[].name` 作为 `overrides.bgm.category`。
- 目录存在但没有音频时，`categories` 和 `files` 返回空数组。
- `docs/BGM_MANIFEST.json` 是静态清单，适合离线对齐；运行时仍以 `GET /bgm` 为准。

### 2.0.1 查询口播音乐

```bash
curl -X GET "http://127.0.0.1:3000/bgm-avatar" \
  -H "X-Api-Key: goumee-music"
```

响应结构与 `/bgm` 相同，但只返回 `oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/` 下同步的口播歌曲。提交 `subtitle-burn` 或 `bgm-concat` 时必须显式传 `source="bgm-avatar"` 和 `category`；省略 `filename` 时只在该分类内随机选择。

## 2.1 上传素材或用户音频

```http
POST /upload
Content-Type: multipart/form-data
X-Api-Key: goumee-music
```

表单字段固定为 `file`。视频/图片素材上传后返回 `kind=asset`，后续可放进 `/render.clips`；用户音频上传后返回 `kind=user_audio`，后续只能放进 `/render.overrides.bgm.fileId`。

上传视频素材：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/demo.mp4"
```

响应：

```json
{
  "fileId": "video123def45",
  "ossKey": "GouMei-Video-Cut/inputs/video123def45.mp4",
  "kind": "asset"
}
```

上传用户音频：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/Furious.mp3"
```

响应：

```json
{
  "fileId": "audio123def45",
  "ossKey": "GouMei-Video-Cut/user-audio/audio123def45.mp3",
  "kind": "user_audio",
  "expiresAt": "2026-06-03T12:00:00.000000"
}
```

用户音频不会写入 `/input/bgm`，不会出现在 `GET /bgm`，也不会参与随机 BGM。真实 OSS 模式建议给 `GouMei-Video-Cut/user-audio/` 前缀配置生命周期规则，到期自动删除；不需要新建 bucket。

## 2.2 同步模板音乐

后台模板配置里的隐藏音乐不走 `GET /bgm`，也不会展示给前端。运营先在后台上传音乐到：

```text
oss://goumee-coze/GouMei-Video-Cut/bgm-templete/
```

然后后台调用同步接口，把 OSS 上的模板音乐拉到服务本地目录：

```http
POST /admin/bgm-template/sync
Content-Type: application/json
X-Api-Key: goumee-music
```

容器默认设置 `SYNC_BGM_ON_STARTUP=1`，每次启动 API 服务前都会按普通、备份、模板、口播顺序统一增量同步。后台发布模板后调用本接口，可以不重启容器立即拉取最新音乐。

- 同一容器重启，或者 `BGM_TEMPLATE_DIR` 使用了宿主机目录/Docker volume 时，`-u` 会跳过相同文件。
- 容器被删除且 `BGM_TEMPLATE_DIR` 未持久化时，本地音乐也会被删除；新容器启动时会自动从 OSS 重新下载。
- 启动同步失败时服务不会启动，容器日志会保留 `ossutil` 错误。

全量同步：

```bash
curl -X POST "http://127.0.0.1:3000/admin/bgm-template/sync" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{}'
```

全量同步等价于：

```bash
ossutil sync "$BGM_TEMPLATE_OSS_URI" "$BGM_TEMPLATE_DIR/" -u -f ...
```

指定分类同步：

```bash
curl -X POST "http://127.0.0.1:3000/admin/bgm-template/sync" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{"category":"测试1"}'
```

指定分类同步等价于：

```bash
ossutil sync "oss://goumee-coze/GouMei-Video-Cut/bgm-templete/测试1/" "/app/input/bgm-templete/测试1/" -u -f ...
```

同步命令固定带 `-u -f`：`-u` 会跳过本地已存在且无需更新的相同文件；接口不会加删除参数，避免误删仍被模板引用的音乐。同步不需要重启镜像或容器。

同步成功响应：

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

同步后如果目录里有非音频文件，接口仍返回同步结果，并在 `validation.invalidFiles` 里反馈：

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

相关环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SYNC_BGM_ON_STARTUP` | `1` | 容器启动前是否统一同步普通、备份、模板和口播音乐 |
| `BGM_TEMPLATE_DIR` | `/app/input/bgm-templete` | 模板音乐本地目录，不会被 `GET /bgm` 展示 |
| `BGM_TEMPLATE_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm-templete/` | 模板音乐 OSS 同步源 |
| `BGM_AVATAR_DIR` | `/app/input/bgm-avatar` | 口播音乐本地目录，只由 `GET /bgm-avatar` 展示 |
| `BGM_AVATAR_OSS_URI` | `oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/` | 口播音乐 OSS 同步源 |
| `BGM_TEMPLATE_SYNC_TIMEOUT_SECONDS` | `600` | 同步接口等待 `ossutil sync` 的超时时间 |

## 3. 创建渲染任务

```http
POST /render
Content-Type: application/json
X-Api-Key: goumee-music
```

请求体：

```json
{
  "pipeline": "bgm-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4"
  ],
  "overrides": {}
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pipeline` | `string` | 是 | Pipeline ID |
| `clips` | `string[]` | 是 | 视频/图片素材列表，可传素材 OSS key 或 `kind=asset` 的上传 `fileId` |
| `overrides` | `object` | 否 | 运行时覆盖参数，不传时使用 pipeline 默认配置 |

调用方通常只需要传 `pipeline` 和 `clips`。裁剪、转场、画质、BGM 等渲染参数由服务端按选定 pipeline 的固定配置处理。

输入视频原音轨默认保留。要去除所有输入原声，在顶层 `overrides` 中传布尔字段 `preserve_original_audio=false`：

```json
{
  "pipeline": "bgm-concat",
  "clips": ["video123def45"],
  "overrides": {
    "preserve_original_audio": false,
    "bgm": {
      "category": "calm",
      "filename": "测试1"
    }
  }
}
```

不传或传 `true` 时保留原声：有 BGM 则输出“原声 + BGM”，无 BGM 则只输出原声。传 `false` 时去除原声：有 BGM 则只输出 BGM，无 BGM 则输出无音轨视频。该字段只接受 JSON 布尔值，字符串 `"false"` 无效。

如果要使用用户上传音频，先调用 `/upload` 上传音频，再在 `overrides.bgm.fileId` 中传返回的音频 `fileId`：

```json
{
  "pipeline": "bgm-concat",
  "clips": ["video123def45"],
  "overrides": {
    "bgm": {
      "fileId": "audio123def45"
    }
  }
}
```

`overrides.bgm.fileId` 只接受 `fileId` 一个字段，不能同时传 `category`、`filename`、`dir`、`volume`、`fade_out` 或其它 BGM 字段。音量使用 pipeline 默认配置。传了 `fileId` 后，服务端直接使用用户音频，不扫描 `/input/bgm`；输入视频存在音轨时会保留原声并与用户音乐叠加。字段名必须是驼峰 `fileId`，`file_id` 是非法字段。

如果要指定某一首 BGM，先调用 `GET /bgm` 查询清单，再在 `overrides.bgm` 里传返回的 `category + filename`：

```json
{
  "pipeline": "bgm-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4"
  ],
  "overrides": {
    "bgm": {
      "category": "calm",
      "filename": "测试1"
    }
  }
}
```

如果要使用后台模板配置的隐藏音乐，先调用 `POST /admin/bgm-template/sync` 同步对应分类，再在 `overrides.bgm` 里传 `source="template"`。例如测试音乐：

```text
oss://goumee-coze/GouMei-Video-Cut/bgm-templete/测试1/生活感.mp3
```

对应渲染请求：

```json
{
  "pipeline": "bgm-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4"
  ],
  "overrides": {
    "bgm": {
      "source": "template",
      "category": "测试1",
      "filename": "生活感"
    }
  }
}
```

BGM 与原声规则：

- 所有标准 Pipeline 默认逐段恢复输入音轨；有原声则按裁剪区间保留，无原声则补等长静音。该行为与是否选择 BGM 无关。
- `preserve_original_audio=false` 时跳过输入原声；有 BGM 时只写入 BGM，没有 BGM 时成片不含音频流。
- `GET /bgm` 返回实时清单；`docs/BGM_MANIFEST.json` 仅作为静态示例/历史清单参考。
- 用户上传音频不属于曲库，使用 `overrides.bgm.fileId`，不需要调用 `GET /bgm`。
- 模板音乐使用 `source="template"`，只查 `/app/input/bgm-templete`，不会出现在 `GET /bgm`，也不会回退公开曲库或 `BGM_BACKUP_DIR`。
- 口播音乐使用 `source="bgm-avatar"`，只查 `/app/input/bgm-avatar`；必须传 `category`，`filename` 可选。省略 `filename` 时只在 `/app/input/bgm-avatar/<category>` 内随机选择，不跨分类，也不会回退公开曲库或 `BGM_BACKUP_DIR`。
- 模板音乐必须同时传 `category + filename`；`filename` 不带扩展名。
- 未传 `source` 或 `source="catalog"` 时，`category` 是 `/app/input/bgm` 下的英文相对目录名，例如 `calm`。
- `category + filename` 可精确指定该分类下的文件，例如 `{"category": "calm", "filename": "测试1"}`；`filename` 是不带扩展名的歌曲 ID，真实文件仍可以是 `测试1.mp3`。
- 只传 `bgm.category` 且不传 `bgm.filename` 时，服务端只在该分类目录下随机选择一首。
- `filename` 不允许包含扩展名、`.`、`/`、`\`、绝对路径或 `..`；同一分类下不允许同名 stem 文件。
- 指定文件或分类目录不存在时任务失败，不会回退随机音乐。
- 不传 `bgm.category` 时，服务端会在 `/app/input/bgm` 下递归随机选择一首。
- BGM 文件由容器启动同步逻辑从 `BGM_OSS_URI` 同步到 `/app/input/bgm`，`/render` 不按 OSS key 单独下载音乐。

按分类随机选择 BGM：

```json
{
  "pipeline": "bgm-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4"
  ],
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
  "pipeline": "bgm-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/clip_001.mp4",
    "GouMei-Video-Cut/test-input/1/clip_002.mp4"
  ],
  "overrides": {
    "bgm": {
      "category": "calm",
      "filename": "测试1"
    }
  }
}
```

`clips` 约定：

- `clips` 中每一项可以是视频/图片素材 OSS key，也可以是 `/upload` 返回的 `kind=asset` 的 `fileId`。
- OSS key 默认必须以 `GouMei-Video-Cut/` 开头。
- 不允许把用户音频 `fileId` 或 `GouMei-Video-Cut/user-audio/...` 放进 `clips`。
- 不支持传本地路径或公网 URL。

合法示例：

```text
GouMei-Video-Cut/test-input/1/clip_001.mp4
```

响应：

```json
{
  "taskId": "t_ab12cd34ef56ab78"
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | `string` | 异步任务 ID，后续用于查询状态和下载结果 |

## 4. 查询整体任务状态

```http
GET /tasks/summary
X-Api-Key: goumee-music
```

curl 示例：

```bash
curl "http://127.0.0.1:3000/tasks/summary" \
  -H "X-Api-Key: goumee-music"
```

响应：

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

## 5. 查询未结束任务

```http
GET /tasks/active
X-Api-Key: goumee-music
```

curl 示例：

```bash
curl "http://127.0.0.1:3000/tasks/active" \
  -H "X-Api-Key: goumee-music"
```

只返回 `pending` 和 `rendering` 任务，按创建时间升序排列。

响应：

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

## 6. 查询任务

```http
GET /tasks/{taskId}
X-Api-Key: goumee-music
```

响应：

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
  "externalJobs": [],
  "taskKind": "pipeline",
  "sourceName": "bgm-concat"
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `taskId` | `string` | 任务 ID |
| `status` | `string` | `pending`, `rendering`, `completed`, `failed` |
| `progress` | `number` | 进度，完成时为 `100` |
| `attempt` | `number` | 已尝试次数 |
| `createdAt` | `string` | 创建时间，北京时间 |
| `startedAt` | `string|null` | 最近一次开始执行时间，北京时间 |
| `completedAt` | `string|null` | 完成或最终失败时间，北京时间 |
| `outputUrl` | `string|null` | 完成后返回结果地址。真实 OSS 模式下为 `OSS_PUBLIC_ENDPOINT` 拼接的公网对象 URL，路径里的 `/` 不应被编码成 `%2F` |
| `error` | `string|null` | 最终失败原因 |
| `lastError` | `string|null` | 最近一次失败原因 |
| `lastErrorAt` | `string|null` | 最近一次失败时间，北京时间 |
| `failureHistory` | `array` | 失败历史 |
| `failureCode` | `number` | 可选；AIGC 元数据最终失败时为 `3006` |
| `failureReason` | `string` | 可选；AIGC 元数据失败的稳定机器可读原因 |
| `failureDetails` | `object` | 可选；包含失败阶段、尝试次数、是否可重试及逐次错误摘要 |
| `externalJobs` | `array` | 外部云任务；字幕任务包含腾讯 MPS ID、归一化状态、错误码和时间，其他任务为空数组 |
| `taskKind` | `string` | 当前固定为 `pipeline` |
| `sourceName` | `string` | pipeline 名称 |

`externalJobs[].status` 取值为 `unknown`、`submitted`、`processing`、`succeeded` 或 `failed`。失败项的 `error` 包含 `code`、`extendedCode`、`message`；历史回填且无法确认状态的任务使用 `unknown`。

轮询约定：

- 建议每 `3-5` 秒查询一次。
- `completed` 表示成功，可以下载。
- `failed` 表示最终失败，读取 `error`。

## 7. 下载结果

```http
GET /tasks/{taskId}/download
X-Api-Key: goumee-music
```

成功时返回视频文件。

真实 OSS 模式下接口可能返回 `302` 跳转，客户端需要允许 redirect。

真实 OSS 模式下跳转地址必须是 `OSS_PUBLIC_ENDPOINT` 拼接的公网对象 URL。如果服务端检测到跳转地址仍是 `*-internal.aliyuncs.com` 或路径里包含 `%2F`，会返回 `500` 和 `error_code=3004`，用于定位结果 URL 配置问题。

curl 示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: goumee-music" \
  -o final.mp4
```

## 8. 错误码

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

常见错误：

| HTTP | `error_code` | 场景 | 处理建议 |
|---:|---:|---|---|
| `401` | `1001` | 缺少或错误的 `X-Api-Key` | 检查 API key |
| `415` | `1002` | `Content-Type` 不符合要求 | `/render` 使用 `application/json` |
| `422` | `1003` | JSON 字段类型校验失败 | 按字段类型修正 |
| `404` | `1004` | 请求路径不存在 | 检查 URL path |
| `400` | `2001` | `/render` 缺少 `pipeline` 或 `clips` | 修正请求体 |
| `400` | `2002` | `clips` 中存在本地路径、URL、非法 OSS key，或把用户音频当素材传入 | 改传合法素材 OSS key 或素材 `fileId` |
| `400` | `2003` | pipeline 不存在 | 使用已注册 pipeline |
| `404` | `2004` | 查询的 `taskId` 不存在 | 检查 `taskId` |
| `400/404` | `2006` | `clips` 引用的上传 `fileId` 不存在、BGM 目录不存在，或 `subtitle-burn` 输入 OSS 对象不存在 | 重新上传、修正素材引用，或检查对应 OSS key / `BGM_DIR` |
| `400` | `2007` | `overrides.bgm` 字段结构、未知字段或混传错误 | 修正 BGM 参数 |
| `400` | `2008` | `overrides.bgm.fileId` 不存在或不是用户音频 | 重新上传音频并传音频 `fileId` |
| `400` | `2009` | `subtitle-burn` 的 `clips` 数量不是 1 | 只传一个视频 OSS key |
| `400` | `2010` | `subtitle-burn` 传入了 `bgm`、`preserve_original_audio` 以外的 runtime override | 删除字幕、画质等覆盖项，仅保留支持的覆盖字段 |
| `400` | `2011` | `subtitle-burn` 输入不在服务端配置的字幕输入前缀下 | 将视频上传到响应 `details.expectedPrefix` 指定的目录 |
| `404` | `3001` | 下载时任务不存在、未完成或无输出 | 先轮询到 `completed` |
| `503` | `3002` | 渲染队列已满 | 稍后重试 |
| `500` | `3004` | 任务结果 URL 配置异常，例如 `OSS_PUBLIC_ENDPOINT` 误配为 internal endpoint，或路径分隔符被编码成 `%2F` | 检查 `OSS_PUBLIC_ENDPOINT` 和服务端日志里的 `outputUrlHost`、`outputUrlPath`、`ossKey` |
| `409` | `3005` | 模板音乐同步正在运行 | 稍后重试 `POST /admin/bgm-template/sync` |
| `500` | `9002` | 模板音乐 OSS 同步失败 | 检查 OSS 凭证、endpoint、`ossutil` 和响应里的 `reason/detail` |
| `500` | `9001` | 未预期服务端异常 | 记录日志并联系服务方 |

错误示例：

```json
{
  "error_code": 1001,
  "message": "Unauthorized.",
  "details": {}
}
```

```json
{
  "error_code": 2002,
  "message": "Invalid clip reference.",
  "details": {
    "value": "D:/tmp/local.mp4"
  }
}
```

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

```json
{
  "error_code": 2008,
  "message": "BGM file reference not found.",
  "details": {
    "fileId": "audio123def45"
  }
}
```

```json
{
  "error_code": 2008,
  "message": "Invalid BGM file reference.",
  "details": {
    "fileId": "video123def45",
    "kind": "asset",
    "expected": "user_audio"
  }
}
```

`subtitle-burn` 参数错误示例：

```json
{
  "error_code": 2009,
  "message": "subtitle-burn requires exactly one input video.",
  "details": {}
}
```

```json
{
  "error_code": 2010,
  "message": "subtitle-burn only accepts the BGM and preserve_original_audio runtime overrides.",
  "details": {"unsupported": ["quality"]}
}
```

```json
{
  "error_code": 2011,
  "message": "subtitle-burn input must be stored under the subtitle input prefix.",
  "details": {
    "expectedPrefix": "Happyhorse/subtitle-input/"
  }
}
```

```json
{
  "error_code": 2006,
  "message": "Subtitle input object was not found.",
  "details": {
    "ossKey": "Happyhorse/subtitle-input/not-found.mp4"
  }
}
```

以上错误发生在 `POST /render` 创建任务之前，响应中没有 `taskId`。

腾讯 MPS 提交/轮询、COS 字幕下载、字幕解析、FFmpeg 压制、AIGC 元数据写入和最终 OSS 上传发生在任务创建之后。此类失败不会改变查询接口的 HTTP 状态；查询 `GET /tasks/{taskId}` 时 HTTP 状态仍为 `200`，通过 `status="failed"`、`error`、`lastError` 和 `failureHistory` 判断。AIGC 元数据最终失败还会返回 `failureCode=3006`、`failureReason` 和 `failureDetails`。

```json
{
  "taskId": "t_ab12cd34ef56ab78",
  "status": "failed",
  "progress": 20,
  "attempt": 1,
  "outputUrl": null,
  "error": "Tencent MPS subtitle task failed.",
  "lastError": "Tencent MPS subtitle task failed.",
  "lastErrorAt": "2026-07-21T18:30:00.000000",
  "failureHistory": [
    {
      "attempt": 1,
      "error": "Tencent MPS subtitle task failed.",
      "createdAt": "2026-07-21T18:30:00.000000"
    }
  ],
  "taskKind": "pipeline",
  "sourceName": "subtitle-burn"
}
```

`error` 文本用于诊断，具体内容可能来自腾讯云、COS、FFmpeg 或 OSS，不应作为稳定错误码解析。客户端遇到 `status="failed"` 后应停止轮询并记录该 `taskId`，不要因一次查询网络错误而重新调用 `/render`。

AIGC 元数据写入默认最多尝试 3 次，每次写入均有 180 秒超时，并按 2 秒、4 秒线性退避。源文件缺失、输出不是 MP4、FFmpeg/ffprobe 不可用等前置错误不做无意义重试。达到上限后任务立即进入 `failed`，未标识视频不会上传。示例：

```json
{
  "status": "failed",
  "progress": 95,
  "outputUrl": null,
  "error": "AIGC metadata labeling failed [3006/AIGC_FFPROBE_TIMEOUT] after 3 attempt(s): ...",
  "failureCode": 3006,
  "failureReason": "AIGC_FFPROBE_TIMEOUT",
  "failureDetails": {
    "code": 3006,
    "reason": "AIGC_FFPROBE_TIMEOUT",
    "phase": "verify",
    "attempt": 3,
    "maxAttempts": 3,
    "retryable": true,
    "message": "AIGC ffprobe verification timed out after 60 seconds.",
    "attempts": []
  }
}
```

常见 `failureReason`：

| 原因 | 含义 |
|---|---|
| `AIGC_TOOL_UNAVAILABLE` | FFmpeg/ffprobe 缺失或无法启动 |
| `AIGC_FFMPEG_TIMEOUT` / `AIGC_FFMPEG_FAILED` | metadata 写入超时或 FFmpeg 非零退出 |
| `AIGC_TEMP_OUTPUT_MISSING_OR_EMPTY` | 临时 MP4 不存在或为空 |
| `AIGC_FFPROBE_TIMEOUT` / `AIGC_FFPROBE_FAILED` | ffprobe 验证超时或非零退出 |
| `AIGC_FFPROBE_OUTPUT_INVALID` | ffprobe 输出不是合法 JSON |
| `AIGC_METADATA_MISSING` | 未读取到 `AIGC` 标签 |
| `AIGC_METADATA_JSON_INVALID` | `AIGC` 标签内容不是合法 JSON |
| `AIGC_METADATA_FIELDS_INVALID` | 七字段缺失、增加或顺序不符 |
| `AIGC_METADATA_FIELD_TYPE_INVALID` | 字段值不是字符串 |
| `AIGC_METADATA_VALUES_MISMATCH` | 字段值与当前任务预期不一致 |
| `AIGC_ATOMIC_REPLACE_FAILED` | 验证通过后原子替换失败 |

```json
{
  "error_code": 2003,
  "message": "Pipeline \"not-exists\" is not registered.",
  "details": {
    "available": ["trim-mixed-dissolve-v1"]
  }
}
```

```json
{
  "error_code": 2006,
  "message": "BGM directory not found.",
  "details": {
    "bgmRoot": "/app/input/bgm"
  }
}
```

```json
{
  "error_code": 3001,
  "message": "Task output is not ready.",
  "details": {}
}
```

```json
{
  "error_code": 3002,
  "message": "Queue is full.",
  "details": {
    "queueSize": 200
  }
}
```

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

模板音乐文件不存在或不是音频时，`/render` 会在创建任务前返回 `400`：

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

模板音乐同步正在运行：

```json
{
  "error_code": 3005,
  "message": "BGM template sync is already running.",
  "details": {}
}
```

模板音乐同步失败：

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

## 9. 完整调用示例

查询音乐列表：

```bash
curl -X GET "http://127.0.0.1:3000/bgm" \
  -H "X-Api-Key: goumee-music"
```

同步模板音乐分类：

```bash
curl -X POST "http://127.0.0.1:3000/admin/bgm-template/sync" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{"category":"测试1"}'
```

使用模板音乐渲染：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "bgm-concat",
    "clips": [
      "GouMei-Video-Cut/test-input/1/clip_001.mp4",
      "GouMei-Video-Cut/test-input/1/clip_002.mp4"
    ],
    "overrides": {
      "bgm": {
        "source": "template",
        "category": "测试1",
        "filename": "生活感"
      }
    }
  }'
```

直接使用 OSS key：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "bgm-concat",
    "clips": [
      "GouMei-Video-Cut/test-input/1/clip_001.mp4",
      "GouMei-Video-Cut/test-input/1/clip_002.mp4",
      "GouMei-Video-Cut/test-input/1/clip_003.mp4"
    ],
    "overrides": {
      "bgm": {
        "category": "calm",
        "filename": "测试1"
      }
    }
  }'
```

上传用户音频并渲染：

```bash
audio_json=$(curl -s -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: goumee-music" \
  -F "file=@D:/input/Furious.mp3")

curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "bgm-concat",
    "clips": ["GouMei-Video-Cut/test-input/1/clip_001.mp4"],
    "overrides": {
      "bgm": {
        "fileId": "audio123def45"
      }
    }
  }'
```

上例里 `audio123def45` 需要替换成 `/upload` 返回的用户音频 `fileId`。

Python `requests` 版本：

```python
import time
from pathlib import Path

import requests

base_url = "http://127.0.0.1:3000"
api_key = "goumee-music"


def headers(json_body=False):
    result = {"X-Api-Key": api_key}
    if json_body:
        result["Content-Type"] = "application/json"
    return result


payload = {
    "pipeline": "bgm-concat",
    "clips": [
        "GouMei-Video-Cut/test-input/1/clip_001.mp4",
        "GouMei-Video-Cut/test-input/1/clip_002.mp4",
        "GouMei-Video-Cut/test-input/1/clip_003.mp4",
    ],
    "overrides": {
        "bgm": {
            "category": "calm",
            "filename": "测试1",
        },
    },
}

resp = requests.post(
    f"{base_url}/render",
    headers=headers(json_body=True),
    json=payload,
    timeout=60,
)
resp.raise_for_status()
task_id = resp.json()["taskId"]

deadline = time.time() + 30 * 60
while True:
    task_resp = requests.get(f"{base_url}/tasks/{task_id}", headers=headers(), timeout=60)
    task_resp.raise_for_status()
    task = task_resp.json()

    if task["status"] == "completed":
        break
    if task["status"] == "failed":
        raise RuntimeError(task)
    if time.time() > deadline:
        raise TimeoutError(f"task timeout: {task_id}")

    time.sleep(5)

download_resp = requests.get(
    f"{base_url}/tasks/{task_id}/download",
    headers=headers(),
    allow_redirects=True,
    stream=True,
    timeout=60,
)
download_resp.raise_for_status()

target = Path("final.mp4")
with target.open("wb") as handle:
    for chunk in download_resp.iter_content(1024 * 1024):
        if chunk:
            handle.write(chunk)

print(f"saved to {target}")
```

使用 `subtitle-burn` 生成硬字幕视频：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "subtitle-burn",
    "clips": [
      "GouMei-Video-Cut/subtitle-input/clip_001.mp4"
    ],
    "overrides": {}
  }'
```

使用口播专用音乐：

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: goumee-music" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "subtitle-burn",
    "clips": ["GouMei-Video-Cut/subtitle-input/clip_001.mp4"],
    "overrides": {
      "bgm": {
        "source": "bgm-avatar",
        "category": "口播测试",
        "filename": "1"
      }
    }
  }'
```

公开曲库音乐使用 `source="catalog"`（也可省略 `source`），模板音乐使用 `source="template"`，口播音乐使用 `source="bgm-avatar"`，用户上传音乐使用 `{"bgm":{"fileId":"audio123def45"}}`。模板音乐必须同时提供 `category/filename`；口播音乐必须提供 `category`，`filename` 可选，省略时仅在该分类内随机选择。指定音乐时，FFmpeg 在压制字幕的同时以原声 `1.0`、BGM `1.0` 混音；不传音乐时仍只压字幕并保留原声。

查询任务：

```bash
curl "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78" \
  -H "X-Api-Key: goumee-music"
```

下载结果：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34ef56ab78/download" \
  -H "X-Api-Key: goumee-music" \
  -o final.mp4
```

## 10. 可用 pipeline

| Pipeline ID | 含义 |
|---|---|
| `avatar-bgm-concat` | 单个 avatar 视频先裁掉开头 1 秒，再混入 BGM |
| `avatar-bgm-concat-2s` | 单个 avatar 视频先裁掉开头 2 秒，再混入 BGM |
| `avatar-bgm-concat-3s` | 单个 avatar 视频先裁掉开头 3 秒，再混入 BGM |
| `subtitle-burn` | 单个 OSS 视频经腾讯 MPS 生成字幕，可选口播、模板或用户上传音乐，并保留原音轨 |
| `bgm-concat` | 多段素材直接拼接，不做转场，最后混入 BGM；单个视频时用于给单视频添加 BGM |
| `segment-5-6-then-3-5-concat` | 固定使用 5 个输入视频，先取每段 5-6 秒，再取每段 3-5 秒，按顺序直切拼接并混入 BGM |
| `flash-black-concat` | 多段素材直接拼接，片段之间使用闪黑转场 |
| `trim-2-5-concat` | 固定使用 5 个输入视频，取每段 2-5 秒，按顺序直切拼接并混入 BGM |
| `trim-concat` | 每段素材先裁掉开头固定时长，再直切拼接 |
| `trim-mixed-concat` | 每段素材先裁掉开头固定时长，再按固定顺序混合闪黑和溶解转场 |
| `trim-mixed-dissolve-v1` | 每段素材先裁掉开头固定时长，再按固定顺序混合闪黑和溶解转场，并混入 BGM |
| `trim-xfade-concat` | 每段素材先裁掉开头固定时长，再使用溶解转场拼接 |
| `xfade-concat` | 多段素材直接拼接，片段之间使用溶解转场 |
| `zoom-dissolve-concat` | 多段素材直接拼接，片段之间使用放大溶解转场 |

推荐默认使用：

```text
bgm-concat
```
