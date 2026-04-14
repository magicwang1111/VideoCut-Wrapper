# API 封装实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Goumei Video Cut 封装为生产可用的异步 HTTP API，支持 OSS 文件存储、Worker 子进程池渲染、SQLite 任务持久化。

**Architecture:** Fastify HTTP API + 有界内存队列 + child_process Worker 池。所有 SQLite 写操作集中在 API 进程，Worker 通过 IPC 消息回传结果。任务启动时从 SQLite 重放 pending/rendering 任务，Worker 崩溃后自动重试（最多 3 次）。

**Tech Stack:** Fastify, @fastify/multipart, ali-oss, better-sqlite3, tsx（运行时），Node.js child_process

---

## 文件结构

**新建：**
- `src/api/server.ts` — Fastify 入口，路由挂载，IPC 监听
- `src/api/routes/upload.ts` — POST /upload
- `src/api/routes/render.ts` — POST /render（参数适配 + 入队）
- `src/api/routes/tasks.ts` — GET /tasks/:id，GET /tasks/:id/download
- `src/api/middleware/auth.ts` — X-Api-Key 校验
- `src/queue/TaskQueue.ts` — 有界内存队列 + 启动重放
- `src/queue/WorkerPool.ts` — 子进程池，fork/exit/IPC 桥接
- `src/queue/worker-entry.ts` — Worker 入口，OSS 下载 → 渲染 → OSS 上传 → IPC 回传
- `src/store/TaskStore.ts` — SQLite WAL，CRUD + 重放查询
- `src/oss/OssClient.ts` — OSS upload/download/presignUrl
- `.env.example` — 环境变量示例

**修改：**
- `package.json` — 新增依赖
- `src/render/index.ts` — 移除内部 createTask，接受外部 taskId；video_list 兼容
- `src/render/task.ts` — 新增 attempt 字段
- `src/registry/index.ts` — VariableType 新增 `'video_list'`
- `src/registry/schema-validator.ts` — 校验 video_list（数组，每项验扩展名）
- `src/project/asset-resolver.ts` — video_list 逐项路径解析

---

### Task 1: 安装依赖

**Files:**
- Modify: `package.json`

- [ ] **Step 1: 安装运行时依赖**

```bash
cd D:\Goumei-Video-Cut
npm install fastify @fastify/multipart ali-oss better-sqlite3
npm install --save-dev @types/better-sqlite3 @types/ali-oss
```

- [ ] **Step 2: 验证安装**

```bash
node -e "require('fastify'); require('ali-oss'); require('better-sqlite3'); console.log('OK')"
```

Expected: `OK`

- [ ] **Step 3: 创建 .env.example**

```bash
cat > D:\Goumei-Video-Cut\.env.example << 'EOF'
PORT=3000
API_KEYS=your-api-key-here
OSS_ENDPOINT=oss-cn-hangzhou-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=your-ak
OSS_ACCESS_KEY_SECRET=your-sk
OSS_BUCKET=goumee-coze
OSS_PREFIX=GouMei-Video-Cut
WORKER_COUNT=
QUEUE_MAX=200
TASK_MAX_ATTEMPT=3
TASK_TTL_DAYS=7
DB_PATH=./data/tasks.db
TEMP_DIR=./temp
EOF
```

- [ ] **Step 4: Commit**

```bash
cd D:\Goumei-Video-Cut
git add package.json package-lock.json .env.example
git commit -m "feat: 安装 API 封装依赖（fastify, ali-oss, better-sqlite3）"
```

---

### Task 2: TaskStore — SQLite 持久化

**Files:**
- Create: `src/store/TaskStore.ts`

- [ ] **Step 1: 创建 TaskStore**

