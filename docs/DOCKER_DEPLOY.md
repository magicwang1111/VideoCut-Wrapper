# Docker 部署操作手册

本文档基于当前仓库 `/mnt/d/VideoCut-Wrapper` 和本地已经验证通过的镜像编写，用于把 VideoCut Wrapper 部署到 Linux 服务器。命令默认使用 `sudo docker`；如果部署账号已经加入 `docker` 用户组，可以去掉 `sudo`。

当前已验证的本地镜像：

```text
base 镜像: magicwang/pytorch-base:torch210-cu128-runtime-v1
业务镜像: videocut-wrapper:v1
```

`videocut-wrapper:v1` 已验证：

```text
Python: 3.12.3
FFmpeg: n7.1.1
GPU 可用时: Video encoder: h264_nvenc
无 GPU 时: Video encoder: libx264
```

## 1. 部署方式怎么选

推荐两种方式：

1. Linux 服务器上有仓库代码：直接在服务器上 `docker build`，再启动容器。
2. Linux 服务器上没有仓库代码：在当前机器打好 `videocut-wrapper:v1`，用 `docker save` 导出，再拷到 Linux 服务器 `docker load`。

默认不依赖镜像仓库。除非你后续有自己的镜像仓库，否则文档里的镜像名就保持本地镜像名：

```bash
videocut-wrapper:v1
```

## 2. 部署机目录约定

在 Linux 服务器上准备变量：

```bash
export IMAGE="videocut-wrapper:v1"
export CONTAINER_NAME="videocut-wrapper"
export HOST_PORT="8536"
export CONTAINER_PORT="3000"
export APP_HOME="/opt/videocut"
```

创建宿主机目录：

```bash
sudo mkdir -p \
  "${APP_HOME}/data" \
  "${APP_HOME}/temp" \
  "${APP_HOME}/input/bgm" \
  "${APP_HOME}/output" \
  "${APP_HOME}/oss-local"
```

目录含义：

```text
${APP_HOME}/data       -> /srv/videocut/data       SQLite 任务库
${APP_HOME}/temp       -> /srv/videocut/temp       上传、下载和渲染临时目录
${APP_HOME}/input/bgm  -> /app/input/bgm           BGM 文件目录
${APP_HOME}/output     -> /app/output              本地渲染输出目录
${APP_HOME}/oss-local  -> /srv/videocut/oss-local  本地 OSS 模式目录
```

## 3. 部署前检查

检查 Docker：

```bash
sudo docker version
sudo docker compose version
```

GPU 服务器需要检查 NVIDIA 驱动和 NVIDIA Container Toolkit：

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

如果服务器没有 GPU，不要加 `--gpus all`，也不要叠加 `docker-compose.gpu.yml`。应用层的 `FFMPEG_ENCODER=auto` 会在容器内探测 GPU 编码器；探测不到时自动回退到 `libx264` CPU 编码。

### 3.1 CPU 和 GPU 命令差异速查

核心区别只有这几处：

```text
CPU-only 服务器:
  不加 --gpus all
  不加 NVIDIA_DRIVER_CAPABILITIES
  Compose 只用 docker-compose.yml
  预期编码器是 libx264

GPU 服务器:
  加 --gpus all
  加 -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
  Compose 叠加 docker-compose.gpu.yml
  预期编码器是 h264_nvenc
```

自检命令对照：

```bash
# CPU-only 服务器
sudo docker run --rm \
  --entrypoint python \
  --env-file "${APP_HOME}/.env" \
  "${IMAGE}" \
  -m videocut check
```

```bash
# GPU 服务器，比 CPU-only 多 --gpus all 和 NVIDIA_DRIVER_CAPABILITIES
sudo docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  --env-file "${APP_HOME}/.env" \
  "${IMAGE}" \
  -m videocut check
```

Compose 启动命令对照：

```bash
# CPU-only 服务器
sudo docker compose up -d --build
```

```bash
# GPU 服务器，叠加 docker-compose.gpu.yml
sudo docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

## 4. 准备 .env

在 Linux 服务器创建 `.env`：

```bash
sudo vim "${APP_HOME}/.env"
```

推荐模板如下，按你的真实 OSS 配置修改：

```bash
PORT=3000
API_KEYS=change-me
LOG_LEVEL=INFO
TZ=Asia/Shanghai

