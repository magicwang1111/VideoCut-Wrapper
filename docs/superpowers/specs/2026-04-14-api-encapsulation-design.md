# API 封装设计文档

**日期**: 2026-04-14  
**项目**: Goumei Video Cut  
**状态**: 待实施

---

## 背景与目标

现有系统为 CLI + YAML 配置驱动的本地工具。目标是将其封装为云端 HTTP API，供其他后端服务调用，支持约 100 并发渲染请求。

核心问题：
1. manifest 变量系统用具名键（`clip_1`…`clip_6`）限制了视频输入数量
2. 无网络接口，无法被远程调用
3. 无文件存储层，素材依赖本地路径

---

## 架构决策

### 存储：阿里云 OSS
- Bucket: `oss://goumee-coze/GouMei-Video-Cut/`
- 内网端点：`oss-cn-hangzhou-internal.aliyuncs.com`（服务器与 OSS 同 region，内网传输）
- 输入文件路径：`GouMei-Video-Cut/inputs/<fileId>.mp4`
- 输出文件路径：`GouMei-Video-Cut/outputs/<taskId>/final.mp4`

### 渲染模式：异步任务
- `POST /render` 立即返回 `taskId`
- 调用方轮询 `GET /tasks/:taskId` 获取状态与结果
- 任务状态持久化到 SQLite（轻量，无额外服务依赖）

### 并发模型：API 进程 + Worker 子进程池
- API 进程（Fastify）：仅处理 HTTP，不执行渲染
- Worker Pool：N 个子进程（建议 N = CPU 核数），每个 Worker 同时处理 1 个任务
- 超出并发上限的任务进入有界内存队列等待
- Worker 异常退出只影响当前任务，API 自动重启 Worker 并补充进池

---

## API 接口

### 上传素材
```
POST /upload
Content-Type: multipart/form-data
Body: file=<video>

Response 200:
{
  "fileId": "abc123",
  "ossKey": "GouMei-Video-Cut/inputs/abc123.mp4"
}
```

### 发起渲染
```
POST /render
{
  "template": "trim-xfade-concat",
  "clips": ["abc123", "def456", "ghi789"],   // fileId 列表，不限数量
  "params": {
    "trimStart": 2,
    "transitionDuration": 0.5
  }
}

Response 202:
{
  "taskId": "t_xyz"
}
```

### 查询任务状态
```
GET /tasks/:taskId

Response 200:
{
  "taskId": "t_xyz",
  "status": "rendering",      // pending | rendering | completed | failed
  "progress": 45,
  "createdAt": "2026-04-14T10:00:00Z",
  "outputUrl": null           // completed 时返回 OSS 预签名 URL（有效期 1 小时）
}
```

### 下载结果（可选）
```
GET /tasks/:taskId/download
→ 302 重定向到 OSS 预签名 URL
```

### 鉴权
所有接口通过请求头 `X-Api-Key` 校验，服务端配置白名单。

---

## 模块结构

```
src/
├── api/
│   ├── server.ts              # Fastify 入口、插件注册、路由挂载
│   ├── routes/
│   │   ├── upload.ts          # POST /upload：接收文件 → 上传 OSS → 返回 fileId
│   │   ├── render.ts          # POST /render：校验参数 → 入队 → 返回 taskId
│   │   └── tasks.ts           # GET /tasks/:id，GET /tasks/:id/download
│   └── middleware/
│       └── auth.ts            # X-Api-Key 校验
├── queue/
│   ├── TaskQueue.ts           # 有界内存队列 + Worker 池调度逻辑
│   ├── WorkerPool.ts          # child_process fork/exit/restart 管理
│   └── worker-entry.ts        # Worker 子进程入口：接收任务 → 下载 OSS → 渲染 → 上传 OSS → 回写状态
├── store/
│   └── TaskStore.ts           # SQLite 持久化（better-sqlite3）：任务 CRUD
├── oss/
│   └── OssClient.ts           # 阿里云 OSS 封装：upload / download / presignUrl
├── render/                    # 现有渲染逻辑（最小改动）
│   └── index.ts               # RenderService.render() 接收 clips 数组
├── registry/                  # 现有模板注册（不动）
└── cli.ts                     # 现有 CLI（保留，兼容本地使用）
```

---

## 变量系统改动

新增 `video_list` 变量类型，支持数组输入，替代 `clip_1`…`clip_6` 具名键方案：

**manifest.json（新模板写法）**
```json
{
  "variables": {
    "clips": { "type": "video_list", "label": "视频列表", "required": true },
    "trimStart": { "type": "number", "label": "裁去开头（秒）", "default": 2 },
    "transitionDuration": { "type": "number", "label": "叠化时长（秒）", "default": 1 }
  }
}
```

改动范围：
- `registry/index.ts`：`VariableType` 新增 `'video_list'`
- `registry/schema-validator.ts`：校验数组类型，每项验证文件扩展名
- `project/asset-resolver.ts`：对 `video_list` 逐项解析路径
- `render/index.ts`：`clips` 收集逻辑改为读 `video_list` 字段
- 现有 FFmpeg 模板（trim-concat / xfade-concat / trim-xfade-concat）更新 manifest

---

## 数据流

```
调用方
  │
  ├─→ POST /upload (文件)
  │       └─→ OssClient.upload() → OSS inputs/
  │       └─→ 返回 fileId
  │
  ├─→ POST /render (template + clips[] + params)
  │       └─→ 参数校验
  │       └─→ TaskStore.create(task)
  │       └─→ TaskQueue.enqueue(task)
  │       └─→ 返回 taskId
  │
  ├─→ GET /tasks/:taskId（轮询）
  │       └─→ TaskStore.get(taskId) → 返回状态
  │
Worker 内部：
  TaskQueue.dequeue()
    └─→ OssClient.download(clips[]) → temp/
    └─→ RenderService.render()
    └─→ OssClient.upload(output) → OSS outputs/
    └─→ TaskStore.update(status=completed, outputUrl)
    └─→ 清理 temp 文件
```

---

## 关键约束

| 项目 | 值 |
|------|-----|
| Worker 并发数 | CPU 核数（建议 4~8） |
| 任务队列上限 | 500（超出返回 503） |
| 上传文件大小上限 | 500MB |
| 预签名 URL 有效期 | 1 小时 |
| 任务记录保留时长 | 7 天（SQLite 定期清理） |

---

## 保留兼容性

- `src/cli.ts` 不改动，本地 CLI 继续可用
- 现有 `trim-concat` / `xfade-concat` / `trim-xfade-concat` manifest 保留 `clip_1`…`clip_6` 写法，新增对应的 `*-v2` 模板使用 `video_list`，待验证后替换