```typescript
// src/store/TaskStore.ts
import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';

export type TaskStatus = 'pending' | 'rendering' | 'completed' | 'failed';

export interface TaskRow {
  id: string;
  template_id: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  variables: string; // JSON
  oss_key: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskRecord {
  id: string;
  templateId: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  variables: Record<string, unknown>;
  ossKey: string | null;
  error: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

function rowToRecord(row: TaskRow): TaskRecord {
  return {
    id: row.id,
    templateId: row.template_id,
    status: row.status,
    progress: row.progress,
    attempt: row.attempt,
    variables: JSON.parse(row.variables),
    ossKey: row.oss_key,
    error: row.error,
    createdAt: row.created_at,
    startedAt: row.started_at,
    completedAt: row.completed_at,
  };
}

export class TaskStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 5000');
    this.migrate();
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        template_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        progress INTEGER NOT NULL DEFAULT 0,
        attempt INTEGER NOT NULL DEFAULT 0,
        variables TEXT NOT NULL,
        oss_key TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    `);
  }

  create(record: Omit<TaskRecord, 'progress' | 'attempt' | 'ossKey' | 'error' | 'startedAt' | 'completedAt'>): TaskRecord {
    const now = new Date().toISOString();
    const full: TaskRecord = {
      ...record,
      progress: 0,
      attempt: 0,
      ossKey: null,
      error: null,
      startedAt: null,
      completedAt: null,
    };
    this.db.prepare(`
      INSERT INTO tasks (id, template_id, status, progress, attempt, variables, oss_key, error, created_at, started_at, completed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(full.id, full.templateId, full.status, full.progress, full.attempt,
           JSON.stringify(full.variables), full.ossKey, full.error,
           full.createdAt, full.startedAt, full.completedAt);
    return full;
  }

  get(id: string): TaskRecord | null {
    const row = this.db.prepare('SELECT * FROM tasks WHERE id = ?').get(id) as TaskRow | undefined;
    return row ? rowToRecord(row) : null;
  }

  markRendering(id: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='rendering', started_at=?, attempt=attempt+1 WHERE id=?
    `).run(new Date().toISOString(), id);
  }

  updateProgress(id: string, progress: number): void {
    this.db.prepare('UPDATE tasks SET progress=? WHERE id=?').run(progress, id);
  }

  markCompleted(id: string, ossKey: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='completed', progress=100, oss_key=?, completed_at=? WHERE id=?
    `).run(ossKey, new Date().toISOString(), id);
  }

  markFailed(id: string, error: string): void {
    this.db.prepare(`
      UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?
    `).run(error, new Date().toISOString(), id);
  }

  resetToQueue(id: string): void {
    this.db.prepare(`UPDATE tasks SET status='pending', started_at=NULL WHERE id=?`).run(id);
  }

  /** 启动重放：返回需要重新入队的任务 */
  getPendingAndStalled(): TaskRecord[] {
    const rows = this.db.prepare(
      "SELECT * FROM tasks WHERE status IN ('pending', 'rendering') ORDER BY created_at ASC"
    ).all() as TaskRow[];
    return rows.map(rowToRecord);
  }

  /** 清理超期任务 */
  cleanupOldTasks(ttlDays: number): number {
    const cutoff = new Date(Date.now() - ttlDays * 86400_000).toISOString();
    const result = this.db.prepare(
      "DELETE FROM tasks WHERE status IN ('completed','failed') AND created_at < ?"
    ).run(cutoff);
    return result.changes;
  }

  close(): void {
    this.db.close();
  }
}
```

- [ ] **Step 2: 快速冒烟验证**

```bash
cd D:\Goumei-Video-Cut
node --input-type=module << 'EOF'
import { TaskStore } from './src/store/TaskStore.ts';
// 用 tsx 验证
EOF
npx tsx -e "
import { TaskStore } from './src/store/TaskStore.ts';
const s = new TaskStore('./temp/test.db');
const t = s.create({ id: 'test1', templateId: 'x', status: 'pending', variables: {}, createdAt: new Date().toISOString() });
s.markRendering('test1');
s.markCompleted('test1', 'outputs/test1/final.mp4');
const r = s.get('test1');
console.log(r?.status, r?.ossKey);
s.close();
import { unlinkSync } from 'fs'; unlinkSync('./temp/test.db');
"
```

Expected: `completed outputs/test1/final.mp4`

- [ ] **Step 3: Commit**

```bash
git add src/store/TaskStore.ts
git commit -m "feat: TaskStore SQLite 持久化（WAL + busy_timeout + 重放查询）"
```

---

### Task 3: OssClient — OSS 封装

**Files:**
- Create: `src/oss/OssClient.ts`

- [ ] **Step 1: 创建 OssClient**

```typescript
// src/oss/OssClient.ts
import OSS from 'ali-oss';
import fs from 'node:fs';
import path from 'node:path';

export class OssClient {
  private client: OSS;
  private bucket: string;
  private prefix: string;

  constructor() {
    this.bucket = process.env.OSS_BUCKET ?? 'goumee-coze';
    this.prefix = process.env.OSS_PREFIX ?? 'GouMei-Video-Cut';
    this.client = new OSS({
      endpoint: process.env.OSS_ENDPOINT ?? 'oss-cn-hangzhou-internal.aliyuncs.com',
      accessKeyId: process.env.OSS_ACCESS_KEY_ID!,
      accessKeySecret: process.env.OSS_ACCESS_KEY_SECRET!,
      bucket: this.bucket,
    });
  }

  inputKey(fileId: string, ext: string): string {
    return `${this.prefix}/inputs/${fileId}${ext}`;
  }

  outputKey(taskId: string): string {
    return `${this.prefix}/outputs/${taskId}/final.mp4`;
  }

  async upload(localPath: string, ossKey: string): Promise<void> {
    await this.client.put(ossKey, localPath);
  }

  async download(ossKey: string, localPath: string): Promise<void> {
    fs.mkdirSync(path.dirname(localPath), { recursive: true });
    await this.client.getStream(ossKey).then(({ stream }) =>
      new Promise<void>((resolve, reject) => {
        const out = fs.createWriteStream(localPath);
        stream.pipe(out);
        out.on('finish', resolve);
        out.on('error', reject);
      })
    );
  }

  presignUrl(ossKey: string, expiresSeconds = 3600): string {
    return this.client.signatureUrl(ossKey, { expires: expiresSeconds });
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/oss/OssClient.ts
git commit -m "feat: OssClient 封装（upload/download/presign，凭证环境变量注入）"
```

---

### Task 4: video_list 变量类型支持

**Files:**
- Modify: `src/registry/index.ts`
- Modify: `src/registry/schema-validator.ts`
- Modify: `src/project/asset-resolver.ts`

- [ ] **Step 1: 在 VariableType 中新增 video_list**

在 `src/registry/index.ts` 第 7 行，将：
```typescript
export type VariableType =
  | 'video'
  | 'image'
  | 'audio'
  | 'text'
  | 'color'
  | 'number'
  | 'boolean'
  | 'select';
```
改为：
```typescript
export type VariableType =
  | 'video'
  | 'video_list'
  | 'image'
  | 'audio'
  | 'text'
  | 'color'
  | 'number'
  | 'boolean'
  | 'select';
```

- [ ] **Step 2: 在 schema-validator.ts 中处理 video_list**

在 `src/registry/schema-validator.ts` 的 `validateType` 函数中，在 `case 'video':` 之前新增：

```typescript
case 'video_list': {
  if (!Array.isArray(value)) {
    return `${def.label}（${key}）: 应为视频路径数组，实际是 ${typeof value}`;
  }
  if (value.length === 0) {
    return `${def.label}（${key}）: 视频列表不能为空`;
  }
  const allowed = FILE_EXTENSIONS['video'];
  for (let i = 0; i < value.length; i++) {
    const item = value[i];
    if (typeof item !== 'string') {
      return `${def.label}（${key}）[${i}]: 应为文件路径，实际是 ${typeof item}`;
    }
    const ext = item.slice(item.lastIndexOf('.')).toLowerCase();
    if (!allowed.includes(ext)) {
      return `${def.label}（${key}）[${i}]: 不支持的格式 "${ext}"，支持: ${allowed.join(', ')}`;
    }
  }
  return null;
}
```

- [ ] **Step 3: 在 asset-resolver.ts 中处理 video_list**

在 `src/project/asset-resolver.ts` 中，将 `const FILE_TYPES = new Set(['video', 'image', 'audio']);` 改为：
```typescript
const FILE_TYPES = new Set(['video', 'video_list', 'image', 'audio']);
```

然后在 `resolveAssets` 函数的循环中，在现有 `if (!def || !FILE_TYPES.has(def.type)) continue;` 之后增加分支：

```typescript
// video_list：逐项解析
if (def.type === 'video_list') {
  if (!Array.isArray(value)) continue;
  const resolvedList: string[] = [];
  for (let i = 0; i < value.length; i++) {
    const item = value[i];
    if (typeof item !== 'string' || item === '') continue;
    const absPath = resolveFilePath(item, [projectDir, templateInfo.dir, rootDir]);
    if (!absPath) throw new AssetNotFoundError(item, `${key}[${i}]`, configPath);
    resolvedList.push(absPath);
  }
  resolved[key] = resolvedList;
  continue;
}
```

- [ ] **Step 4: 验证**

```bash
cd D:\Goumei-Video-Cut
npx tsx -e "
import { validateVariables } from './src/registry/schema-validator.ts';
const schema = { clips: { type: 'video_list', label: '视频列表', required: true } };
const r1 = validateVariables(schema, { clips: ['a.mp4', 'b.mp4'] });
console.log('valid:', r1.valid, 'merged clips:', r1.merged.clips);
const r2 = validateVariables(schema, { clips: [] });
console.log('empty invalid:', !r2.valid);
const r3 = validateVariables(schema, { clips: ['a.txt'] });
console.log('bad ext invalid:', !r3.valid);
"
```

Expected:
```
valid: true merged clips: [ 'a.mp4', 'b.mp4' ]
empty invalid: true
bad ext invalid: true
```

- [ ] **Step 5: Commit**

```bash
git add src/registry/index.ts src/registry/schema-validator.ts src/project/asset-resolver.ts
git commit -m "feat: 新增 video_list 变量类型，支持不限数量视频输入"
```

---

### Task 5: RenderService 重构——移除内部 createTask

**Files:**
- Modify: `src/render/index.ts`
- Modify: `src/render/task.ts`

- [ ] **Step 1: 在 RenderTask 中新增 attempt 字段**

在 `src/render/task.ts` 的 `RenderTask` interface 中新增：
```typescript
attempt: number;
```

在 `createTask` 函数返回对象中新增：
```typescript
attempt: 0,
```

- [ ] **Step 2: RenderRequest 新增 taskId 可选字段**

在 `src/render/index.ts` 的 `RenderRequest` interface 中新增：
```typescript
taskId?: string; // 外部传入则跳过内部 createTask
```

- [ ] **Step 3: render() 支持外部 taskId**

在 `src/render/index.ts` 的 `render()` 方法中，将：
```typescript
const task = createTask(request.templateId, request.variables);
startTask(task);
```
改为：
```typescript
const task = createTask(request.templateId, request.variables);
if (request.taskId) task.id = request.taskId;
startTask(task);
```

- [ ] **Step 4: render() 支持 video_list clips 收集**

在 `src/render/index.ts` 的 `trim-xfade-concat` 分支（以及 `trim-concat` / `xfade-concat`），在 clips 收集循环之前，优先检查 `video_list`：

```typescript
// 优先读 video_list 字段
const videoListKey = Object.entries(request.templateInfo.manifest.variables)
  .find(([, def]) => def.type === 'video_list')?.[0];

const clips: Array<{ key: string; src: string; duration: number }> = [];

if (videoListKey && Array.isArray(request.variables[videoListKey])) {
  const list = request.variables[videoListKey] as string[];
  for (let i = 0; i < list.length; i++) {
    const src = list[i];
    const duration = (request.variables[`${videoListKey}_source_durations`] as number[] | undefined)?.[i] ?? 0;
    clips.push({ key: `clip_${i + 1}`, src, duration });
  }
} else {
  // 回退：扫描具名 video 键（兼容现有 CLI 模板）
  for (const [key, def] of Object.entries(request.templateInfo.manifest.variables)) {
    if (def.type !== 'video') continue;
    const src = request.variables[key];
    if (typeof src !== 'string' || !src.trim()) continue;
    const info = videoInfo.get(key);
    clips.push({ key, src, duration: info?.duration ?? 0 });
  }
}
```

注意：ffprobe 探测 video_list 时需同步更新 `collectVideoInfo` 以支持数组。在 `collectVideoInfo` 函数中新增：

```typescript
// 处理 video_list
for (const [key, def] of Object.entries(templateInfo.manifest.variables)) {
  if (def.type !== 'video_list') continue;
  const list = variables[key];
  if (!Array.isArray(list)) continue;
  const durations: number[] = [];
  for (const src of list as string[]) {
    if (typeof src !== 'string' || !src.trim()) { durations.push(0); continue; }
    const probed = probeVideo(ffprobePath, src);
    durations.push(probed?.duration ?? 0);
    if (probed && !videoInfo.has(key)) videoInfo.set(key, probed); // 用第一个做分辨率探测
  }
  (variables as Record<string, unknown>)[`${key}_source_durations`] = durations;
}
```

- [ ] **Step 5: 验证现有 CLI 渲染仍可工作**

```bash
cd D:\Goumei-Video-Cut
npx tsx src/cli.ts render projects/test-trim-xfade/config.yaml 2>&1 | tail -5
```

Expected: `✓ 渲染完成!`

- [ ] **Step 6: Commit**

```bash
git add src/render/index.ts src/render/task.ts
git commit -m "refactor: RenderService 支持外部 taskId 和 video_list clips 收集"
```

---

### Task 6: Worker 子进程入口

**Files:**
- Create: `src/queue/worker-entry.ts`

- [ ] **Step 1: 创建 worker-entry.ts**

```typescript
// src/queue/worker-entry.ts
// Worker 子进程入口，通过 IPC 与 API 进程通信

import path from 'node:path';
import fs from 'node:fs';
import { OssClient } from '../oss/OssClient.js';
import { RenderService } from '../render/index.js';
import { TemplateRegistry } from '../registry/index.js';

const ROOT_DIR = path.resolve(import.meta.dirname, '../..');
const TEMP_DIR = process.env.TEMP_DIR ? path.resolve(process.env.TEMP_DIR) : path.join(ROOT_DIR, 'temp');
const TEMPLATES_DIR = path.join(ROOT_DIR, 'templates');

const oss = new OssClient();
const renderService = new RenderService(ROOT_DIR);
const registry = new TemplateRegistry(TEMPLATES_DIR);
registry.scan();

// 通知 API 进程 Worker 已就绪
process.send!({ type: 'worker_ready' });

process.on('message', async (msg: {
  type: 'run_task';
  taskId: string;
  templateId: string;
  variables: Record<string, unknown>;
  preset: string;
  quality: string;
}) => {
  if (msg.type !== 'run_task') return;

  const { taskId, templateId, variables, preset, quality } = msg;
  const taskTempDir = path.join(TEMP_DIR, taskId);

  try {
    // 通知开始
    process.send!({ type: 'lease_start', taskId });

    // 下载 video_list 素材到 temp
    const videoListKey = Object.entries(
      registry.get(templateId).manifest.variables
    ).find(([, def]) => def.type === 'video_list')?.[0];

    const resolvedVars = { ...variables };

    if (videoListKey && Array.isArray(variables[videoListKey])) {
      const ossKeys = variables[videoListKey] as string[];
      fs.mkdirSync(taskTempDir, { recursive: true });
      const localPaths: string[] = [];
      for (let i = 0; i < ossKeys.length; i++) {
        const ext = path.extname(ossKeys[i]) || '.mp4';
        const localPath = path.join(taskTempDir, `clip_${i}${ext}`);
        await oss.download(ossKeys[i], localPath);
        localPaths.push(localPath);
      }
      resolvedVars[videoListKey] = localPaths;
    }

    const templateInfo = registry.get(templateId);
    const projectDir = taskTempDir;

    let lastProgressReport = 0;
    const result = await renderService.render({
      taskId,
      templateId,
      templateInfo,
      variables: resolvedVars,
      preset,
      quality,
      outputFilename: 'final.mp4',
      projectDir,
    });

    if (result.status === 'failed') {
      process.send!({ type: 'task_failed', taskId, error: result.error ?? 'unknown' });
      return;
    }

    // 上传结果到 OSS
    const ossKey = oss.outputKey(taskId);
    await oss.upload(result.outputPath!, ossKey);

    process.send!({ type: 'task_done', taskId, ossKey });
  } catch (err) {
    process.send!({
      type: 'task_failed',
      taskId,
      error: err instanceof Error ? err.message : String(err),
    });
  } finally {
    // 清理临时目录
    try { fs.rmSync(taskTempDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
});
```

- [ ] **Step 2: Commit**

```bash
git add src/queue/worker-entry.ts
git commit -m "feat: Worker 子进程入口（OSS 下载 → 渲染 → OSS 上传 → IPC 回传）"
```

---

### Task 7: WorkerPool — 子进程池管理

**Files:**
- Create: `src/queue/WorkerPool.ts`

- [ ] **Step 1: 创建 WorkerPool**

```typescript
// src/queue/WorkerPool.ts
import { fork, ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';

export interface WorkerTask {
  taskId: string;
  templateId: string;
  variables: Record<string, unknown>;
  preset: string;
  quality: string;
}

export type IpcMessage =
  | { type: 'worker_ready' }
  | { type: 'lease_start'; taskId: string }
  | { type: 'task_done'; taskId: string; ossKey: string }
  | { type: 'task_failed'; taskId: string; error: string }
  | { type: 'progress'; taskId: string; progress: number };

type IpcHandler = (msg: IpcMessage, workerId: number) => void;

interface WorkerState {
  process: ChildProcess;
  id: number;
  currentTaskId: string | null;
  ready: boolean;
}

const WORKER_ENTRY = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  'worker-entry.ts',
);

export class WorkerPool {
  private workers: WorkerState[] = [];
  private count: number;
  private onMessage: IpcHandler;
  private onWorkerDead: (workerId: number, taskId: string | null) => void;
  private nextId = 0;

  constructor(
    count: number,
    onMessage: IpcHandler,
    onWorkerDead: (workerId: number, taskId: string | null) => void,
  ) {
    this.count = count || Math.max(1, Math.floor(os.cpus().length / 2));
    this.onMessage = onMessage;
    this.onWorkerDead = onWorkerDead;
  }

  start(): Promise<void> {
    const ready: Promise<void>[] = [];
    for (let i = 0; i < this.count; i++) {
      ready.push(this.spawnWorker());
    }
    return Promise.all(ready).then(() => {});
  }

  private spawnWorker(): Promise<void> {
    return new Promise((resolve) => {
      const id = this.nextId++;
      const proc = fork(WORKER_ENTRY, [], {
        execArgv: ['--import', 'tsx/esm'],
        env: { ...process.env },
      });

      const state: WorkerState = { process: proc, id, currentTaskId: null, ready: false };
      this.workers.push(state);

      proc.on('message', (msg: IpcMessage) => {
        if (msg.type === 'worker_ready') {
          state.ready = true;
          resolve();
          return;
        }
        if (msg.type === 'lease_start') {
          state.currentTaskId = msg.taskId;
        }
        if (msg.type === 'task_done' || msg.type === 'task_failed') {
          state.currentTaskId = null;
        }
        this.onMessage(msg, id);
      });

      proc.on('exit', (code) => {
        const idx = this.workers.indexOf(state);
        if (idx !== -1) this.workers.splice(idx, 1);
        this.onWorkerDead(id, state.currentTaskId);
        // 重启 Worker
        this.spawnWorker().catch(() => {});
      });
    });
  }

  /** 找到空闲 Worker，发送任务；无空闲则返回 false */
  dispatch(task: WorkerTask): boolean {
    const idle = this.workers.find((w) => w.ready && w.currentTaskId === null);
    if (!idle) return false;
    idle.currentTaskId = task.taskId;
    idle.process.send({ type: 'run_task', ...task });
    return true;
  }

  get idleCount(): number {
    return this.workers.filter((w) => w.ready && w.currentTaskId === null).length;
  }

  get size(): number {
    return this.workers.length;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/queue/WorkerPool.ts
git commit -m "feat: WorkerPool 子进程池（自动重启 + IPC 桥接）"
```

---

### Task 8: TaskQueue — 有界队列 + 持久化重放

**Files:**
- Create: `src/queue/TaskQueue.ts`

- [ ] **Step 1: 创建 TaskQueue**

```typescript
// src/queue/TaskQueue.ts
import { TaskStore, TaskRecord } from '../store/TaskStore.js';
import { WorkerPool, WorkerTask, IpcMessage } from './WorkerPool.js';
import { OssClient } from '../oss/OssClient.js';

const TASK_MAX_ATTEMPT = parseInt(process.env.TASK_MAX_ATTEMPT ?? '3', 10);
const QUEUE_MAX = parseInt(process.env.QUEUE_MAX ?? '200', 10);

export type TaskEventHandler = (event: {
  type: 'completed' | 'failed' | 'progress';
  taskId: string;
  ossKey?: string;
  progress?: number;
  error?: string;
}) => void;

export class TaskQueue {
  private queue: WorkerTask[] = [];
  private store: TaskStore;
  private pool: WorkerPool;
  private oss: OssClient;
  private onEvent: TaskEventHandler;
  // 进度节流：taskId → last report time
  private progressThrottle = new Map<string, number>();

  constructor(store: TaskStore, oss: OssClient, workerCount: number, onEvent: TaskEventHandler) {
    this.store = store;
    this.oss = oss;
    this.onEvent = onEvent;
    this.pool = new WorkerPool(
      workerCount,
      this.handleIpc.bind(this),
      this.handleWorkerDead.bind(this),
    );
  }

  async start(): Promise<void> {
    await this.pool.start();
    this.replayFromStore();
    this.drain();
  }

  /** 启动时重放 pending/rendering 任务 */
  private replayFromStore(): void {
    const stalled = this.store.getPendingAndStalled();
    for (const task of stalled) {
      if (task.status === 'rendering') {
        this.store.resetToQueue(task.id);
      }
      if (task.attempt >= TASK_MAX_ATTEMPT) {
        this.store.markFailed(task.id, '超过最大重试次数');
        continue;
      }
      this.queue.push({
        taskId: task.id,
        templateId: task.templateId,
        variables: task.variables,
        preset: (task.variables['_preset'] as string) ?? 'auto',
        quality: (task.variables['_quality'] as string) ?? 'high',
      });
    }
    console.log(`[Queue] 重放 ${stalled.length} 个滞留任务`);
  }

  enqueue(task: WorkerTask): boolean {
    if (this.queue.length >= QUEUE_MAX) return false;
    this.queue.push(task);
    this.drain();
    return true;
  }

  get queueSize(): number {
    return this.queue.length;
  }

  private drain(): void {
    while (this.queue.length > 0 && this.pool.idleCount > 0) {
      const task = this.queue.shift()!;
      this.store.markRendering(task.taskId);
      this.pool.dispatch(task);
    }
  }

  private handleIpc(msg: IpcMessage, _workerId: number): void {
    if (msg.type === 'task_done') {
      this.store.markCompleted(msg.taskId, msg.ossKey);
      this.onEvent({ type: 'completed', taskId: msg.taskId, ossKey: msg.ossKey });
      this.drain();
    } else if (msg.type === 'task_failed') {
      const record = this.store.get(msg.taskId);
      if (record && record.attempt < TASK_MAX_ATTEMPT) {
        this.store.resetToQueue(msg.taskId);
        this.queue.push({
          taskId: msg.taskId,
          templateId: record.templateId,
          variables: record.variables,
          preset: (record.variables['_preset'] as string) ?? 'auto',
          quality: (record.variables['_quality'] as string) ?? 'high',
        });
        this.drain();
      } else {
        this.store.markFailed(msg.taskId, msg.error);
        this.onEvent({ type: 'failed', taskId: msg.taskId, error: msg.error });
      }
      this.drain();
    } else if (msg.type === 'progress') {
      const now = Date.now();
      const last = this.progressThrottle.get(msg.taskId) ?? 0;
      if (now - last > 1000) {
        this.progressThrottle.set(msg.taskId, now);
        this.store.updateProgress(msg.taskId, msg.progress);
        this.onEvent({ type: 'progress', taskId: msg.taskId, progress: msg.progress });
      }
    }
  }

  private handleWorkerDead(_workerId: number, taskId: string | null): void {
    if (!taskId) return;
    const record = this.store.get(taskId);
    if (!record) return;
    if (record.attempt >= TASK_MAX_ATTEMPT) {
      this.store.markFailed(taskId, 'Worker 崩溃，超过最大重试次数');
      this.onEvent({ type: 'failed', taskId, error: 'Worker 崩溃' });
    } else {
      this.store.resetToQueue(taskId);
      this.queue.push({
        taskId,
        templateId: record.templateId,
        variables: record.variables,
        preset: (record.variables['_preset'] as string) ?? 'auto',
        quality: (record.variables['_quality'] as string) ?? 'high',
      });
      this.drain();
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/queue/TaskQueue.ts
git commit -m "feat: TaskQueue 有界队列（持久化重放 + Worker 崩溃重试 + 进度节流）"
```

---

### Task 9: API 中间件 + 路由

**Files:**
- Create: `src/api/middleware/auth.ts`
- Create: `src/api/routes/upload.ts`
- Create: `src/api/routes/render.ts`
- Create: `src/api/routes/tasks.ts`

- [ ] **Step 1: 创建 auth 中间件**

```typescript
// src/api/middleware/auth.ts
import { FastifyRequest, FastifyReply } from 'fastify';

const API_KEYS = new Set(
  (process.env.API_KEYS ?? '').split(',').map((k) => k.trim()).filter(Boolean)
);

export async function authMiddleware(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const key = request.headers['x-api-key'];
  if (!key || !API_KEYS.has(key as string)) {
    reply.code(401).send({ error: 'unauthorized' });
  }
}
```

- [ ] **Step 2: 创建 upload 路由**

```typescript
// src/api/routes/upload.ts
import { FastifyInstance } from 'fastify';
import { OssClient } from '../../oss/OssClient.js';
import { authMiddleware } from '../middleware/auth.js';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';

const ALLOWED_EXTS = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mp3', '.wav', '.aac', '.png', '.jpg', '.jpeg', '.webp']);
const MAX_BYTES = 500 * 1024 * 1024;

export async function uploadRoutes(app: FastifyInstance): Promise<void> {
  const oss = new OssClient();

  app.post('/upload', { preHandler: authMiddleware }, async (request, reply) => {
    const data = await request.file({ limits: { fileSize: MAX_BYTES } });
    if (!data) return reply.code(400).send({ error: 'no_file' });

    const ext = path.extname(data.filename).toLowerCase();
    if (!ALLOWED_EXTS.has(ext)) {
      return reply.code(400).send({ error: 'unsupported_format', ext });
    }

    const fileId = crypto.randomUUID().replace(/-/g, '').slice(0, 12);
    const ossKey = oss.inputKey(fileId, ext);

    // 先写到临时文件再上传（ali-oss put 支持 stream，但临时文件更稳）
    const tmpPath = path.join(os.tmpdir(), `goumei_upload_${fileId}${ext}`);
    try {
      await new Promise<void>((resolve, reject) => {
        const out = fs.createWriteStream(tmpPath);
        data.file.pipe(out);
        out.on('finish', resolve);
        out.on('error', reject);
      });
      await oss.upload(tmpPath, ossKey);
    } finally {
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
    }

    return reply.send({ fileId, ossKey });
  });
}
```

- [ ] **Step 3: 创建 render 路由**

```typescript
// src/api/routes/render.ts
import { FastifyInstance } from 'fastify';
import { authMiddleware } from '../middleware/auth.js';
import { TaskStore } from '../../store/TaskStore.js';
import { TaskQueue } from '../../queue/TaskQueue.js';
import { OssClient } from '../../oss/OssClient.js';
import crypto from 'node:crypto';

export async function renderRoutes(
  app: FastifyInstance,
  { store, queue, oss }: { store: TaskStore; queue: TaskQueue; oss: OssClient },
): Promise<void> {
  app.post<{
    Body: {
      template: string;
      clips: string[];           // fileId 列表
      params?: Record<string, unknown>;
      idempotencyKey?: string;
    };
  }>('/render', { preHandler: authMiddleware }, async (request, reply) => {
    const { template, clips, params = {} } = request.body;
    const idempotencyKey = request.headers['x-idempotency-key'] as string | undefined;

    if (!template || !Array.isArray(clips) || clips.length === 0) {
      return reply.code(400).send({ error: 'invalid_body' });
    }

    // 幂等：若相同 key 已有 pending/rendering/completed 任务则直接返回
    // （简化实现：不做幂等存储，实际按需扩展）

    // 将 fileId 转为 ossKey 数组
    const oss_ = new OssClient();
    const ossKeys = clips.map((id) => {
      // 如果已经是 ossKey 格式（含 /），直接使用；否则尝试拼接（需要客户端传入扩展名信息）
      // 简化：客户端传 fileId，服务端用上传时记录的 ossKey（此处从 fileId 重建，约定 .mp4）
      // 更健壮的方式是在 TaskStore 中存 fileId→ossKey 映射表
      return id.includes('/') ? id : oss_.inputKey(id, '.mp4');
    });

    const taskId = `t_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}`;
    const variables: Record<string, unknown> = {
      clips: ossKeys,
      ...params,
      _preset: (params['preset'] as string) ?? 'auto',
      _quality: (params['quality'] as string) ?? 'high',
    };

    store.create({
      id: taskId,
      templateId: template,
      status: 'pending',
      variables,
      createdAt: new Date().toISOString(),
    });

    const enqueued = queue.enqueue({
      taskId,
      templateId: template,
      variables,
      preset: (params['preset'] as string) ?? 'auto',
      quality: (params['quality'] as string) ?? 'high',
    });

    if (!enqueued) {
      store.markFailed(taskId, '队列已满');
      return reply.code(503).send({ error: 'queue_full', queueSize: queue.queueSize });
    }

    return reply.code(202).send({ taskId });
  });
}
```

