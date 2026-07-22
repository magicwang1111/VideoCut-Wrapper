# VideoCut Wrapper Docker 构建与零停机部署手册

本手册从 `v5.17` 开始使用蓝绿发布：

- `videocut-proxy` 固定监听宿主机 `3000`；
- `videocut-blue`、`videocut-green` 轮流运行新旧镜像；
- 新镜像健康后才切换流量；
- 旧容器完成手中任务后再退出；
- 发布失败时保留或回切旧版本；
- SQLite、BGM、输出文件统一保存在宿主机持久目录。

旧的 `docker rm -f videocut-wrapper` 再 `docker run -p 3000:3000` 方式不再作为正常更新流程。第一次从旧部署迁移到蓝绿部署会有一次短暂停机，后续更新不需要中断服务。

## 1. 路径和版本约定

本手册使用以下示例，请按实际服务器修改服务器地址：

```text
本地仓库：D:\VideoCut-Wrapper
WSL 仓库：/mnt/d/VideoCut-Wrapper
服务器仓库：/data/VideoCut-Wrapper
服务器镜像目录：/data/images
服务器环境文件：/data/env/videocut.env
服务器持久目录：/data/videocut
镜像：videocut-wrapper:v5.17
```

同一次构建、检查、导出、导入和发布必须使用同一个版本号，避免混用 `v2`、`v5.10`、`v5.15` 等标签。

## 2. 本地构建 v5.17

进入 WSL 仓库：

```bash
cd /mnt/d/VideoCut-Wrapper

VERSION=v5.17
IMAGE=videocut-wrapper:${VERSION}
```

如本地还没有基础镜像，先构建一次：

```bash
docker build \
  -f docker/base/Dockerfile \
  -t magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  .
```

构建业务镜像：

```bash
docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION="${VERSION}" \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image ${VERSION}" \
  -t "${IMAGE}" \
  .
```

也可以使用仓库脚本。脚本会先根据本地 `input/bgm` 刷新 `docs/BGM_MANIFEST.json`，再构建镜像：

```bash
./docker/build_image.sh "${VERSION}"
```

如果本次不准备更新 BGM 清单，构建后应检查 `git diff -- docs/BGM_MANIFEST.json`，避免把无关的大型清单变化带入提交。

## 3. 本地验证镜像

GPU 检查：

```bash
docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  "${IMAGE}" \
  -m videocut check
```

GPU 正常时应包含：

```text
Video encoder: h264_nvenc
```

检查镜像标签：

```bash
docker image inspect "${IMAGE}" \
  --format '{{json .Config.Labels}}'
```

## 4. 导出并上传镜像

在 WSL 中导出：

```bash
docker save "${IMAGE}" -o "videocut-wrapper_${VERSION}.tar"
```

对应 Windows 文件：

```text
D:\VideoCut-Wrapper\videocut-wrapper_v5.17.tar
```

上传镜像和环境文件；把 `SERVER_IP` 换成实际地址：

```bash
SERVER_IP=192.168.1.100

scp "videocut-wrapper_${VERSION}.tar" \
  root@${SERVER_IP}:/data/images/

scp /mnt/d/VideoCut-Wrapper/.env \
  root@${SERVER_IP}:/data/env/videocut.env
```

不要把 `.env` 打进镜像。镜像或 tar 可能被复制，密钥应只通过服务器环境文件注入。

## 5. 同步服务器部署文件

镜像 tar 只包含应用代码，不会自动更新宿主机上的 Compose、Nginx 和发布脚本。服务器的 `/data/VideoCut-Wrapper` 必须同步到构建 `v5.17` 时使用的同一份代码。

如果服务器仓库通过 Git 管理：

```bash
cd /data/VideoCut-Wrapper
git pull --ff-only
```

至少应确认以下文件存在：

```text
docker-compose.zero-downtime.yml
docker-compose.zero-downtime.gpu.yml
deploy/nginx/nginx.conf
deploy/zero_downtime_deploy.sh
```

发布脚本需要可执行权限：

```bash
chmod +x /data/VideoCut-Wrapper/deploy/zero_downtime_deploy.sh
```

蓝绿脚本要求 Docker Compose v2。服务器必须能执行：

```bash
docker compose version
```

如果不可用，在 Ubuntu 上安装 `docker-compose-plugin`；若当前使用 Ubuntu 自带 Docker 软件源且找不到该包，则安装 `docker-compose-v2`：

```bash
apt-get update
apt-get install -y docker-compose-plugin || \
  apt-get install -y docker-compose-v2

docker compose version
```

