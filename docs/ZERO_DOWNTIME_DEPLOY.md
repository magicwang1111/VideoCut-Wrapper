# 零停机镜像更新（蓝绿发布）

现有 `docker run -p 3000:3000 --name videocut-wrapper ...` 方式无法无缝更新：旧、新容器会同时争用固定容器名和宿主机 `3000` 端口。

本方案让 `videocut-proxy` 固定占用宿主机 `3000`，`videocut-blue` 与 `videocut-green` 只在 Docker 内部网络提供服务。发布脚本会：

1. 在非当前槽位启动新镜像；
2. 等待新容器 `/health` 通过；
3. 原子更新 Nginx upstream 并 reload，新请求立即进入新版本；
4. 让旧容器完成已经接收的渲染任务，再优雅停止旧容器；
5. 新镜像启动失败或代理验证失败时不影响旧版本，并自动回切。

新容器在交接启动时会消费一次性 marker，不重放旧容器已经持有的 SQLite 任务，避免重复渲染。marker 只生效一次；以后容器异常重启仍会正常恢复未完成任务。

要使用这套脚本，新镜像必须由包含本方案代码的提交或其后续版本构建；旧的 `v5.15`、`v5.16` 镜像不认识一次性任务 marker，不能直接作为蓝绿目标镜像。

服务器必须安装 Docker Compose v2，脚本使用的是带空格的 `docker compose`，不是旧版 `docker-compose`。部署前检查：

```bash
docker compose version
```

如果提示 Compose 不存在，在 Ubuntu 上根据 Docker 的安装来源选择其中一个包：

```bash
apt-get update
apt-get install -y docker-compose-plugin
```

若当前使用 Ubuntu 自带 Docker 软件源且上面的包不存在，则安装：

```bash
apt-get install -y docker-compose-v2
```

安装完成后必须确保 `docker compose version` 能正常输出 v2 版本，再运行发布脚本。不要仅安装旧版 `docker-compose` v1。

## 当前服务器资源基线

2026-07-22 对阿里云剪辑服务器的检查结果：

```text
CPU：8 vCPU（AMD EPYC 9T34）
内存：29 GiB
Swap：0
GPU：NVIDIA L20-4Q，4 GiB 显存
```

因此本服务器使用以下单应用容器上限：

```bash
VIDEOCUT_MEMORY_LIMIT=24g
VIDEOCUT_CPU_LIMIT=8
```

`24g` 是单个 blue/green 应用容器的内存上限，不是预分配内存；剩余约 5 GiB 留给宿主机、Docker、Nginx、文件缓存和蓝绿交接期间的另一个容器。不要在这台 29 GiB、无 Swap 的服务器上继续使用 `28g` 上限。

建议 `/data/env/videocut.env` 从以下并发配置开始：

```dotenv
WORKER_COUNT=4
UPLOAD_WORKER_COUNT=10
```

如果既有压力测试已经证明 6 路渲染稳定，可以保留 `WORKER_COUNT=6`；`UPLOAD_WORKER_COUNT=30` 对当前 8 核、4 GiB 显存服务器偏激进，建议先降到 `10`。

## 首次迁移

当前旧容器直接占用了 `3000`，而且旧部署没有挂载持久目录，所以第一次迁移无法完全零停机。先暂停新的任务提交并确认旧容器任务排空：

```bash
curl http://127.0.0.1:3000/health

docker exec -i videocut-wrapper python - <<'PY'
import sqlite3

with sqlite3.connect("/srv/videocut/data/tasks.db") as conn:
    rows = list(conn.execute("""
        select status, count(*)
        from tasks
        where status in ('pending', 'rendering')
        group by status
    """))
    print(rows)
PY
```

数据库查询必须输出 `[]`。确认无任务后再停止容器（先不要删除）：

```bash
docker stop --timeout 600 videocut-wrapper
```

把旧容器里的数据库、BGM 和输出迁移到宿主机持久目录：