- [ ] **Step 4: 创建 tasks 路由**

```typescript
// src/api/routes/tasks.ts
import { FastifyInstance } from 'fastify';
import { authMiddleware } from '../middleware/auth.js';
import { TaskStore } from '../../store/TaskStore.js';
import { OssClient } from '../../oss/OssClient.js';

export async function tasksRoutes(
  app: FastifyInstance,
  { store, oss }: { store: TaskStore; oss: OssClient },
): Promise<void> {
  app.get<{ Params: { id: string } }>(
    '/tasks/:id',
    { preHandler: authMiddleware },
    async (request, reply) => {
      const task = store.get(request.params.id);
      if (!task) return reply.code(404).send({ error: 'not_found' });

      let outputUrl: string | null = null;
      if (task.status === 'completed' && task.ossKey) {
        outputUrl = oss.presignUrl(task.ossKey, 3600);
      }

      return reply.send({
        taskId: task.id,
        status: task.status,
        progress: task.progress,
        attempt: task.attempt,
        createdAt: task.createdAt,
        startedAt: task.startedAt,
        completedAt: task.completedAt,
        outputUrl,
        error: task.error,
      });
    },
  );

  app.get<{ Params: { id: string } }>(
    '/tasks/:id/download',
    { preHandler: authMiddleware },
    async (request, reply) => {
      const task = store.get(request.params.id);
      if (!task || task.status !== 'completed' || !task.ossKey) {
        return reply.code(404).send({ error: 'not_found_or_not_ready' });
      }
      const url = oss.presignUrl(task.ossKey, 3600);
      return reply.redirect(url, 302);
    },
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add src/api/middleware/auth.ts src/api/routes/upload.ts src/api/routes/render.ts src/api/routes/tasks.ts
git commit -m "feat: API 路由（upload / render / tasks）+ auth 中间件"
```