FFMPEG_PATH=
FFPROBE_PATH=
FFMPEG_ENCODER=auto
FFMPEG_HWACCEL=

OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_ACCESS_KEY_ID=填真实AK
OSS_ACCESS_KEY_SECRET=填真实SK
OSS_STS_TOKEN=
OSS_BUCKET=goumee-coze
OSS_PREFIX=GouMei-Video-Cut
OSS_LOCAL_ROOT=

SYNC_BGM_ON_STARTUP=1
BGM_DIR=/app/input/bgm
BGM_OSS_URI=oss://goumee-coze/GouMei-Video-Cut/bgm/

WORKER_COUNT=0
QUEUE_MAX=200
TASK_MAX_ATTEMPT=3
TASK_TTL_DAYS=7
DB_PATH=/srv/videocut/data/tasks.db
TEMP_DIR=/srv/videocut/temp
```

注意：

- `.env` 里有真实 OSS AK/SK，不要提交到 Git，也不要贴到群里。
- 如果只是本地联调，不连真实 OSS，可以设置 `OSS_LOCAL_ROOT=/srv/videocut/oss-local`。
- 如果不想启动时同步 BGM，可以设置 `SYNC_BGM_ON_STARTUP=0`。
- `PORT=3000` 是容器内端口；宿主机对外端口由 `HOST_PORT` 控制，例如 `8536:3000`。

## 5. 方式 A：在 Linux 服务器上从源码构建

在服务器上拉取或拷贝本仓库代码后，进入仓库根目录。

先构建 base 镜像：

```bash
sudo docker build \
  -f docker/base/Dockerfile \
  -t magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  .
```

再构建业务镜像。每次正式打包建议递增 tag，例如 `v1`、`v2`、`v3`：

```bash
export IMAGE_TAG="v1"
export IMAGE_DESCRIPTION="Docker GPU/CPU deployment"

sudo docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION="${IMAGE_TAG}" \
  --build-arg IMAGE_DESCRIPTION="${IMAGE_DESCRIPTION}" \
  -t "videocut-wrapper:${IMAGE_TAG}" \
  .

export IMAGE="videocut-wrapper:${IMAGE_TAG}"
```

查看镜像 label：

```bash
sudo docker inspect "${IMAGE}" \
  --format 'version={{ index .Config.Labels "org.opencontainers.image.version" }} desc={{ index .Config.Labels "org.opencontainers.image.description" }}'
```

## 6. 方式 B：从当前机器导出镜像到 Linux 服务器

当前机器已经有业务镜像：

```bash
docker images | grep videocut-wrapper
```

导出业务镜像：

```bash
docker save videocut-wrapper:v1 -o videocut-wrapper_v1.tar
```

把 tar 包拷到 Linux 服务器，例如：

```bash
scp videocut-wrapper_v1.tar user@your-linux-host:/tmp/
```

在 Linux 服务器导入：

```bash
sudo docker load -i /tmp/videocut-wrapper_v1.tar
sudo docker images | grep videocut-wrapper
export IMAGE="videocut-wrapper:v1"
```

说明：`docker save videocut-wrapper:v1` 会把该镜像依赖的底层镜像层一起打进 tar，Linux 服务器一般不需要单独再拉 base 镜像。

## 7. 镜像自检

CPU-only 检查：

```bash
sudo docker run --rm \
  --entrypoint python \
  --env-file "${APP_HOME}/.env" \
  "${IMAGE}" \
  -m videocut check
```

预期无 GPU 时看到：

```text
Video encoder: libx264
```

GPU 检查：

```bash
sudo docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  --env-file "${APP_HOME}/.env" \
  "${IMAGE}" \
  -m videocut check
```

预期 GPU 可用时看到：

```text
Video encoder: h264_nvenc
```

如果 GPU 机器仍然显示 `libx264`，优先检查：

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
sudo docker run --rm --gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video --entrypoint sh "${IMAGE}" -lc 'ldconfig -p | grep libnvidia-encode || true'
```

## 8. 使用 docker run 部署

这一节直接复制对应服务器类型的整段命令即可。CPU-only 命令不包含 GPU 参数；GPU 命令只比 CPU-only 多两行：

```bash
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
```

### 8.1 CPU-only 服务器

