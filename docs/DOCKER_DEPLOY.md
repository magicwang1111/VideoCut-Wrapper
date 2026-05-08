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

## 4. 把本地 .env 传到 Linux

镜像里已经有 Linux、Python、FFmpeg 和代码。`.env` 只是运行参数和密钥，例如 OSS AK/SK、端口、worker 数量。

可以把 `.env` 打进镜像，但不建议这么做：镜像一旦传给别人或保存成 tar，OSS 密钥也一起被带走；以后换 AK/SK 还要重新 build 镜像。更简单也更安全的做法是启动时用 `--env-file` 传进去。

你本地已经有 `D:\VideoCut-Wrapper\.env`，直接传到 Linux：

```bash
scp /mnt/d/VideoCut-Wrapper/.env root@192.168.1.100:/tmp/videocut.env
```

## 5. Linux 启动容器

你的旧命令里没有目录映射，只有 `-p` 端口映射。下面也先给不挂载目录的简单启动命令。

注意端口：当前服务容器内端口是 `3000`，所以这里是 `-p 8536:3000`。你以前的 `-p 8536:8080` 是把宿主机 `8536` 转发到容器内 `8080`，那是另一个镜像的服务端口。

### 5.1 有 GPU 的服务器，最简单启动

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
  --env-file /tmp/videocut.env \
  videocut-wrapper:v1
```

### 5.2 没有 GPU 的服务器，最简单启动

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --env-file /tmp/videocut.env \
  videocut-wrapper:v1
```

CPU-only 命令不要加 `--gpus all`。GPU 命令比 CPU-only 命令只多两行：

```bash
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
```

### 5.3 按以前开发方式进容器手动启动

当前代码支持这种方式，不需要改业务代码。容器启动时会自动读取 `/app/.env`；如果把宿主机项目目录映射到 `/app`，并且项目目录里有 `.env`，就不需要再写 `--env-file`。

像旧项目一样映射代码目录并进容器：

```bash
sudo docker rm -f videocut-wrapper_wx 2>/dev/null || true

sudo docker run -it \
  --name videocut-wrapper_wx \
  -v /data/wangxi/VideoCut-Wrapper:/app \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  videocut-wrapper:v1 \
  /bin/bash
```

如果不映射代码目录，就用前面传上去的 `/tmp/videocut.env`：

```bash
sudo docker rm -f videocut-wrapper_wx 2>/dev/null || true

sudo docker run -it \
  --name videocut-wrapper_wx \
  -p 8536:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file /tmp/videocut.env \
  videocut-wrapper:v1 \
  /bin/bash
```

进容器后手动启动服务。映射代码目录时，先安装当前 `/app`：

```bash
python -m pip install -e /app
rm -rf /srv/videocut/temp/*
python -m videocut serve --host 0.0.0.0 --port 3000
```

再开一个 Linux 服务器外部窗口跑测试：

```bash
export API_BASE_URL=http://127.0.0.1:3000
export API_KEY=你的API_KEY
python api-test/http_api_test_client.py --group 1
```

## 6. 验证

查看容器：

```bash
sudo docker ps --filter "name=videocut-wrapper"
sudo docker logs -f videocut-wrapper
```

健康检查：

```bash
curl http://127.0.0.1:3000/health
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