```bash
mkdir -p \
  /data/videocut/data \
  /data/videocut/temp \
  /data/videocut/input/bgm \
  /data/videocut/input/bgm-backup \
  /data/videocut/input/bgm-templete \
  /data/videocut/output \
  /data/videocut/oss-local

docker cp videocut-wrapper:/srv/videocut/data/. /data/videocut/data/
docker cp videocut-wrapper:/srv/videocut/temp/. /data/videocut/temp/
docker cp videocut-wrapper:/app/input/bgm/. /data/videocut/input/bgm/
docker cp videocut-wrapper:/app/input/bgm-backup/. /data/videocut/input/bgm-backup/
docker cp videocut-wrapper:/app/input/bgm-templete/. /data/videocut/input/bgm-templete/
docker cp videocut-wrapper:/app/output/. /data/videocut/output/
docker cp videocut-wrapper:/srv/videocut/oss-local/. /data/videocut/oss-local/

docker rm videocut-wrapper
```

若某个可选目录在旧镜像中不存在，对应的 `docker cp` 可以跳过。确认 `/data/env/videocut.env` 中使用的是容器内 Linux 路径，例如 `DB_PATH=/srv/videocut/data/tasks.db`、`TEMP_DIR=/srv/videocut/temp`、`BGM_DIR=/app/input/bgm`，不要填写 Windows 宿主机路径。

先导入并确认本次首次部署使用的 `v5.17` 镜像：

```bash
docker load -i /data/images/videocut-wrapper_v5.17.tar
docker image inspect videocut-wrapper:v5.17 >/dev/null
```

然后启动第一套 blue + proxy。命令最后的 `videocut-wrapper:v5.17` 是必须真实存在的完整镜像名和 tag：

```bash
cd /data/VideoCut-Wrapper
chmod +x deploy/zero_downtime_deploy.sh

VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=24g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.17
```

从下一次更新开始不再需要手动停止或删除旧容器。

## 后续每次更新

这台服务器不适合让旧、新容器各自同时运行多路渲染。选择没有新任务提交的发布窗口，先检查：

```bash
curl http://127.0.0.1:3000/health
```

确认响应中的以下两个字段都是 `0`：

```json
{
  "queueSize": 0,
  "localActiveWorkers": 0
}
```

例如下一次发布 `v5.18`，先导入并确认镜像：

```bash
docker load -i /data/images/videocut-wrapper_v5.18.tar
docker image inspect videocut-wrapper:v5.18 >/dev/null
```

然后执行 GPU 发布：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=24g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.18
```

这里的 `v5.18` 只是“下一次版本”的具体示例。以后发布 `v5.19` 时，tar 文件名、`docker load` 后的镜像 tag 和脚本最后一个参数必须同时改成 `v5.19`。不要使用字面值 `vNext`，发布脚本不会自动猜测版本号。

CPU 配额不是预留资源。blue 和 green 短暂共存时仍然共享宿主机的 8 个 vCPU；发布前等待任务排空，可以避免两套 worker 同时争抢 CPU、内存和 4 GiB GPU 显存。

### 包含数据库结构变更的发布

发布包含 `task_external_jobs` 等 SQLite 迁移的新镜像前，先通过当前活动容器执行在线备份。不要直接复制正在使用的 `tasks.db`：

```bash
ACTIVE_CONTAINER=$(docker exec videocut-proxy sh -c \
  "awk '{print \$2}' /etc/nginx/runtime/upstream.conf | cut -d: -f1 | tr -d ';'")

docker exec -i "${ACTIVE_CONTAINER}" python - <<'PY'
import sqlite3
from datetime import datetime