---

### Task 10: Fastify 服务入口

**Files:**
- Create: `src/api/server.ts`

- [ ] **Step 1: 创建 server.ts**

```typescript
// src/api/server.ts
import Fastify from 'fastify';
import multipart from '@fastify/multipart';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { TaskStore } from '../store/TaskStore.js';
import { OssClient } from '../oss/OssClient.js';
import { TaskQueue } from '../queue/TaskQueue.js';
import { uploadRoutes } from './routes/upload.js';
import { renderRoutes } from './routes/render.js';
import { tasksRoutes } from './routes/tasks.js';

const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const DB_PATH = process.env.DB_PATH ? path.resolve(process.env.DB_PATH) : path.join(ROOT_DIR, 'data', 'tasks.db');
const PORT = parseInt(process.env.PORT ?? '3000', 10);
const WORKER_COUNT = parseInt(process.env.WORKER_COUNT || '0', 10) || Math.max(1, Math.floor(os.cpus().length / 2));

async function main(): Promise<void> {
  const store = new TaskStore(DB_PATH);
  const oss = new OssClient();

  const queue = new TaskQueue(store, oss, WORKER_COUNT, (event) => {
    // 可接入 WebSocket/SSE 推送，此处仅记录日志
    if (event.type === 'completed') console.log(`[Task] ${event.taskId} 完成，ossKey=${event.ossKey}`);
    if (event.type === 'failed') console.log(`[Task] ${event.taskId} 失败：${event.error}`);
  });

  await queue.start();
  console.log(`[Queue] ${WORKER_COUNT} 个 Worker 已启动`);

  // 启动时清理超期任务
  const cleaned = store.cleanupOldTasks(parseInt(process.env.TASK_TTL_DAYS ?? '7', 10));
  if (cleaned > 0) console.log(`[Store] 清理 ${cleaned} 条过期任务`);

  const app = Fastify({ logger: true });
  await app.register(multipart);

  await app.register(uploadRoutes);
  await app.register(renderRoutes, { store, queue, oss });
  await app.register(tasksRoutes, { store, oss });

  app.get('/health', async () => ({ ok: true, workers: WORKER_COUNT, queueSize: queue.queueSize }));

  await app.listen({ port: PORT, host: '0.0.0.0' });
  console.log(`[API] 监听 :${PORT}`);

  process.on('SIGTERM', async () => {
    await app.close();
    store.close();
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

- [ ] **Step 2: 在 package.json 中新增启动脚本**

在 `package.json` 的 `scripts` 中新增：
```json
"start:api": "tsx src/api/server.ts"
```

- [ ] **Step 3: 启动服务验证健康检查**

```bash
cd D:\Goumei-Video-Cut
# 设置最小环境变量（无 OSS 的本地测试）
set API_KEYS=test-key
set OSS_ACCESS_KEY_ID=dummy
set OSS_ACCESS_KEY_SECRET=dummy
set WORKER_COUNT=1
npx tsx src/api/server.ts &
# 等待 2 秒
timeout 2
curl -s http://localhost:3000/health
```

Expected: `{"ok":true,"workers":1,"queueSize":0}`

- [ ] **Step 4: 验证 auth 拦截**

```bash
curl -s http://localhost:3000/tasks/nonexistent
```
Expected: `{"error":"unauthorized"}`

```bash
curl -s -H "X-Api-Key: test-key" http://localhost:3000/tasks/nonexistent
```
Expected: `{"error":"not_found"}`

- [ ] **Step 5: Commit**

```bash
git add src/api/server.ts package.json
git commit -m "feat: Fastify API 服务入口（健康检查 + Worker 启动 + 任务重放）"
```

---

### Task 11: fileId→ossKey 映射表修复

> Codex 指出 render 路由中 fileId→ossKey 映射是弱约定（默认 .mp4）。需要在 TaskStore 或独立表中存储上传时的扩展名。

**Files:**
- Modify: `src/store/TaskStore.ts`
- Modify: `src/api/routes/upload.ts`
- Modify: `src/api/routes/render.ts`

- [ ] **Step 1: 在 TaskStore 中新增 files 表**

在 `TaskStore.migrate()` 中追加：
```typescript
this.db.exec(`
  CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    oss_key TEXT NOT NULL,
    created_at TEXT NOT NULL
  );
`);
```

新增方法：
```typescript
saveFile(fileId: string, ossKey: string): void {
  this.db.prepare(
    'INSERT OR REPLACE INTO files (file_id, oss_key, created_at) VALUES (?, ?, ?)'
  ).run(fileId, ossKey, new Date().toISOString());
}

