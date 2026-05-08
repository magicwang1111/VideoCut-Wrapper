# Docker 部署命令手册

目标流程只有一条：在本地 `D:\VideoCut-Wrapper` build 镜像，导出成 tar，传到 Linux，`docker load` 后启动。

当前镜像名约定：

```bash
videocut-wrapper:v1
```

## 1. 本地 build 镜像

在本机 WSL 里进入项目：

```bash
cd /mnt/d/VideoCut-Wrapper
```

先 build base 镜像：

```bash
docker build \
  -f docker/base/Dockerfile \
  -t magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  .
```

再 build 业务镜像。以后每次打包把 `IMAGE_TAG` 改成 `v2`、`v3` 这种递增 tag：

```bash
export IMAGE_TAG=v1

docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION="${IMAGE_TAG}" \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image ${IMAGE_TAG}" \
  -t "videocut-wrapper:${IMAGE_TAG}" \
  .
```

本地检查镜像：

```bash
docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  "videocut-wrapper:${IMAGE_TAG}" \
  -m videocut check
```

GPU 正常时应看到：

```text
Video encoder: h264_nvenc
```

## 2. 导出镜像并传到 Linux

导出 tar 包：

```bash
docker save "videocut-wrapper:${IMAGE_TAG}" -o "videocut-wrapper_${IMAGE_TAG}.tar"
```

传到 Linux 服务器：

```bash
scp "videocut-wrapper_${IMAGE_TAG}.tar" user@你的服务器IP:/tmp/
```

`docker save` 会把业务镜像依赖的底层镜像层一起打进去，Linux 服务器通常不需要单独再 build base 镜像。

## 3. Linux 服务器导入镜像

登录 Linux 服务器后导入：

```bash
export IMAGE_TAG=v1
export IMAGE="videocut-wrapper:${IMAGE_TAG}"

sudo docker load -i "/tmp/videocut-wrapper_${IMAGE_TAG}.tar"
sudo docker images | grep videocut-wrapper
```

## 4. 准备部署目录和 .env

```bash
export APP_HOME=/opt/videocut

sudo mkdir -p \
  "${APP_HOME}/data" \
  "${APP_HOME}/temp" \
  "${APP_HOME}/input/bgm" \
  "${APP_HOME}/output" \
  "${APP_HOME}/oss-local"
```

创建环境变量文件：

```bash
sudo vim "${APP_HOME}/.env"
```

最小模板如下，OSS 配置按你的真实值填写：

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

说明：

- `.env` 不要提交到 Git。
- `FFMPEG_ENCODER=auto` 会自动选择 GPU 编码器；没有 GPU 时回退到 CPU 的 `libx264`。
- 如果不想启动时同步 BGM，改成 `SYNC_BGM_ON_STARTUP=0`。

## 5. Linux 启动容器

先准备通用变量：

```bash
export IMAGE_TAG=v1
export IMAGE="videocut-wrapper:${IMAGE_TAG}"
export CONTAINER_NAME=videocut-wrapper
export APP_HOME=/opt/videocut
export HOST_PORT=8536
export CONTAINER_PORT=3000
```

### 5.1 有 GPU 的服务器

GPU 服务器使用这条：

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

GPU 版本比 CPU 版本只多这两行：

```bash
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
```

### 5.2 没有 GPU 的服务器

CPU-only 服务器使用这条，不要加 `--gpus all`：

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

## 6. 验证

查看容器：

```bash
sudo docker ps --filter "name=${CONTAINER_NAME}"
sudo docker logs -f "${CONTAINER_NAME}"
```

检查服务健康：

```bash
curl "http://127.0.0.1:${HOST_PORT}/health"
```

检查容器内编码器：

```bash
sudo docker exec "${CONTAINER_NAME}" python -m videocut check
```

预期：

```text
有 GPU: Video encoder: h264_nvenc
无 GPU: Video encoder: libx264
```

## 7. 更新到下一版

本地重新 build 时只需要递增 tag：

```bash
export IMAGE_TAG=v2
```

然后重复：

```bash
docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION="${IMAGE_TAG}" \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image ${IMAGE_TAG}" \
  -t "videocut-wrapper:${IMAGE_TAG}" \
  .

docker save "videocut-wrapper:${IMAGE_TAG}" -o "videocut-wrapper_${IMAGE_TAG}.tar"
scp "videocut-wrapper_${IMAGE_TAG}.tar" user@你的服务器IP:/tmp/
```

Linux 上重新导入并启动：

```bash
export IMAGE_TAG=v2
export IMAGE="videocut-wrapper:${IMAGE_TAG}"

sudo docker load -i "/tmp/videocut-wrapper_${IMAGE_TAG}.tar"
sudo docker rm -f videocut-wrapper
```

然后按第 5 节选择 GPU 或 CPU 启动命令。

## 8. 临时修改镜像时才用 docker commit

正常情况不要用 `docker commit`，应该改代码后重新 build。只有临时热修时才这样做：

```bash
export IMAGE=videocut-wrapper:v1
export FIXED_IMAGE=videocut-wrapper:v2

sudo docker run -it \
  --name commit-videocut-fix \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint /bin/bash \
  "${IMAGE}"
```

容器里只做最终修复，退出前清理临时文件：

```bash
rm -rf /srv/videocut/temp/* /tmp/*
exit
```

提交新镜像，`-m` 用中文：

```bash
sudo docker commit \
  -m "修复视频渲染问题" \
  commit-videocut-fix \
  "${FIXED_IMAGE}"
```

如果要把这个热修镜像也传到别的 Linux 机器：

```bash
sudo docker save "${FIXED_IMAGE}" -o videocut-wrapper_v2.tar
```
