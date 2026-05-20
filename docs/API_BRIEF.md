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

本文默认调用方已经把素材放在 OSS 中，并在 `/render` 的 `clips` 中传 OSS key。本文不约定上传接口。

## 1. 健康检查

```http
GET /health
```

响应：

```json
{
  "ok": true,
  "workers": 4,
  "queueSize": 0,
  "pipelines": 8
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
    {"name": "激烈", "count": 5},
    {"name": "舒缓", "count": 5}
  ],
  "files": [
    {"category": "激烈", "filename": "2.mp3"},
    {"category": "舒缓", "filename": "1.mp3"}
  ]
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bgmRoot` | `string` | 服务端实际扫描的 BGM 根目录 |
| `categories` | `array` | 分类汇总，`name` 是分类目录，`count` 是该分类下音频数量 |
| `files` | `array` | 音乐文件清单，`category + filename` 可直接用于 `/render` |

说明：

- 调用方需要指定某首 BGM 时，先调用 `GET /bgm` 查询实时清单。
- 精确指定歌曲使用 `files[].category + files[].filename`。
- 按分类随机使用 `categories[].name` 作为 `overrides.bgm.category`。
- 目录存在但没有音频时，`categories` 和 `files` 返回空数组。
- `docs/BGM_MANIFEST.json` 是静态清单，适合离线对齐；运行时仍以 `GET /bgm` 为准。

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
| `clips` | `string[]` | 是 | 素材 OSS key 列表 |
| `overrides` | `object` | 否 | 运行时覆盖参数，不传时使用 pipeline 默认配置 |

调用方通常只需要传 `pipeline` 和 `clips`。裁剪、转场、画质、BGM 等渲染参数由服务端按选定 pipeline 的固定配置处理。

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
      "category": "舒缓",
      "filename": "1.mp3"
    }
  }
}
```

BGM 路径规则：

- `GET /bgm` 返回实时清单；`docs/BGM_MANIFEST.json` 仅作为静态示例/历史清单参考。
- `category` 只能是 `/app/input/bgm` 下的相对目录名，例如 `舒缓`。
- `category + filename` 可精确指定该分类下的文件，例如 `{"category": "舒缓", "filename": "1.mp3"}`。
- 只传 `bgm.category` 且不传 `bgm.filename` 时，服务端只在该分类目录下随机选择一首。
- 不允许绝对路径，也不允许 `.` 或 `..`。
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
      "category": "舒缓"
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
      "category": "舒缓",
      "filename": "1.mp3"
    }
  }
}
```

`clips` 约定：

- `clips` 中每一项都是 OSS key。
- 默认必须以 `GouMei-Video-Cut/` 开头。
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
| `outputUrl` | `string|null` | 完成后返回结果地址 |
| `error` | `string|null` | 最终失败原因 |
| `lastError` | `string|null` | 最近一次失败原因 |
| `lastErrorAt` | `string|null` | 最近一次失败时间，北京时间 |
| `failureHistory` | `array` | 失败历史 |
| `taskKind` | `string` | 当前固定为 `pipeline` |
| `sourceName` | `string` | pipeline 名称 |

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
| `400` | `2002` | `clips` 中存在本地路径、URL 或非法 OSS key | 改传合法 OSS key |
| `400` | `2003` | pipeline 不存在 | 使用已注册 pipeline |
| `404` | `2004` | 查询的 `taskId` 不存在 | 检查 `taskId` |
| `400/404` | `2006` | 上传引用的 `fileId` 不存在，或 BGM 目录不存在 | 重新上传、修正 `fileId` 或检查 `BGM_DIR` |
| `404` | `3001` | 下载时任务不存在、未完成或无输出 | 先轮询到 `completed` |
| `503` | `3002` | 渲染队列已满 | 稍后重试 |
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

## 9. 完整调用示例

查询音乐列表：

```bash
curl -X GET "http://127.0.0.1:3000/bgm" \
  -H "X-Api-Key: goumee-music"
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
        "category": "舒缓",
        "filename": "1.mp3"
      }
    }
  }'
```

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
            "category": "舒缓",
            "filename": "1.mp3",
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
| `bgm-concat` | 多段素材直接拼接，不做转场，最后混入 BGM；单个视频时用于给单视频添加 BGM |
| `flash-black-concat` | 多段素材直接拼接，片段之间使用闪黑转场 |
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
