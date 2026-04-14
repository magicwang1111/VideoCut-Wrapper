# API 封装设计文档（修订版 v2）

**日期**: 2026-04-14（Codex 审查后修订）
**项目**: Goumei Video Cut
**状态**: 待实施

---

## 背景与目标

将 Goumei Video Cut 从本地 CLI 工具封装为云端 HTTP API，供其他后端服务调用，支持约 100 并发渲染请求。

核心解决的问题：
1. manifest 变量系统 `clip_1…clip_6` 限制视频输入数量
2. 无网络接口，无法远程调用
3. 无文件存储层，素材依赖本地路径
4. 任务不持久，进程重启丢失队列

---

## 架构决策

### 存储：阿里云 OSS
- Bucket: `oss://goumee-coze/GouMei-Video-Cut/`
- 端点通过环境变量 `OSS_ENDPOINT` 配置，默认 `oss-cn-hangzhou-internal.aliyuncs.com`
- 凭证通过环境变量注入（`OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET`），不硬编码
- 输入路径：`GouMei-Video-Cut/inputs/<fileId><ext>`（保留原始扩展名）
- 输出路径：`GouMei-Video-Cut/outputs/<taskId>/final.mp4`
- 数据库存储 OSS object key，预签名 URL 在读取时动态生成（有效期 1 小时），不持久化

### 渲染模式：异步任务
- `POST /render` 立即返回 `taskId`
- 调用方轮询 `GET /tasks/:taskId` 获取状态与结果
- 任务状态持久化到 SQLite（WAL 模式，`busy_timeout=5000`）
- 所有 SQLite 写操作集中在 API 进程（Workers 通过 IPC 消息回写状态），避免多进程直写竞争

### 并发模型：API 进程 + Worker 子进程池
- API 进程（Fastify）：仅处理 HTTP，不执行渲染，不直接写 SQLite（除任务创建）
- Worker Pool：`WORKER_COUNT` 个子进程（默认 `Math.max(1, Math.floor(cpus/2))`，可通过环境变量覆盖）
- 超出并发上限的任务进入有界内存队列（上限 200，超出返回 503）
- Worker 异常退出：API 捕获 `exit` 事件，将该任务标记 `failed`（通过 IPC），重启新 Worker

### 队列持久化与重放
- 服务启动时从 SQLite 读取所有 `status=pending` 和 `status=rendering` 的任务
- `rendering` 任务重置为 `pending`（崩溃后未完成的任务视为需要重试）
- 重放入队后正常调度
- 任务有 `attempt` 计数，超过 3 次自动标记 `failed`

### Worker 故障恢复
- Worker 启动任务时通过 IPC 发送 `lease_start`，API 将任务标记 `rendering` 并记录 `startedAt`
- Worker 完成后发送 `task_done`（含 ossKey 或 error），API 负责写 SQLite
- 若 Worker 进程异常退出（无 `task_done`），API 根据进程 PID 找到该任务，`attempt++`，超限则 `failed`，否则重新入队

---

## API 接口

### 上传素材
```
POST /upload
Content-Type: multipart/form-data
Body: file=<video|image|audio>（≤500MB）

Response 200:
{
  "fileId": "abc123",
  "ossKey": "GouMei-Video-Cut/inputs/abc123.mp4"
}
```
文件扩展名取自原始文件名，fileId 为 UUID 前 12 位。

### 发起渲染
```
POST /render
{
  "template": "trim-xfade-concat",
  "clips": ["abc123", "def456", "ghi789"],
  "params": {
    "trim_start": 2,
    "transition_duration": 0.5
  }
}
```
`params` 键名与 manifest 变量名保持一致（snake_case）。路由层将 `clips[]` + `params` 转换为 manifest 期望的 `variables` 格式（`{ clips: [...ossKeys], ...params }`）。

```
Response 202:
{ "taskId": "t_xyz" }

Response 503（队列已满）:
{ "error": "queue_full", "queueSize": 200 }
```

### 查询任务状态
```
GET /tasks/:taskId

Response 200:
{
  "taskId": "t_xyz",
  "status": "rendering",
  "progress": 45,
  "attempt": 1,
  "createdAt": "2026-04-14T10:00:00Z",
  "startedAt": "2026-04-14T10:00:05Z",
  "completedAt": null,
  "outputUrl": null,      // completed 时返回动态生成的预签名 URL（1小时有效）
  "error": null
}
```

### 下载结果
```
GET /tasks/:taskId/download
→ 302 重定向到 OSS 预签名 URL
→ 404 任务不存在或未完成
```

### 鉴权
所有接口通过请求头 `X-Api-Key` 校验，服务端从环境变量 `API_KEYS`（逗号分隔）读取白名单。

---

## 模块结构

```
src/
├── api/
│   ├── server.ts              # Fastify 入口、插件注册、路由挂载、IPC 监听
│   ├── routes/
│   │   ├── upload.ts          # POST /upload
│   │   ├── render.ts          # POST /render（参数适配 + 入队）
│   │   └── tasks.ts           # GET /tasks/:id，GET /tasks/:id/download
│   └── middleware/
│       └── auth.ts            # X-Api-Key 校验
├── queue/
│   ├── TaskQueue.ts           # 有界内存队列 + 调度逻辑 + 启动重放
│   ├── WorkerPool.ts          # child_process fork/exit/restart + IPC 桥接
│   └── worker-entry.ts        # Worker 子进程入口：IPC 接收任务 → OSS 下载 → 渲染 → OSS 上传 → IPC 回写
├── store/
│   └── TaskStore.ts           # SQLite WAL，API 进程唯一写入方，task CRUD + 重放查询
├── oss/
│   └── OssClient.ts           # OSS upload/download/presignUrl，凭证从环境变量读取
├── render/
│   └── index.ts               # RenderService：移除内部 createTask，接受外部传入 taskId
├── registry/
│   └── index.ts               # VariableType 新增 video_list
├── project/
│   ├── schema-validator.ts    # video_list 校验（数组，每项验文件扩展名）
│   └── asset-resolver.ts      # video_list 逐项路径解析
└── cli.ts                     # 保留，不改动
```