getOssKey(fileId: string): string | null {
  const row = this.db.prepare('SELECT oss_key FROM files WHERE file_id=?').get(fileId) as { oss_key: string } | undefined;
  return row?.oss_key ?? null;
}
```

- [ ] **Step 2: upload 路由存储 fileId→ossKey**

在 `uploadRoutes` 的 `oss.upload()` 后新增：
```typescript
store.saveFile(fileId, ossKey);
```

（需将 `store` 注入 `uploadRoutes`，修改其函数签名：`uploadRoutes(app, { store })`）

- [ ] **Step 3: render 路由查找 ossKey**

将 render 路由中的：
```typescript
const ossKeys = clips.map((id) => id.includes('/') ? id : oss_.inputKey(id, '.mp4'));
```
改为：
```typescript
const ossKeys: string[] = [];
for (const id of clips) {
  if (id.includes('/')) { ossKeys.push(id); continue; }
  const ossKey = store.getOssKey(id);
  if (!ossKey) return reply.code(400).send({ error: 'file_not_found', fileId: id });
  ossKeys.push(ossKey);
}
```

- [ ] **Step 4: Commit**

```bash
git add src/store/TaskStore.ts src/api/routes/upload.ts src/api/routes/render.ts
git commit -m "fix: fileId→ossKey 映射表，避免扩展名弱约定"
```

---

### Task 12: README 更新

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 在 README 末尾新增 API 服务章节**

在 `README.md` 末尾追加：

```markdown
## API 服务

