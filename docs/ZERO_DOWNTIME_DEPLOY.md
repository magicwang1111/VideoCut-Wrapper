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

## 首次迁移

当前旧容器直接占用了 `3000`，而且旧部署没有挂载持久目录，所以第一次迁移无法完全零停机。先等待旧容器任务排空，然后停止容器（先不要删除）：

```bash
curl http://127.0.0.1:3000/health
docker stop --time 600 videocut-wrapper
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

启动第一套 blue + proxy：

```bash
cd /data/VideoCut-Wrapper
chmod +x deploy/zero_downtime_deploy.sh

VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:vNext
```

从下一次更新开始不再需要手动停止或删除旧容器。

## 后续每次更新

GPU 服务器（默认）：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:vNext
```

CPU-only 服务器：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=64g \
VIDEOCUT_CPU_LIMIT=2 \
ZERO_DOWNTIME_GPU=0 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:vNext
```

默认最多等待旧任务排空 1 小时，优雅停止最多等待 10 分钟。可按长任务耗时调整：

```bash
DRAIN_TIMEOUT_SECONDS=7200 STOP_TIMEOUT_SECONDS=1200 \
  ./deploy/zero_downtime_deploy.sh videocut-wrapper:vNext
```

若排空超时，流量已经在新版本上，但脚本会保留旧容器，绝不会强杀正在渲染的任务。稍后确认其 `/health` 中 `queueSize + localActiveWorkers` 为 `0` 后再删除即可。

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