source = "/srv/videocut/data/tasks.db"
target = f"{source}.bak-{datetime.now():%Y%m%d-%H%M%S}"
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print(target)
PY
```

备份文件会保存在宿主机 `/data/videocut/data/`。新容器启动时自动幂等建表并回填 `variables.subtitle_state.mps_task_id`，旧容器会忽略新增表。

发布完成后检查数据库完整性和迁移结果：

```bash
ACTIVE_CONTAINER=$(docker exec videocut-proxy sh -c \
  "awk '{print \$2}' /etc/nginx/runtime/upstream.conf | cut -d: -f1 | tr -d ';'")

docker exec -i "${ACTIVE_CONTAINER}" python - <<'PY'
import sqlite3

db = "/srv/videocut/data/tasks.db"
with sqlite3.connect(db) as conn:
    print("integrity:", conn.execute("pragma integrity_check").fetchone()[0])
    print("external_jobs:", conn.execute("select count(*) from task_external_jobs").fetchone()[0])
    print("unknown_backfill:", conn.execute(
        "select count(*) from task_external_jobs where status='unknown'"
    ).fetchone()[0])
PY
```

`integrity` 必须为 `ok`。若需要回滚旧镜像，不要删除 `task_external_jobs`；旧代码仍从 `variables.subtitle_state` 恢复 MPS 任务。

CPU-only 服务器：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=64g \
VIDEOCUT_CPU_LIMIT=2 \
ZERO_DOWNTIME_GPU=0 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.18
```

默认最多等待旧任务排空 1 小时，优雅停止最多等待 10 分钟。可按长任务耗时调整：

```bash
DRAIN_TIMEOUT_SECONDS=7200 STOP_TIMEOUT_SECONDS=1200 \
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=24g \
VIDEOCUT_CPU_LIMIT=8 \
  ./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.18
```

若排空超时，流量已经在新版本上，但脚本会保留旧容器，绝不会强杀正在渲染的任务。稍后确认其 `/health` 中 `queueSize + localActiveWorkers` 为 `0` 后再删除即可。

## 查看日志和进入当前应用容器

蓝绿部署完成后，不再存在名为 `videocut-wrapper` 的应用容器。`videocut-wrapper:v5.17` 是镜像名；运行中的容器名是 `videocut-blue` 或 `videocut-green`，并会在每次发布后切换。因此不要再执行：

```bash
docker logs -f videocut-wrapper
docker exec -it videocut-wrapper /bin/bash
```

先从 Nginx upstream 自动识别当前接收流量的容器：

```bash
ACTIVE=$(docker exec videocut-proxy \
  cat /etc/nginx/runtime/upstream.conf | \
  sed -n 's/.*server \(videocut-\(blue\|green\)\):3000;.*/\1/p')

test -n "${ACTIVE}" || {
  echo "无法识别当前 blue/green 槽位" >&2
  exit 1
}

echo "当前应用容器：${ACTIVE}"
```

查看当前应用日志：

```bash
docker logs --tail 200 -f "${ACTIVE}"
```

进入当前应用容器：

```bash
docker exec -it "${ACTIVE}" /bin/bash
```

直接在当前容器中检查 GPU、FFmpeg 和字体：

```bash
docker exec "${ACTIVE}" python -m videocut check
```

查看代理日志：

```bash
docker logs --tail 200 -f videocut-proxy
```

查看全部蓝绿相关容器：

```bash
docker ps -a --filter name=videocut-
```

## 验证与回滚

```bash
curl http://127.0.0.1:3000/health
docker exec videocut-proxy cat /etc/nginx/runtime/upstream.conf
docker ps --filter name=videocut-
```

手动回切到仍在运行的槽位，例如 blue：

```bash
docker exec videocut-proxy sh -c \
  "printf 'server videocut-blue:3000;\\n' > /etc/nginx/runtime/upstream.conf && nginx -t && nginx -s reload"
```

注意：两个应用槽位会短暂共享 SQLite WAL 数据库和挂载目录。脚本只让新实例接收切流后的新任务，旧实例只排空切流前的本地任务；不要绕过代理直接向两个容器同时提交请求。
