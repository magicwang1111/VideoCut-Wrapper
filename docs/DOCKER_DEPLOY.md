# Docker 部署命令手册

流程：本地 build `videocut-wrapper:v1`，导出 tar，传到 Linux，`docker load`，然后 `docker run` 启动。

## 1. 本地 build 镜像

在本机 WSL 进入项目：

```bash
cd /mnt/d/VideoCut-Wrapper
```

build base 镜像：

```bash
docker build \
  -f docker/base/Dockerfile \
  -t magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  .
```

build 业务镜像，tag 直接写 `v1`：

```bash
docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION=v1 \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image v1" \
  -t videocut-wrapper:v1 \
  .
```

本地检查镜像：

```bash
docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  videocut-wrapper:v1 \
  -m videocut check
```

GPU 正常时应看到：

```text
Video encoder: h264_nvenc
```

## 2. 导出镜像并传到 Linux

导出 tar 包：

```bash
docker save videocut-wrapper:v1 -o videocut-wrapper_v1.tar
```

传到 Linux 服务器。下面用 `root@192.168.1.100` 做例子，实际执行时把 IP 换成你的 Linux 服务器 IP：

```bash
scp videocut-wrapper_v1.tar root@192.168.1.100:/tmp/
```

说明：`docker save` 会把业务镜像依赖的底层镜像层一起打进去，Linux 服务器不需要单独 build base 镜像。

## 3. Linux 导入镜像

登录 Linux 服务器后执行：

```bash
sudo docker load -i /tmp/videocut-wrapper_v1.tar
sudo docker images | grep videocut-wrapper
```

确认能看到：

```text
videocut-wrapper   v1
```

## 4. 准备运行时配置

Docker 镜像里已经有 Linux、Python、FFmpeg 和代码。这里准备的不是“补 Linux 环境”，而是运行时配置：

- `/opt/videocut/...` 是宿主机目录，用来保存数据库、临时文件、BGM 和输出文件。这样容器删掉重建后，数据还在。
- `/opt/videocut/.env` 是运行时环境变量，主要放 OSS AK/SK、端口、worker 数量等配置。密钥不要写进镜像里，否则镜像传给别人时密钥也跟着泄露。
- 你以前“镜像拉下来直接部署”，通常是因为那些镜像不需要外部密钥/持久化目录，或者部署平台已经帮你注入了环境变量和挂载目录。

创建目录：

```bash
sudo mkdir -p \
  /opt/videocut/data \
  /opt/videocut/temp \
  /opt/videocut/input/bgm \
  /opt/videocut/output \
  /opt/videocut/oss-local
```

你本地已经有 `D:\VideoCut-Wrapper\.env`，可以直接传到 Linux：

```bash
scp /mnt/d/VideoCut-Wrapper/.env root@192.168.1.100:/tmp/videocut.env
```

在 Linux 上移动到部署目录：

```bash
sudo mv /tmp/videocut.env /opt/videocut/.env
sudo chmod 600 /opt/videocut/.env
```

如果不想传本地 `.env`，也可以在 Linux 上手动创建：

```bash
sudo vim /opt/videocut/.env
```

填入下面内容，OSS 的 AK/SK 按你的真实值修改：

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

### 5.1 有 GPU 的服务器

GPU 服务器用这条：

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file /opt/videocut/.env \
  -v /opt/videocut/data:/srv/videocut/data \
  -v /opt/videocut/temp:/srv/videocut/temp \
  -v /opt/videocut/input/bgm:/app/input/bgm \
  -v /opt/videocut/output:/app/output \
  -v /opt/videocut/oss-local:/srv/videocut/oss-local \
  videocut-wrapper:v1
```

### 5.2 没有 GPU 的服务器

CPU-only 服务器用这条，不要加 `--gpus all`：

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --env-file /opt/videocut/.env \
  -v /opt/videocut/data:/srv/videocut/data \
  -v /opt/videocut/temp:/srv/videocut/temp \
  -v /opt/videocut/input/bgm:/app/input/bgm \
  -v /opt/videocut/output:/app/output \
  -v /opt/videocut/oss-local:/srv/videocut/oss-local \
  videocut-wrapper:v1
```

GPU 命令比 CPU 命令多两行：

```bash
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
```

## 6. 验证

查看容器：

```bash
sudo docker ps --filter "name=videocut-wrapper"
sudo docker logs -f videocut-wrapper
```

健康检查：

```bash
curl http://127.0.0.1:8536/health
```

检查编码器：

```bash
sudo docker exec videocut-wrapper python -m videocut check
```

预期：

```text
有 GPU: Video encoder: h264_nvenc
无 GPU: Video encoder: libx264
```

## 7. 更新到 v2

本地 build `v2`：

```bash
cd /mnt/d/VideoCut-Wrapper

docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION=v2 \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image v2" \
  -t videocut-wrapper:v2 \
  .
```

导出并传到 Linux：

```bash
docker save videocut-wrapper:v2 -o videocut-wrapper_v2.tar
scp videocut-wrapper_v2.tar root@192.168.1.100:/tmp/
```

Linux 导入：

```bash
sudo docker load -i /tmp/videocut-wrapper_v2.tar
```

启动时把最后一行镜像名从 `videocut-wrapper:v1` 改成：

```bash
videocut-wrapper:v2
```

## 8. 临时修改镜像时才用 docker commit

正常情况不要用 `docker commit`，应该改代码后重新 build。只有临时热修时才这样做。

启动一个干净容器进去修改：

```bash
sudo docker run -it \
  --name commit-videocut-fix \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint /bin/bash \
  videocut-wrapper:v1
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
  videocut-wrapper:v2
```

导出热修镜像：

```bash
sudo docker save videocut-wrapper:v2 -o videocut-wrapper_v2.tar
```
