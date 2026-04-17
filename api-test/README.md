# VideoCut API 对接说明

这份文档面向工程端，描述当前 HTTP API 的最小对接协议、调用阶段、返回格式和常见错误处理。

算法内部参数不在这里暴露。

当前仓库提供的完整参考脚本：

- [http_api_test_client.py](D:/VideoCut-Wrapper/api-test/http_api_test_client.py)

当前已注册的 pipeline 清单可在这里查看：

- [pipelines/](D:/VideoCut-Wrapper/pipelines/)
- 或运行 `videocut pipelines`

## 阶段 0：鉴权与健康检查

### 鉴权

除 `/health` 外，业务接口都需要请求头：

```http
X-Api-Key: your-api-key
```

服务端会从环境变量 `API_KEYS` 中校验白名单。

### 健康检查

- 方法：`GET`
- URL：`/health`
- 是否需要鉴权：否

示例：

```bash
curl "http://127.0.0.1:3000/health"
```

成功返回：

```json
{
  "ok": true,
  "workers": 4,
  "queueSize": 0,
  "pipelines": 1
}
```

字段说明：

- `ok`：服务是否正常启动
- `workers`：worker 进程数
- `queueSize`：当前排队中的任务数
- `pipelines`：当前已注册 pipeline 数量

## 阶段 1：两种素材来源模式

### 模式 A：素材已经在 OSS 上

如果工程端已经拿到可直接使用的 OSS key，可以直接调 `/render`。

当前建议传入的 `clips` 元素格式：

```text
GouMei-Video-Cut/...
```

例如：

```text
GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4
```

这是当前推荐的真实线上调用方式。


### 上传素材 `/upload`

- 方法：`POST`
- URL：`/upload`
- 请求头：`X-Api-Key`
- `Content-Type`：`multipart/form-data`

示例：

```bash
curl -X POST "http://127.0.0.1:3000/upload" \
  -H "X-Api-Key: your-api-key" \
  -F "file=@D:/input/demo.mp4"
```

成功返回：

```json
{
  "fileId": "abc123def456",
  "ossKey": "GouMei-Video-Cut/inputs/abc123def456.mp4"
}
```

字段说明：

- `fileId`：后续 `/render` 可直接引用
- `ossKey`：素材在 OSS 上的实际 key

常见错误：

```json
{"error": "unauthorized"}
```

```json
{"error": "unsupported_content_type", "expected": "multipart/form-data", "received": "application/json"}
```

```json
{"error": "unsupported_format", "ext": ".txt"}
```

```json
{"error": "file_too_large"}
```

## 阶段 2：提交渲染任务 `/render`

### 统一要求

- 方法：`POST`
- URL：`/render`
- 请求头：
  - `X-Api-Key`
  - `Content-Type: application/json`

### 最小对接协议

工程端只需要知道以下最小请求体：

- 必填：`pipeline`（pipeline ID）+ `clips`（素材列表）
- 可选：`overrides`（运行时覆盖参数，如 `{"bgm":{"enabled":false}}`）

其它算法参数不属于工程端对接范围。

### 请求示例

```json
{
  "pipeline": "trim-mixed-concat",
  "clips": [
    "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
    "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
    "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
    "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
    "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4"
  ],
  "overrides": {}
}
```

### 成功返回

```json
{
  "taskId": "t_ab12cd34"
}
```

### 常见错误返回

鉴权失败：

```json
{"error": "unauthorized"}
```

请求体不合法：

```json
{"error": "invalid_body"}
```

找不到上传素材：

```json
{"error": "file_not_found", "fileId": "abc123def456"}
```

pipeline 素材引用不合法：

```json
{"error": "invalid_clip_reference", "value": "D:/tmp/local.mp4"}
```

队列已满：

```json
{"error": "queue_full", "queueSize": 200}
```

找不到 pipeline：

```json
{
  "code": "pipeline_not_found",
  "error": "Pipeline \"not-exists\" not found. Available: trim-mixed-dissolve-v1"
}
```

### 请求规则说明

- `clips` 中如果元素不包含 `/`，服务端会把它当作 `fileId`
- `clips` 中如果元素包含 `/`，服务端会把它当作 `ossKey`
- 对工程端来说，直接传 `GouMei-Video-Cut/...` 形式的 OSS key 最稳定

## 阶段 3：轮询任务状态 `/tasks/{id}`

- 方法：`GET`
- URL：`/tasks/{taskId}`
- 请求头：`X-Api-Key`

示例：

```bash
curl "http://127.0.0.1:3000/tasks/t_ab12cd34" \
  -H "X-Api-Key: your-api-key"
```

成功返回：

```json
{
  "taskId": "t_ab12cd34",
  "status": "completed",
  "progress": 100,
  "attempt": 1,
  "createdAt": "2026-04-17T09:00:00.000000+00:00",
  "startedAt": "2026-04-17T09:00:03.000000+00:00",
  "completedAt": "2026-04-17T09:00:21.000000+00:00",
  "outputUrl": "https://...",
  "error": null,
  "taskKind": "pipeline",
  "sourceName": "trim-mixed-concat"
}
```

字段说明：