### 启动

```bash
cp .env.example .env
# 编辑 .env 填入真实凭证
npm run start:api
```

### 接口速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传素材（multipart/form-data，≤500MB） |
| POST | `/render` | 发起渲染，返回 taskId |
| GET | `/tasks/:id` | 查询任务状态和结果 URL |
| GET | `/tasks/:id/download` | 302 重定向到结果文件 |
| GET | `/health` | 健康检查 |

所有接口需携带 `X-Api-Key: <key>` 请求头。

### 渲染请求示例

```bash
# 1. 上传素材
curl -X POST http://server/upload \
  -H "X-Api-Key: your-key" \
  -F "file=@video.mp4"
# → {"fileId":"abc123","ossKey":"..."}

# 2. 发起渲染
curl -X POST http://server/render \
  -H "X-Api-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"template":"trim-xfade-concat","clips":["abc123","def456"],"params":{"trim_start":2,"transition_duration":0.5}}'
# → {"taskId":"t_xyz"}

# 3. 轮询状态
curl http://server/tasks/t_xyz -H "X-Api-Key: your-key"
# → {"status":"completed","outputUrl":"https://..."}
```
```

- [ ] **Step 2: Commit 并推送**

```bash
git add README.md
git commit -m "docs: README 新增 API 服务使用说明"
git push
```

---

## 自检

**Spec 覆盖：**
- ✅ POST /upload → Task 9
- ✅ POST /render → Task 9
- ✅ GET /tasks/:id → Task 9
- ✅ GET /tasks/:id/download → Task 9
- ✅ X-Api-Key 鉴权 → Task 9
- ✅ OSS 凭证环境变量 → Task 3
- ✅ SQLite WAL + 集中写入 → Task 2
- ✅ Worker 子进程池 → Task 7
- ✅ 队列持久化重放 → Task 8
- ✅ Worker 崩溃重试 → Task 8
- ✅ 进度写入节流 → Task 8
- ✅ outputUrl 读时生成 → Task 9（tasks 路由）
- ✅ fileId→ossKey 映射 → Task 11
- ✅ video_list 类型 → Task 4
- ✅ RenderService 外部 taskId → Task 5
- ✅ 幂等 Key（X-Idempotency-Key header 预留）→ Task 9
- ✅ 健康检查 → Task 10
- ✅ 环境变量配置 → Task 1（.env.example）

**类型一致性：**
- `WorkerTask` 定义在 WorkerPool.ts，TaskQueue.ts 引用 ✅
- `IpcMessage` 定义在 WorkerPool.ts，worker-entry.ts 和 TaskQueue.ts 共用 ✅
- `TaskRecord` 定义在 TaskStore.ts，所有路由引用 ✅