旧版独立命令 `docker-compose` v1 不满足本部署脚本要求。

## 6. Linux 导入 v5.17

登录服务器后执行：

```bash
docker load -i /data/images/videocut-wrapper_v5.17.tar
docker image inspect videocut-wrapper:v5.17 >/dev/null
docker images | grep videocut-wrapper
```

`docker save` 已包含依赖镜像层，服务器不需要重新构建基础镜像。

## 7. 检查服务器环境文件

`/data/env/videocut.env` 中的运行路径必须是容器内 Linux 路径，不能使用 `D:\...` 等 Windows 路径：

```dotenv
PORT=3000
DB_PATH=/srv/videocut/data/tasks.db
TEMP_DIR=/srv/videocut/temp
BGM_DIR=/app/input/bgm
BGM_BACKUP_DIR=/app/input/bgm-backup
BGM_TEMPLATE_DIR=/app/input/bgm-templete
OSSUTIL_PATH=ossutil64
```

同时检查 OSS、API key、worker 数量和启动同步配置：

```dotenv
API_KEYS=替换成真实密钥
WORKER_COUNT=6
UPLOAD_WORKER_COUNT=6
SYNC_BGM_ON_STARTUP=1
SYNC_BGM_TEMPLATE_ON_STARTUP=1
```

不要把环境文件内容输出到日志或提交到 Git。

## 8. 首次从旧容器迁移

本节只执行一次。适用于当前仍由单个 `videocut-wrapper` 容器直接占用宿主机 `3000`，并且旧容器没有挂载持久目录的情况。

### 8.1 确认旧任务排空

查询旧容器中的任务：

```bash
docker exec -i videocut-wrapper python - <<'PY'
import sqlite3

db = "/srv/videocut/data/tasks.db"
with sqlite3.connect(db) as conn:
    rows = list(conn.execute("""
        select status, count(*)
        from tasks
        where status in ('pending', 'rendering')
        group by status
    """))
    print(rows)
PY
```

预期输出：

```text
[]
```

如果还有 `pending` 或 `rendering`，先等待任务完成。不要直接 `docker rm -f`。

### 8.2 停止旧容器

```bash
docker stop --timeout 600 videocut-wrapper
```

先不要删除，下一步还需要从容器复制数据。

### 8.3 创建宿主持久目录

```bash
mkdir -p \
  /data/videocut/data \
  /data/videocut/temp \
  /data/videocut/input/bgm \
  /data/videocut/input/bgm-backup \
  /data/videocut/input/bgm-templete \
  /data/videocut/output \
  /data/videocut/oss-local
```

### 8.4 复制数据库和资源

数据库是必须迁移的数据。该命令失败时不要继续删除旧容器：

```bash
docker cp videocut-wrapper:/srv/videocut/data/. \
  /data/videocut/data/
```

其余目录若在旧容器中存在则复制：

```bash
docker cp videocut-wrapper:/srv/videocut/temp/. \
  /data/videocut/temp/ || true

docker cp videocut-wrapper:/app/input/bgm/. \
  /data/videocut/input/bgm/ || true

docker cp videocut-wrapper:/app/input/bgm-backup/. \
  /data/videocut/input/bgm-backup/ || true

docker cp videocut-wrapper:/app/input/bgm-templete/. \
  /data/videocut/input/bgm-templete/ || true

docker cp videocut-wrapper:/app/output/. \
  /data/videocut/output/ || true

docker cp videocut-wrapper:/srv/videocut/oss-local/. \
  /data/videocut/oss-local/ || true
```

确认数据库已复制：

```bash
ls -lh /data/videocut/data/tasks.db
```

确认无误后删除旧容器：

```bash
docker rm videocut-wrapper
```

### 8.5 启动第一套 blue + proxy

GPU 服务器：

```bash
cd /data/VideoCut-Wrapper

VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.17
```

第一次启动完成后，宿主机 `3000` 由 `videocut-proxy` 占用，应用运行在 `videocut-blue` 或 `videocut-green` 中。

## 9. 验证 v5.17

查看容器：

```bash
docker ps --filter name=videocut-
```

正常情况下至少看到：

```text
videocut-proxy
videocut-blue 或 videocut-green
```

健康检查：

```bash
curl http://127.0.0.1:3000/health
```

查看当前流量槽位：

```bash
docker exec videocut-proxy \
  cat /etc/nginx/runtime/upstream.conf
```

自动识别当前接收流量的应用容器：

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

检查 GPU 编码器：

```bash
docker exec "${ACTIVE}" python -m videocut check
```