- `taskId`：任务 ID
- `status`：`pending` / `rendering` / `completed` / `failed`
- `progress`：当前进度
- `attempt`：当前已尝试次数
- `createdAt`：任务创建时间
- `startedAt`：开始执行时间
- `completedAt`：完成时间
- `outputUrl`：仅 `completed` 时返回。真实 OSS 模式下通常是预签名下载 URL
- `error`：失败时的错误信息
- `taskKind`：`pipeline`
- `sourceName`：pipeline 名称

常见错误：

```json
{"error": "not_found"}
```

## 阶段 4：下载结果 `/tasks/{id}/download`

- 方法：`GET`
- URL：`/tasks/{taskId}/download`
- 请求头：`X-Api-Key`

示例：

```bash
curl -L "http://127.0.0.1:3000/tasks/t_ab12cd34/download" \
  -H "X-Api-Key: your-api-key" \
  -o final.mp4
```

### 云端 OSS 模式说明


在云端 OSS 模式下：

- `/tasks/{id}/download` 可能返回 `302`
- 客户端必须允许 redirect
- `curl` 需要加 `-L`
- `requests` 需要 `allow_redirects=True`

### 未就绪或不存在

```json
{"error": "not_found_or_not_ready"}
```

### 推荐下载顺序

1. 先轮询 `/tasks/{id}`，直到 `status=completed`
2. 再请求 `/tasks/{id}/download`
3. 或直接使用 `/tasks/{id}` 返回的 `outputUrl`

## 阶段 5：常见错误码与处理建议

### 401 Unauthorized

常见返回：

```json
{"error": "unauthorized"}
```

处理建议：

- 检查 `X-Api-Key`
- 确认 key 已加入服务端 `API_KEYS`

### 400 Bad Request

常见返回：

```json
{"error": "invalid_body"}
```

```json
{"error": "file_not_found", "fileId": "abc123def456"}
```

```json
{"error": "invalid_clip_reference", "value": "D:/tmp/local.mp4"}
```

处理建议：

- 核对请求 JSON 字段
- 确认 `pipeline` 和 `clips` 非空
- 确认传入的是有效 `fileId` 或有效 `GouMei-Video-Cut/...` OSS key

### 404 Not Found

常见返回：

```json
{"error": "not_found"}
```

```json
{"error": "not_found_or_not_ready"}
```

处理建议：

- 检查 `taskId` 是否正确
- 确认任务是否已完成

### 415 Unsupported Media Type

常见返回：

```json
{"error": "unsupported_content_type", "expected": "application/json", "received": "text/plain"}
```

处理建议：

- `/render` 使用 `application/json`
- `/upload` 使用 `multipart/form-data`

### 503 Service Unavailable

常见返回：

```json
{"error": "queue_full", "queueSize": 200}
```

处理建议：

- 稍后重试
- 或在业务层做重试 / 限流

## Python 对接示例

```python
import requests

base_url = "http://127.0.0.1:3000"
headers = {
    "X-Api-Key": "your-api-key",
    "Content-Type": "application/json",
}

payload = {
    "pipeline": "trim-mixed-concat",
    "clips": [
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4",
    ],
    "overrides": {}
}

render_resp = requests.post(f"{base_url}/render", headers=headers, json=payload, timeout=60)
render_resp.raise_for_status()
task_id = render_resp.json()["taskId"]

while True:
    task_resp = requests.get(f"{base_url}/tasks/{task_id}", headers={"X-Api-Key": "your-api-key"}, timeout=60)
    task_resp.raise_for_status()
    task = task_resp.json()
    if task["status"] == "completed":
        break
    if task["status"] == "failed":
        raise RuntimeError(task["error"])

download_resp = requests.get(
    f"{base_url}/tasks/{task_id}/download",
    headers={"X-Api-Key": "your-api-key"},
    allow_redirects=True,
    stream=True,
    timeout=60,
)
download_resp.raise_for_status()

with open("final.mp4", "wb") as f:
    for chunk in download_resp.iter_content(1024 * 1024):
        if chunk:
            f.write(chunk)
```

## curl 对接示例

```bash
curl -X POST "http://127.0.0.1:3000/render" \
  -H "X-Api-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": "trim-mixed-concat",
    "clips": [
      "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
      "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4504_0.mp4",
      "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4567_0.mp4",
      "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4662_0.mp4",
      "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4663_0.mp4"
    ],
    "overrides": {
      "bgm": {
        "enabled": false
      }
    }
  }'
```

## 真实测试素材

当前测试素材位于：

```text
oss://goumee-coze/GouMei-Video-Cut/test-input/
```

结构是：

- `test-input/1/`：5 个视频
- `test-input/2/`：5 个视频
- `test-input/3/`：5 个视频
- `test-input/4/`：5 个视频
- `test-input/5/`：5 个视频

默认参考脚本使用 `group 1`。

直接运行：

```bash
# 单任务联调（默认使用 trim-mixed-concat pipeline）
python api-test/http_api_test_client.py --group 1

# 指定 pipeline
python api-test/http_api_test_client.py --pipeline zoom-dissolve-concat --group 1

# 并发提交多组，更接近真实 worker 排队 / 并发场景
python api-test/http_api_test_client.py --groups 1,2,3,4,5 --skip-download
```

说明：

- `--group 1` 是单任务联调
- `--pipeline <name>` 指定要测试的 pipeline，默认 `trim-mixed-concat`
- `--groups 1,2,3,4,5` 会并发提交多个 `/render` 请求