---

## 变量系统改动

新增 `video_list` 类型，值为文件路径字符串数组：

**manifest.json（新写法）**
```json
{
  "variables": {
    "clips": {
      "type": "video_list",
      "label": "视频列表",
      "required": true
    },
    "trim_start": { "type": "number", "label": "裁去开头（秒）", "default": 2, "min": 0, "max": 10 },
    "transition_duration": { "type": "number", "label": "叠化时长（秒）", "default": 0.5, "min": 0.1, "max": 3.0 }
  }
}
```

渲染时 `clips` 字段直接传 OSS 本地临时路径数组，render/index.ts 读取方式：
```typescript
// 旧：扫描 def.type === 'video' 的具名键
// 新：优先读 video_list 字段，不存在则回退旧逻辑（兼容现有 CLI 模板）
```

ffprobe 探测结果以数组形式存储：`clips_source_durations: number[]`

**迁移策略**：
- 现有 CLI 模板（`clip_1`…`clip_6`）**保留不动**，CLI 继续可用
- API 路由层将 `clips[]` fileId 列表转换为 ossKey 数组后，以 `video_list` 格式传入
- 不新增 `*-v2` 模板，API 内部统一用 `video_list` 路径

---

## 数据流

```
调用方
  │
  ├─→ POST /upload (multipart)
  │       └─→ OssClient.upload(file) → OSS inputs/
  │       └─→ 返回 { fileId, ossKey }
  │
  ├─→ POST /render { template, clips[], params }
  │       └─→ auth.ts 校验 X-Api-Key
  │       └─→ 组装 variables = { clips: [ossKey,...], ...params }
  │       └─→ TaskStore.create(task) → SQLite（status=pending, attempt=0）
  │       └─→ TaskQueue.enqueue(task) → 成功返回 202 / 队列满返回 503
  │
  ├─→ GET /tasks/:taskId（轮询）
  │       └─→ TaskStore.get(taskId) → 返回状态
  │       └─→ 若 status=completed：OssClient.presignUrl(ossKey) → outputUrl
  │
Worker 内部（IPC 通信）：
  WorkerPool → fork worker-entry.ts
  worker-entry 收到任务 via IPC →
    OssClient.download(clips[]) → temp/<taskId>/
    RenderService.render({ taskId, ... })  // 进度通过 IPC 消息发送
    OssClient.upload(outputPath) → OSS outputs/
    IPC: task_done { taskId, ossKey }  // 或 task_failed { taskId, error }

API 进程 IPC 处理：
  task_done → TaskStore.update(completed, ossKey)
  task_failed → TaskStore.update(failed, error) + attempt++
  Worker exit（非正常）→ 找到对应任务 → attempt++ → 重新入队或标记 failed
```

---

## SQLite Schema

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  template_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|rendering|completed|failed
  progress INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0,
  variables TEXT NOT NULL,                  -- JSON
  oss_key TEXT,                             -- 输出 OSS key（不存 URL）
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);
CREATE INDEX idx_tasks_status ON tasks(status);
```

WAL 模式开启：`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PORT` | HTTP 监听端口 | `3000` |
| `API_KEYS` | 合法 API Key，逗号分隔 | 必填 |
| `OSS_ENDPOINT` | OSS 端点 | `oss-cn-hangzhou-internal.aliyuncs.com` |
| `OSS_ACCESS_KEY_ID` | OSS AK | 必填 |
| `OSS_ACCESS_KEY_SECRET` | OSS SK | 必填 |
| `OSS_BUCKET` | OSS Bucket | `goumee-coze` |
| `OSS_PREFIX` | OSS 路径前缀 | `GouMei-Video-Cut` |
| `WORKER_COUNT` | Worker 子进程数 | `Math.max(1, floor(cpuCount/2))` |
| `QUEUE_MAX` | 队列最大长度 | `200` |
| `TASK_MAX_ATTEMPT` | 最大重试次数 | `3` |
| `TASK_TTL_DAYS` | 任务记录保留天数 | `7` |
| `TEMP_DIR` | 临时文件目录 | `./temp` |
| `DB_PATH` | SQLite 文件路径 | `./data/tasks.db` |

---

## 关键约束

| 项目 | 值 |
|------|-----|
| Worker 并发数 | `max(1, floor(CPU核数/2))`，可覆盖 |
| 任务队列上限 | 200（超出返回 503） |
| 上传文件大小上限 | 500MB |
| 预签名 URL 有效期 | 1 小时（读时生成，不存库） |
| 任务最大重试次数 | 3 次 |
| 任务记录保留 | 7 天，启动时清理 |
| 进度写入频率 | 最多 1 次/秒（节流） |
| 任务幂等 | `POST /render` 支持 `X-Idempotency-Key` header |

---

## 保留兼容性

- `src/cli.ts` 不改动，本地 CLI 继续可用
- 现有模板 manifest 不改动（`clip_1`…`clip_6` 保留）
- `video_list` 类型仅用于 API 内部调用路径，CLI 用户无感知