```bash
sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  --memory="64g" \
  --cpus="16" \
  --env-file "${APP_HOME}/.env" \
  -v "${APP_HOME}/data:/srv/videocut/data" \
  -v "${APP_HOME}/temp:/srv/videocut/temp" \
  -v "${APP_HOME}/input/bgm:/app/input/bgm" \
  -v "${APP_HOME}/output:/app/output" \
  -v "${APP_HOME}/oss-local:/srv/videocut/oss-local" \
  "${IMAGE}"
```

### 8.2 GPU 服务器

```bash
sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file "${APP_HOME}/.env" \
  -v "${APP_HOME}/data:/srv/videocut/data" \
  -v "${APP_HOME}/temp:/srv/videocut/temp" \
  -v "${APP_HOME}/input/bgm:/app/input/bgm" \
  -v "${APP_HOME}/output:/app/output" \
  -v "${APP_HOME}/oss-local:/srv/videocut/oss-local" \
  "${IMAGE}"
```

如果只想让容器看到某一张 GPU，例如第 0 张卡：

```bash
sudo docker run --gpus '"device=0"' ...
```

或者容器暴露全部 GPU，但应用进程只看第 0 张卡：

```bash
sudo docker run --gpus all -e CUDA_VISIBLE_DEVICES=0 ...
```

## 9. 使用 docker compose 部署

如果 Linux 服务器上有仓库代码，可以使用 Compose。

Compose 会读取仓库根目录的 `.env`。如果你前面把配置写在了 `${APP_HOME}/.env`，先复制一份到仓库根目录：

```bash
cp "${APP_HOME}/.env" .env
```

CPU-only 服务器：

```bash
export IMAGE_TAG="v1"
export IMAGE_DESCRIPTION="Docker CPU deployment"
sudo docker compose up -d --build
```

GPU 服务器：

```bash
export IMAGE_TAG="v1"
export IMAGE_DESCRIPTION="Docker GPU deployment"
sudo docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

两条 Compose 命令的区别是：CPU-only 只加载 `docker-compose.yml`；GPU 多加载 `docker-compose.gpu.yml`，这个 override 文件会增加 `gpus: all` 和 `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`。

说明：

- `docker-compose.yml` 不强制申请 GPU，CPU-only 服务器可以直接启动。
- `docker-compose.gpu.yml` 才会加上 `gpus: all` 和 `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`。
- 执行 `docker compose config` 会展开 `.env`，可能打印密钥，不要把完整输出发到不可信位置。

## 10. 启动后验证

查看容器状态：

```bash
sudo docker ps --filter "name=${CONTAINER_NAME}"
sudo docker logs -f "${CONTAINER_NAME}"
```

健康检查：

```bash
curl "http://127.0.0.1:${HOST_PORT}/health"
```

进入容器检查运行环境：

```bash
sudo docker exec "${CONTAINER_NAME}" python -m videocut check
```

常见预期：

```text
Video encoder: h264_nvenc   # GPU 服务器
Video encoder: libx264      # CPU-only 服务器
```

如果启动日志停在 BGM 同步，检查 `.env` 里的 OSS 配置。临时跳过 BGM 同步可以设置：

```bash
SYNC_BGM_ON_STARTUP=0
```

## 11. 交互式排查容器

启动一个不影响正式容器的测试容器：

```bash
sudo docker rm -f test-videocut-wrapper 2>/dev/null || true

sudo docker run -it \
  --name test-videocut-wrapper \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file "${APP_HOME}/.env" \
  --entrypoint /bin/bash \
  "${IMAGE}"
