# Docker 部署命令手册

流程：本地 build `videocut-wrapper:v2`，导出 tar，传到 Linux，`docker load`，然后 `docker run` 启动。

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

build 业务镜像，tag 直接写 `v2`。建议走脚本，它会先扫描本地 `input/bgm` 并刷新 `docs/BGM_MANIFEST.json`，再执行 `docker build`：

```bash
./docker/build_image.sh v2
```

如果 BGM 不在默认目录，可以指定：

```bash
BGM_MANIFEST_SOURCE=/path/to/bgm ./docker/build_image.sh v2
```

如果本机 Python 命令不是 `python`，脚本会自动尝试 `python3`，也可以手动指定：

```bash
PYTHON=python3 ./docker/build_image.sh v2
```

本地检查镜像：

```bash
docker run --rm \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint python \
  videocut-wrapper:v2 \
  -m videocut check
```

GPU 正常时应看到：

```text
Video encoder: h264_nvenc
```

## 2. 导出镜像并传到 Linux

导出 tar 包：

```bash
docker save videocut-wrapper:v2 -o videocut-wrapper_v2.tar
```

导出后文件在：

```text
D:\VideoCut-Wrapper\videocut-wrapper_v2.tar
```

传到 Linux 服务器。下面用 `root@192.168.1.100` 做例子，实际执行时把 IP 换成你的 Linux 服务器 IP：

```bash
scp videocut-wrapper_v2.tar root@192.168.1.100:/tmp/
```

说明：`docker save` 会把业务镜像依赖的底层镜像层一起打进去，Linux 服务器不需要单独 build base 镜像。

## 3. Linux 导入镜像

登录 Linux 服务器后执行：

```bash
sudo docker load -i /tmp/videocut-wrapper_v2.tar
sudo docker images | grep videocut-wrapper
```

确认能看到：

```text
videocut-wrapper   v2
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

注意端口：当前服务容器内端口是 `3000`，这里也直接用宿主机 `3000`，所以是 `-p 3000:3000`。你以前的 `8536` 只是旧项目示例端口，不作为当前项目默认值。

### 5.1 有 GPU 的服务器，最简单启动

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 3000:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file /tmp/videocut.env \
  videocut-wrapper:v2
```

### 5.2 没有 GPU 的服务器，最简单启动

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 3000:3000 \
  --memory="64g" \
  --cpus="16" \
  --env-file /tmp/videocut.env \
  videocut-wrapper:v2
```

CPU-only 命令不要加 `--gpus all`。GPU 命令比 CPU-only 命令只多两行：

```bash
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
```

### 5.3 进入已启动容器排查

第 5.1 / 5.2 节用的是 `docker run -d`，容器会在后台自动启动服务，不需要再进容器手动执行 `python -m videocut serve`。

如果只是想进去看文件、数据库或执行检查，用 `docker exec` 进入这个已经在跑的容器：

```bash
sudo docker exec -it videocut-wrapper /bin/bash
```

进入后常用命令：

```bash
python -m videocut check
ls -lh /srv/videocut/data
python - <<'PY'
import sqlite3
db = "/srv/videocut/data/tasks.db"
conn = sqlite3.connect(db)
for row in conn.execute("select id, status, progress, created_at, completed_at, error from tasks order by created_at desc limit 20"):
    print(row)
PY
```

另开一个 Linux 服务器窗口跑测试：

```bash
cd /mnt/VideoCut-Wrapper
export API_BASE_URL=http://127.0.0.1:3000
export API_KEY=你的API_KEY
python api-test/render_bgm_file.py
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

BGM 支持按类型放在子目录里，例如：

```text
/app/input/bgm/舒缓/1.mp3
/app/input/bgm/动感/1.mp3
```

程序会递归扫描 `/app/input/bgm` 下的音频文件。

当前 BGM 对齐清单在仓库里。每次用 `./docker/build_image.sh` 打包前会自动刷新：

```text
docs/BGM_MANIFEST.json
```

接口指定某一首 BGM 时，使用清单里的 `category + filename`，例如：

```json
{
  "category": "舒缓",
  "filename": "1.mp3"
}
```

如果接口里要指定某一首 BGM，就传清单里的 `category + filename`：

```bash
python api-test/render_bgm_file.py
```

对应 `/render` 请求体里就是：

```json
{
  "overrides": {
    "bgm": {
      "category": "舒缓",
      "filename": "1.mp3"
    }
  }
}
```

## 7. 删除旧镜像或切换到 v2

如果删除旧镜像时报错，通常是还有容器正在使用它。先查哪个容器占用了 `v1`：

```bash
docker ps -a --filter ancestor=videocut-wrapper:v1
```

你现在本机如果看到 `videocut-wrapper` 还在运行，说明它就是从 `videocut-wrapper:v1` 启动的。要切换到 `v2`，先停掉并删除旧容器，再用 `v2` 启动：

```bash
docker rm -f videocut-wrapper

docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 3000:3000 \
  --memory="64g" \
  --cpus="16" \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file /tmp/videocut.env \
  videocut-wrapper:v2
```

确认 v2 容器启动正常后，再删旧镜像：

```bash
docker rmi videocut-wrapper:v1
```

如果只是本地强制清理，而且你已经确认没有容器需要它，也可以：

```bash
docker rmi -f videocut-wrapper:v1
```

## 8. 下次更新到 v3

本地 build `v3`：

```bash
cd /mnt/d/VideoCut-Wrapper

docker build \
  --build-arg BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1 \
  --build-arg IMAGE_VERSION=v3 \
  --build-arg IMAGE_DESCRIPTION="VideoCut Docker image v3" \
  -t videocut-wrapper:v3 \
  .
```

导出并传到 Linux：

```bash
docker save videocut-wrapper:v3 -o videocut-wrapper_v3.tar
scp videocut-wrapper_v3.tar root@192.168.1.100:/tmp/
```

Linux 导入：

```bash
sudo docker load -i /tmp/videocut-wrapper_v3.tar
```

启动时把最后一行镜像名从 `videocut-wrapper:v2` 改成：

```bash
videocut-wrapper:v3
```

## 9. 临时修改镜像时才用 docker commit

正常情况不要用 `docker commit`，应该改代码后重新 build。只有临时热修时才这样做。

启动一个干净容器进去修改：

```bash
sudo docker run -it \
  --name commit-videocut-fix \
  --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --entrypoint /bin/bash \
  videocut-wrapper:v2
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