接口冒烟测试：

```bash
cd /data/VideoCut-Wrapper
export API_BASE_URL=http://127.0.0.1:3000
export API_KEY=替换成真实API密钥
python api-test/render_bgm_file.py
```

检查历史任务数据库仍可读取：

```bash
python - <<'PY'
import sqlite3

db = "/data/videocut/data/tasks.db"
with sqlite3.connect(db) as conn:
    for row in conn.execute("""
        select id, status, progress, created_at, completed_at, error
        from tasks
        order by created_at desc
        limit 20
    """):
        print(row)
PY
```

## 10. v5.18 及后续零停机更新

本地仍按第 2～4 节执行 build、验证、`docker save` 和 `scp`。服务器执行 `docker load` 后，不再手工停止旧容器，也不再执行 `docker run`。

例如发布 `v5.18`：

```bash
docker load -i /data/images/videocut-wrapper_v5.18.tar

cd /data/VideoCut-Wrapper

VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.18
```

发布脚本会自动：

1. 识别当前 blue/green 槽位；
2. 在另一个槽位启动新镜像；
3. 等待新容器健康检查通过；
4. reload Nginx，将新请求切换到新版本；
5. 等待旧容器的本地队列和渲染任务排空；
6. 优雅停止并删除旧应用容器。

默认最多等待旧任务排空 1 小时，停止时最多等待 10 分钟。长任务环境可以调整：

```bash
DRAIN_TIMEOUT_SECONDS=7200 \
STOP_TIMEOUT_SECONDS=1200 \
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.18
```

如果排空超时，流量已经切到新版本，但旧容器会被保留，不会强杀正在渲染的任务。

## 11. CPU-only 服务器

CPU-only 发布时关闭 GPU Compose，并按机器情况设置资源：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=64g \
VIDEOCUT_CPU_LIMIT=2 \
ZERO_DOWNTIME_GPU=0 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.17
```

CPU-only 环境预期编码器为：

```text
Video encoder: libx264
```

## 12. 回滚

如果新版本已经发布完成，需要回滚到仍保留的旧镜像，例如 `v5.17`，把旧镜像当作一次新发布即可：

```bash
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
VIDEOCUT_MEMORY_LIMIT=28g \
VIDEOCUT_CPU_LIMIT=8 \
./deploy/zero_downtime_deploy.sh videocut-wrapper:v5.17
```

如果新容器未通过健康检查，发布脚本不会切走旧流量。如果切流后的代理验证失败，脚本会尝试自动回切到旧槽位。

## 13. 清理旧镜像

先确认没有容器仍在使用目标镜像：

```bash
docker ps -a --filter ancestor=videocut-wrapper:v5.16
```

没有输出后再删除：

```bash
docker rmi videocut-wrapper:v5.16
```

不要在发布脚本运行期间执行强制镜像清理，也不要使用 `docker system prune -a` 作为日常更新步骤。

## 14. 常用排查命令

蓝绿部署后不再存在名为 `videocut-wrapper` 的应用容器。`videocut-wrapper:vX.Y` 是镜像名，实际运行容器为 `videocut-blue` 或 `videocut-green`。

查看所有蓝绿相关容器：

```bash
docker ps -a --filter name=videocut-
```

查看当前槽位：

```bash
docker exec videocut-proxy \
  cat /etc/nginx/runtime/upstream.conf
```

将当前槽位保存到变量，后续日志、检查和进入容器都使用该变量：

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

检查当前应用运行环境：

```bash
docker exec "${ACTIVE}" python -m videocut check
```

查看代理日志：

```bash
docker logs --tail 200 videocut-proxy
```

查看应用健康状态：

```bash
docker inspect --format '{{json .State.Health}}' videocut-blue
docker inspect --format '{{json .State.Health}}' videocut-green
```

查看当前任务：

```bash
curl http://127.0.0.1:3000/health
```

数据库一致性快照：

```bash
python - <<'PY'
import sqlite3

src = "/data/videocut/data/tasks.db"
dst = "/data/videocut/data/tasks_snapshot.db"

with sqlite3.connect(src) as source:
    with sqlite3.connect(dst) as target:
        source.backup(target)

print(dst)
PY
```

## 15. 不再使用的旧操作

正常更新不要再执行：

```bash
docker rm -f videocut-wrapper
docker run -d -p 3000:3000 ...
```

也不要使用 `docker commit` 制作正式版本。正式版本应修改仓库代码、重新构建带明确 tag 的镜像、验证后再通过蓝绿发布脚本上线。