```

如果是 CPU-only 服务器，去掉 `--gpus all` 和 `NVIDIA_DRIVER_CAPABILITIES` 这一行。

容器里常用命令：

```bash
python -m videocut check
rm -rf /srv/videocut/temp/*
python -m videocut serve --host 0.0.0.0 --port 3000
```

如果需要从外部拉修复文件，优先用 `wget`：

```bash
wget -O /app/videocut/some_file.py "https://example.com/some_file.py"
```

不要用 OSS 拉临时修复文件，避免被 OSS 凭证、endpoint 或启动同步逻辑干扰。

## 12. 镜像有问题时的临时 docker commit 流程

原则：

- 正式修复应该改代码、改 Dockerfile，然后重新执行 `docker build`。
- `docker commit` 只适合重新构建太慢、线上急需热修的情况。
- 不要在跑过大量测试、产生过缓存和临时文件的容器上直接 commit。
- commit message 用中文，tag 使用新 tag，不覆盖原镜像。

推荐流程：先用测试容器验证修复，再开一个干净容器只应用最小改动并 commit。

### 12.1 在测试容器里验证问题

```bash
sudo docker rm -f test-videocut-fix 2>/dev/null || true

sudo docker run -it \
  --name test-videocut-fix \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file "${APP_HOME}/.env" \
  --entrypoint /bin/bash \
  "${IMAGE}"
```

在容器内验证和修改，确认修复思路可行后退出并删除测试容器：

```bash
exit
sudo docker rm -f test-videocut-fix
```

### 12.2 创建干净容器，只放入最终修复

```bash
export FIXED_IMAGE="videocut-wrapper:v2"

sudo docker rm -f commit-videocut-fix 2>/dev/null || true

sudo docker run -it \
  --name commit-videocut-fix \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file "${APP_HOME}/.env" \
  --entrypoint /bin/bash \
  "${IMAGE}"
```

在这个容器里只执行最终修复需要的动作，例如：

```bash
wget -O /app/videocut/some_file.py "https://example.com/some_file.py"
python -m videocut check
rm -rf /srv/videocut/temp/* /tmp/*
exit
```

提交新镜像，`-m` 使用中文说明：

```bash
sudo docker commit \
  -m "修复 GPU 渲染编码器探测问题" \
  commit-videocut-fix \
  "${FIXED_IMAGE}"
```

验证新镜像：

```bash
sudo docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  --env-file "${APP_HOME}/.env" \
  "${FIXED_IMAGE}" \
  -m videocut check
```

确认没问题后，线上容器切到新 tag：

```bash
export IMAGE="${FIXED_IMAGE}"
sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file "${APP_HOME}/.env" \
  -v "${APP_HOME}/data:/srv/videocut/data" \
  -v "${APP_HOME}/temp:/srv/videocut/temp" \
  -v "${APP_HOME}/input/bgm:/app/input/bgm" \
  -v "${APP_HOME}/output:/app/output" \
  -v "${APP_HOME}/oss-local:/srv/videocut/oss-local" \
  "${IMAGE}"
```

## 13. 回滚

回滚就是重新拉起旧 tag，例如从 `v2` 回到 `v1`：

```bash
export IMAGE="videocut-wrapper:v1"

sudo docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

sudo docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  --memory="64g" \
  --cpus="16" \
  --env-file "${APP_HOME}/.env" \
  -v "${APP_HOME}/data:/srv/videocut/data" \
  -v "${APP_HOME}/temp:/srv/videocut/temp" \
  -v "${APP_HOME}/input/bgm:/app/input/bgm" \
  -v "${APP_HOME}/output:/app/output" \
  -v "${APP_HOME}/oss-local:/srv/videocut/oss-local" \
  "${IMAGE}"
```

GPU 服务器回滚时同样加上：

```bash
--gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
```

## 14. 常见问题

### 容器没启动，报找不到 NVIDIA runtime

CPU-only 服务器不要加 `--gpus all`。GPU 服务器检查：

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

### `FFMPEG_ENCODER=auto` 还是走 CPU

进入容器检查：

```bash
sudo docker exec "${CONTAINER_NAME}" sh -lc 'echo $NVIDIA_DRIVER_CAPABILITIES; ldconfig -p | grep libnvidia-encode || true; python -m videocut check'
```

如果没有 `libnvidia-encode.so.1`，通常是没有设置：

```bash
NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
```

### BGM 同步失败

检查 `.env`：

```text
OSS_ENDPOINT
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
BGM_OSS_URI
SYNC_BGM_ON_STARTUP
```

只做接口联调时，可以先设置：

```bash
SYNC_BGM_ON_STARTUP=0
```

### 端口不通

检查端口映射和健康检查：

```bash
sudo docker ps --filter "name=${CONTAINER_NAME}"
sudo docker logs --tail=200 "${CONTAINER_NAME}"
curl -v "http://127.0.0.1:${HOST_PORT}/health"
```

如果部署在云服务器，还要检查安全组和防火墙。
