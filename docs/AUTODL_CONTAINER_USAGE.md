# AutoDL 容器内使用指南

本文记录在 AutoDL / SeetaCloud 已经进入运行容器后的部署方式。此场景下不能再执行 `docker run`，需要把当前容器当作运行环境，直接启动 VideoCut Wrapper API 服务。

API 对接细节请看 `docs/API.md` 或 `docs/API_BRIEF.md`。本文只覆盖服务器侧安装、启动、验证和排错。

## 适用场景

适用于服务器终端已经是类似下面的容器 shell：

```text
(base) root@autodl-container-...:~/autodl-tmp/wangxi/videocut/app#
```

并且检查结果类似：

```bash
command -v docker || echo "docker not installed"
command -v sudo || echo "sudo not installed"
command -v nvidia-smi
command -v screen
```

典型特征：

- `docker not installed`，容器内没有 Docker daemon。
- `sudo` 不可用，或者被配置成空 alias。
- `nvidia-smi` 可用，GPU 已由 AutoDL 平台挂载到当前容器。
- `screen` 可用，适合让服务在 SSH 断开后继续运行。

如果是在真实 Linux 宿主机上部署，并且宿主机有 Docker，请继续使用 `docs/DOCKER_DEPLOY.md`。

## 服务器目录规划

AutoDL 常见目录含义：

| 路径 | 用途 | 建议 |
|---|---|---|
| `/` | 系统盘，空间较小 | 可以放少量代码和系统依赖 |
| `/root/autodl-tmp` | 数据盘，空间大、IO 快 | 推荐放项目源码、运行数据、临时文件和日志 |
| `/autodl-pub` / `/autodl-pub/data` | 公共或文件存储 | 按平台权限和业务需要使用 |

当前已整理成一个项目子目录：

```text
/root/autodl-tmp/wangxi/videocut/
├── app/        # 项目源码
├── env/        # 环境变量文件
├── runtime/    # 数据库、临时文件、BGM、日志
└── scripts/    # 可选，后续放启动/停止脚本
```

默认路径：

| 类型 | 路径 |
|---|---|
| 项目源码 | `/root/autodl-tmp/wangxi/videocut/app` |
| 环境变量文件 | `/root/autodl-tmp/wangxi/videocut/env/videocut.env` |
| 运行目录 | `/root/autodl-tmp/wangxi/videocut/runtime` |
| SQLite 数据库 | `/root/autodl-tmp/wangxi/videocut/runtime/data/tasks.db` |
| 临时文件目录 | `/root/autodl-tmp/wangxi/videocut/runtime/temp` |
| BGM 本地目录 | `/root/autodl-tmp/wangxi/videocut/runtime/input/bgm` |
| 服务日志 | `/root/autodl-tmp/wangxi/videocut/runtime/logs/server.log` |

不要继续使用 Docker 容器里的旧路径作为运行数据路径，例如：

```text
/app/input/bgm
/srv/videocut/data/tasks.db
/srv/videocut/temp
```

这些路径可能存在，也可能不存在；即使存在，也不如数据盘路径清晰可靠。

## 首次安装

进入项目源码目录：

```bash
cd /root/autodl-tmp/wangxi/videocut/app
```

安装 Python 包：

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install .
```

安装 ffmpeg、curl、unzip 和 screen：

```bash
apt-get update
apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates screen
```

apt 安装的 `ffmpeg 4.4.2` 可能能列出 `h264_nvenc`，但在 RTX 5090 上真实编码时报 `unsupported device`。如果必须使用 GPU 渲染，继续执行下面的源码编译步骤，把新版 FFmpeg 安装到 `/usr/local/bin/ffmpeg`。

安装编译依赖：

```bash
apt-get update
apt-get install -y --no-install-recommends \
  git build-essential cmake pkg-config nasm yasm \
  libx264-dev libfreetype-dev libfontconfig1-dev \
  ca-certificates curl unzip
```

编译新版 `nv-codec-headers` 和 FFmpeg：

```bash
cd /root/autodl-tmp/wangxi/videocut

rm -rf build-ffmpeg
mkdir -p build-ffmpeg
cd build-ffmpeg

git clone --depth 1 --branch n13.0.19.0 https://github.com/FFmpeg/nv-codec-headers.git
make -C nv-codec-headers -j"$(nproc)"
make -C nv-codec-headers install PREFIX=/usr/local

git clone --depth 1 --branch n7.1.1 https://github.com/FFmpeg/FFmpeg.git
cd FFmpeg

PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}" ./configure \
  --prefix=/usr/local \
  --enable-gpl \
  --enable-nonfree \
  --enable-libx264 \
  --enable-libfreetype \
  --enable-libfontconfig

make -j"$(nproc)"
make install
hash -r
```

检查 FFmpeg 路径和版本：

```bash
which ffmpeg
ffmpeg -version | head -n 1
which ffprobe
```

期望优先使用：

```text
/usr/local/bin/ffmpeg
/usr/local/bin/ffprobe
```

检查 FFmpeg 是否支持 NVENC：

```bash
ffmpeg -hide_banner -encoders | grep -E "h264_nvenc|hevc_nvenc|libx264"
```

期望至少看到：

```text
h264_nvenc
hevc_nvenc
libx264
```

做一次真实 NVENC 编码探测：

```bash
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i color=c=black:s=640x360:r=30:d=0.2 \
  -frames:v 1 \
  -c:v h264_nvenc \
  -f null -
```

这条命令没有输出并且退出码为 `0`，才表示 GPU 编码真正可用。可以用下面命令确认退出码：

```bash
echo $?
```

安装 `ossutil`：

```bash
curl -fL -o /tmp/ossutil.zip https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil-v1.7.19-linux-amd64.zip
unzip -o /tmp/ossutil.zip -d /tmp
install -m 0755 /tmp/ossutil-v1.7.19-linux-amd64/ossutil /usr/local/bin/ossutil
ossutil help >/dev/null
rm -rf /tmp/ossutil.zip /tmp/ossutil-v1.7.19-linux-amd64
```

确认依赖：

```bash
python - <<'PY'
import pathlib
import videocut
print(pathlib.Path(videocut.__file__).resolve())
PY

command -v ffmpeg
command -v ffprobe
command -v ossutil
command -v screen
nvidia-smi
```

## 环境变量配置

环境变量文件：

```text
/root/autodl-tmp/wangxi/videocut/env/videocut.env
```

创建运行目录：

```bash
mkdir -p /root/autodl-tmp/wangxi/videocut/runtime/{data,temp,input/bgm,logs,oss-local}
```

使用下面的辅助函数修改 env 文件：

```bash
ENV_FILE=/root/autodl-tmp/wangxi/videocut/env/videocut.env

set_kv() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_kv PORT 3000
set_kv TZ Asia/Shanghai
set_kv LOG_LEVEL INFO
set_kv FFMPEG_PATH /usr/local/bin/ffmpeg
set_kv FFPROBE_PATH /usr/local/bin/ffprobe
set_kv FFMPEG_ENCODER h264_nvenc
set_kv FFMPEG_HWACCEL ""
set_kv WORKER_COUNT 8
set_kv DB_PATH /root/autodl-tmp/wangxi/videocut/runtime/data/tasks.db
set_kv TEMP_DIR /root/autodl-tmp/wangxi/videocut/runtime/temp
set_kv BGM_DIR /root/autodl-tmp/wangxi/videocut/runtime/input/bgm
set_kv SYNC_BGM_ON_STARTUP 1
```

关键变量说明：

| 变量 | 说明 |
|---|---|
| `PORT` | API 监听端口，默认 `3000` |
| `API_KEYS` | 允许访问 API 的 key，多个 key 用英文逗号分隔 |
| `FFMPEG_PATH` | GPU 方案固定为 `/usr/local/bin/ffmpeg` |
| `FFPROBE_PATH` | GPU 方案固定为 `/usr/local/bin/ffprobe` |
| `FFMPEG_ENCODER` | GPU 方案固定为 `h264_nvenc`；如果失败说明 FFmpeg/NVENC 仍不支持当前 GPU |
| `FFMPEG_HWACCEL` | 通常留空；需要硬件解码时再配置 |
| `WORKER_COUNT` | 渲染 worker 数量，16 核 CPU 可先用 `8` |
| `DB_PATH` | SQLite 任务数据库路径 |
| `TEMP_DIR` | 上传和渲染临时目录 |
| `BGM_DIR` | BGM 同步和运行时扫描目录 |
| `SYNC_BGM_ON_STARTUP` | `1` 表示服务启动前从 OSS 同步 BGM |

OSS 相关变量需要在 env 文件中配置，但不要写进文档、聊天记录或公开仓库：

```text
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_ACCESS_KEY_ID=<your-access-key-id>
OSS_ACCESS_KEY_SECRET=<your-access-key-secret>
OSS_BUCKET=<your-bucket>
OSS_PREFIX=<your-prefix>
BGM_OSS_URI=oss://<your-bucket>/<your-prefix>/bgm/
```

检查当前路径配置：

```bash
grep -E "^(PORT|FFMPEG_ENCODER|WORKER_COUNT|BGM_DIR|DB_PATH|TEMP_DIR|SYNC_BGM_ON_STARTUP)=" \
  /root/autodl-tmp/wangxi/videocut/env/videocut.env
```

已验证的正确路径应类似：

```text
BGM_DIR=/root/autodl-tmp/wangxi/videocut/runtime/input/bgm
DB_PATH=/root/autodl-tmp/wangxi/videocut/runtime/data/tasks.db
TEMP_DIR=/root/autodl-tmp/wangxi/videocut/runtime/temp
```

如果 `ffmpeg -encoders` 能看到 `h264_nvenc`，但真实渲染时报：

```text
OpenEncodeSessionEx failed: unsupported device
No capable devices found
```

说明当前 FFmpeg/NVENC 组合不能真正支持这张 GPU。不要切 CPU；按上面的源码编译步骤安装新版 FFmpeg，然后确认 `FFMPEG_PATH=/usr/local/bin/ffmpeg`、`FFMPEG_ENCODER=h264_nvenc`，再重启服务。

如果新版 FFmpeg 仍然报同样错误，并且 `ls -l /dev/nvidia*` 里只有 `/dev/nvidia5`、没有 `/dev/nvidia0`，而 `nvidia-smi -L` 显示 GPU index 是 `0`，通常是容器里的 NVIDIA 设备节点编号和 CUDA/NVENC 枚举编号不一致。可以先临时做一个 `/dev/nvidia0` 到实际设备节点的软链接再测试：

```bash
ls -l /dev/nvidia*
nvidia-smi -L

test -e /dev/nvidia0 || ln -s /dev/nvidia5 /dev/nvidia0
ls -l /dev/nvidia0 /dev/nvidia5

/usr/local/bin/ffmpeg -hide_banner -loglevel error \
  -f lavfi -i color=c=black:s=640x360:r=30:d=0.2 \
  -frames:v 1 \
  -c:v h264_nvenc \
  -f null -

echo $?
```

这条测试返回 `0` 后再启动服务。这个软链接只影响当前容器运行期；如果容器重启，需要重新检查。

如果软链接后仍然失败，且输出里能看到：

```text
Loaded Nvenc version 13.0
Nvenc initialized successfully
1 CUDA capable devices found
GPU #0 - < NVIDIA GeForce RTX 5090 >
OpenEncodeSessionEx failed: unsupported device
```

说明 FFmpeg、NVENC 库和 GPU 枚举都已经到位，问题更可能在平台的容器 GPU 映射层。常见场景是 8 卡宿主机只分配了其中一张非 0 号物理卡，例如容器内只有 `/dev/nvidia5`，但 CUDA/NVENC 在容器内枚举为 `GPU 0`。这种情况下 `nvidia-smi` 能正常显示 GPU，不代表 NVENC session 一定能打开。

这不是 VideoCut 代码问题，也不是继续调整 `FFMPEG_ENCODER` 能解决的问题。需要让平台重新创建一个正确暴露 video encode capability 的容器，或分配映射为 `/dev/nvidia0` 的 GPU。给平台排查时可以提供下面的信息：

```bash
ls -l /dev/nvidia*
nvidia-smi -L
echo "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}"
ldconfig -p | grep -E 'libnvidia-encode|libcuda|libnvcuvid' || true
/usr/local/bin/ffmpeg -hide_banner -loglevel error \
  -f lavfi -i color=c=black:s=640x360:r=30:d=0.2 \
  -frames:v 1 \
  -c:v h264_nvenc \
  -f null -
echo $?
```

## 启动服务

使用 `screen` 启动后台服务：

```bash
cd /root/autodl-tmp/wangxi/videocut/app

screen -S videocut -X quit 2>/dev/null || true

screen -dmS videocut bash -lc '
  cd /root/autodl-tmp/wangxi/videocut/app
  unset PORT BGM_DIR DB_PATH TEMP_DIR FFMPEG_PATH FFPROBE_PATH FFMPEG_ENCODER FFMPEG_HWACCEL WORKER_COUNT SYNC_BGM_ON_STARTUP
  export VIDEOCUT_ENV_FILE=/root/autodl-tmp/wangxi/videocut/env/videocut.env
  sh docker/entrypoint.sh sh -c "python -m videocut serve --host 0.0.0.0 --port ${PORT:-3000}" \
    > /root/autodl-tmp/wangxi/videocut/runtime/logs/server.log 2>&1
'
```

命令含义：

- `screen -S videocut -X quit`：如果旧的 `videocut` 会话存在，先停止。
- `screen -dmS videocut ...`：创建一个名为 `videocut` 的后台会话。
- `unset ...`：清掉当前 shell 里可能残留的旧环境变量，避免覆盖 env 文件。
- `VIDEOCUT_ENV_FILE=...`：让 entrypoint 加载指定 env 文件。
- `docker/entrypoint.sh`：复用项目原来的启动逻辑，先加载 env，再按需同步 BGM。
- `python -m videocut serve --host 0.0.0.0 --port 3000`：启动 API 服务。
- `> .../server.log 2>&1`：把标准输出和错误输出都写入日志。

进入后台会话：

```bash
screen -r videocut
```

进入后如果只想退出 screen 但保持服务运行，按：

```text
Ctrl+A
D
```

## 验证服务

查看日志：

```bash
tail -n 100 /root/autodl-tmp/wangxi/videocut/runtime/logs/server.log
```

成功日志应显示 BGM 同步到数据盘：

```text
[entrypoint] syncing BGM from oss://.../bgm/ to /root/autodl-tmp/wangxi/videocut/runtime/input/bgm
```

健康检查：

```bash
curl http://127.0.0.1:3000/health
```

成功时类似：

```json
{"ok":true,"workers":8,"queueSize":0,"pipelines":8}
```

检查 ffmpeg 和 GPU 编码：

```bash
cd /root/autodl-tmp/wangxi/videocut/app
python -m videocut check
```

成功时重点看：

```text
Video encoder: h264_nvenc
```

如果这里不是 `h264_nvenc`，说明服务没有按 GPU 方案启动，需要先检查 env 里的 `FFMPEG_PATH`、`FFPROBE_PATH` 和 `FFMPEG_ENCODER`。

检查 screen 会话：

```bash
screen -ls
```

成功时类似：

```text
There is a screen on:
        29378.videocut  (Detached)
```

外部访问时，使用 AutoDL 控制台给 `3000` 端口映射出来的公网地址。容器内本地测试仍然使用：

```text
http://127.0.0.1:3000
```

### 日志、服务和接口测试命令

`server.log` 会一直写到这里：

```text
/root/autodl-tmp/wangxi/videocut/runtime/logs/server.log
```

查看最新日志：

```bash
tail -n 100 /root/autodl-tmp/wangxi/videocut/runtime/logs/server.log
```

实时跟随日志：

```bash
tail -f /root/autodl-tmp/wangxi/videocut/runtime/logs/server.log
```

查看正在运行的服务：

```bash
screen -ls
ps -ef | grep -E "python -m videocut|uvicorn" | grep -v grep || true
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || lsof -iTCP:3000 -sTCP:LISTEN -P -n 2>/dev/null || true) | grep ':3000' || true
curl http://127.0.0.1:3000/health
```

如果 `ss`、`netstat`、`lsof` 都不存在，也不影响服务运行；以 `screen -ls`、`ps` 和 `/health` 为准。

下面这组命令适合服务启动后直接复制执行。它会从 env 文件读取第一个 `API_KEYS`，不会把真实 key 写进文档。

```bash
cd /root/autodl-tmp/wangxi/videocut/app

ENV_FILE=/root/autodl-tmp/wangxi/videocut/env/videocut.env
API_KEY="$(grep '^API_KEYS=' "$ENV_FILE" | cut -d= -f2- | cut -d, -f1)"
BASE_URL=http://127.0.0.1:3000

echo "== health =="
curl -sS "$BASE_URL/health"
echo

echo "== screen =="
screen -ls

echo "== runtime paths =="
grep -E "^(BGM_DIR|DB_PATH|TEMP_DIR)=" "$ENV_FILE"

echo "== bgm local files =="
du -sh /root/autodl-tmp/wangxi/videocut/runtime/input/bgm
find /root/autodl-tmp/wangxi/videocut/runtime/input/bgm -type f | head

echo "== bgm api =="
curl -sS "$BASE_URL/bgm" \
  -H "X-Api-Key: $API_KEY" | python -m json.tool --no-ensure-ascii | head -80

echo "== ffmpeg check =="
python -m videocut check

echo "== pipelines =="
python -m videocut pipelines
```

预期重点：

- `/health` 返回 `ok: true`。
- `screen -ls` 里有 `videocut`。
- `BGM_DIR` 是 `/root/autodl-tmp/wangxi/videocut/runtime/input/bgm`。
- `du -sh` 能看到 BGM 目录大小，当前同步约 99 MB。
- `/bgm` 能返回 `categories` 和 `files`。
- `python -m videocut check` 显示 `Video encoder: h264_nvenc`。

### 测试变量初始化

后面的测试命令都复用这一组变量。接口里的 `clips` 要传 OSS key，不要带 `oss://bucket/` 前缀。比如素材地址是：

```text
oss://goumee-coze/GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4
```

请求里应写成：

```text
GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4
```

初始化变量：

```bash
cd /root/autodl-tmp/wangxi/videocut/app

ENV_FILE=/root/autodl-tmp/wangxi/videocut/env/videocut.env
BASE_URL=http://127.0.0.1:3000
API_KEY="$(grep '^API_KEYS=' "$ENV_FILE" | cut -d= -f2- | cut -d, -f1)"

CLIP_KEY="GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4"
BGM_CATEGORY="kpop"
BGM_FILENAME="Hyperpop"
LOCAL_AUDIO=/root/autodl-tmp/wangxi/videocut/test/1.mp3

echo "BASE_URL=$BASE_URL"
echo "CLIP_KEY=$CLIP_KEY"
echo "BGM=$BGM_CATEGORY/$BGM_FILENAME"
```

确认指定音乐存在：

```bash
curl -sS "$BASE_URL/bgm" \
  -H "X-Api-Key: $API_KEY" \
  | python -m json.tool --no-ensure-ascii \
  | grep -A4 -B2 "\"filename\": \"$BGM_FILENAME\""
```

### 渲染测试：指定音乐

下面示例使用指定 BGM：`kpop/Hyperpop`。命令会自动保存返回的 `taskId` 到 `TASK_ID_SPECIFIED`。

```bash
TASK_JSON_SPECIFIED="$(curl -sS -X POST "$BASE_URL/render" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"pipeline\": \"bgm-concat\",
    \"clips\": [
      \"$CLIP_KEY\"
    ],
    \"overrides\": {
      \"bgm\": {
        \"category\": \"$BGM_CATEGORY\",
        \"filename\": \"$BGM_FILENAME\"
      }
    }
  }")"

echo "$TASK_JSON_SPECIFIED" | python -m json.tool --no-ensure-ascii

TASK_ID_SPECIFIED="$(printf '%s' "$TASK_JSON_SPECIFIED" | python -c 'import json, sys; print(json.load(sys.stdin)["taskId"])')"
echo "TASK_ID_SPECIFIED=$TASK_ID_SPECIFIED"
```

### 渲染测试：指定类目随机音乐

只传 `category`，不传 `filename`，服务会在该类目下随机选一首 BGM。命令会自动保存返回的 `taskId` 到 `TASK_ID_CATEGORY_RANDOM`。

```bash
TASK_JSON_CATEGORY_RANDOM="$(curl -sS -X POST "$BASE_URL/render" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"pipeline\": \"bgm-concat\",
    \"clips\": [
      \"$CLIP_KEY\"
    ],
    \"overrides\": {
      \"bgm\": {
        \"category\": \"$BGM_CATEGORY\"
      }
    }
  }")"

echo "$TASK_JSON_CATEGORY_RANDOM" | python -m json.tool --no-ensure-ascii

TASK_ID_CATEGORY_RANDOM="$(printf '%s' "$TASK_JSON_CATEGORY_RANDOM" | python -c 'import json, sys; print(json.load(sys.stdin)["taskId"])')"
echo "TASK_ID_CATEGORY_RANDOM=$TASK_ID_CATEGORY_RANDOM"
```

如果不传 `overrides.bgm`，服务会在整个 `BGM_DIR` 下递归随机选一首。命令会自动保存返回的 `taskId` 到 `TASK_ID_FULL_RANDOM`。

```bash
TASK_JSON_FULL_RANDOM="$(curl -sS -X POST "$BASE_URL/render" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"pipeline\": \"bgm-concat\",
    \"clips\": [
      \"$CLIP_KEY\"
    ],
    \"overrides\": {}
  }")"

echo "$TASK_JSON_FULL_RANDOM" | python -m json.tool --no-ensure-ascii

TASK_ID_FULL_RANDOM="$(printf '%s' "$TASK_JSON_FULL_RANDOM" | python -c 'import json, sys; print(json.load(sys.stdin)["taskId"])')"
echo "TASK_ID_FULL_RANDOM=$TASK_ID_FULL_RANDOM"
```

### 渲染测试：上传本地音乐

本地音乐示例路径：

```text
/root/autodl-tmp/wangxi/videocut/test/1.mp3
```

先上传音乐：

```bash
test -f "$LOCAL_AUDIO" || echo "missing local audio: $LOCAL_AUDIO"

AUDIO_JSON="$(curl -sS -X POST "$BASE_URL/upload" \
  -H "X-Api-Key: $API_KEY" \
  -F "file=@${LOCAL_AUDIO}")"

echo "$AUDIO_JSON" | python -m json.tool --no-ensure-ascii

AUDIO_FILE_ID="$(printf '%s' "$AUDIO_JSON" | python -c 'import json, sys; print(json.load(sys.stdin)["fileId"])')"
echo "AUDIO_FILE_ID=$AUDIO_FILE_ID"
```

再用上传返回的 `fileId` 渲染。命令会自动保存返回的 `taskId` 到 `TASK_ID_UPLOAD_AUDIO`。

```bash
TASK_JSON_UPLOAD_AUDIO="$(curl -sS -X POST "$BASE_URL/render" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"pipeline\": \"bgm-concat\",
    \"clips\": [
      \"$CLIP_KEY\"
    ],
    \"overrides\": {
      \"bgm\": {
        \"fileId\": \"$AUDIO_FILE_ID\"
      }
    }
  }")"

echo "$TASK_JSON_UPLOAD_AUDIO" | python -m json.tool --no-ensure-ascii

TASK_ID_UPLOAD_AUDIO="$(printf '%s' "$TASK_JSON_UPLOAD_AUDIO" | python -c 'import json, sys; print(json.load(sys.stdin)["taskId"])')"
echo "TASK_ID_UPLOAD_AUDIO=$TASK_ID_UPLOAD_AUDIO"
```

### 压力测试：16 个并发任务，指定音乐

下面命令会一次提交 16 个并发渲染任务，每个任务都使用同一个测试素材和指定 BGM `kpop/Hyperpop`。它会持续轮询直到全部完成、失败或超时。

```bash
cd /root/autodl-tmp/wangxi/videocut/app

ENV_FILE=/root/autodl-tmp/wangxi/videocut/env/videocut.env
BASE_URL=http://127.0.0.1:3000
export BASE_URL
export API_KEY="$(grep '^API_KEYS=' "$ENV_FILE" | cut -d= -f2- | cut -d, -f1)"

python - <<'PY'
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time

import requests

base_url = os.environ.get("BASE_URL", "http://127.0.0.1:3000").rstrip("/")
api_key = os.environ["API_KEY"]
headers = {
    "X-Api-Key": api_key,
    "Content-Type": "application/json",
}

payload = {
    "pipeline": "bgm-concat",
    "clips": [
        "GouMei-Video-Cut/test-input/1/kling_20260329_作品_镜头固定_原地展示穿_4390_0.mp4",
    ],
    "overrides": {
        "bgm": {
            "category": "kpop",
            "filename": "Hyperpop",
        },
    },
}


def submit(index: int) -> str:
    response = requests.post(
        f"{base_url}/render",
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    task_id = response.json()["taskId"]
    print(f"[submit {index:02d}] {task_id}")
    return task_id


with ThreadPoolExecutor(max_workers=16) as executor:
    future_map = {executor.submit(submit, index): index for index in range(1, 17)}
    task_ids = []
    for future in as_completed(future_map):
        task_ids.append(future.result())

print("[submitted]")
print(json.dumps(task_ids, ensure_ascii=False, indent=2))

deadline = time.time() + 7200
remaining = set(task_ids)
final = {}

while remaining:
    for task_id in list(remaining):
        response = requests.get(
            f"{base_url}/tasks/{task_id}",
            headers={"X-Api-Key": api_key},
            timeout=60,
        )
        response.raise_for_status()
        task = response.json()
        status = task.get("status")
        progress = task.get("progress")
        attempt = task.get("attempt")
        print(f"[poll] {task_id} status={status} progress={progress} attempt={attempt}")
        if status in {"completed", "failed"}:
            final[task_id] = task
            remaining.remove(task_id)

    if not remaining:
        break
    if time.time() > deadline:
        raise TimeoutError(f"timeout; remaining={sorted(remaining)}")
    time.sleep(10)

summary = {
    "total": len(task_ids),
    "completed": sum(1 for task in final.values() if task.get("status") == "completed"),
    "failed": sum(1 for task in final.values() if task.get("status") == "failed"),
    "tasks": {
        task_id: {
            "status": task.get("status"),
            "progress": task.get("progress"),
            "outputUrl": task.get("outputUrl"),
            "error": task.get("error"),
        }
        for task_id, task in final.items()
    },
}
print("[summary]")
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
```

### 查询任务和下载结果

创建渲染任务会返回 `taskId`。拿到 `taskId` 后查询任务：

```bash
TASK_ID="$TASK_ID_SPECIFIED"

curl -sS "$BASE_URL/tasks/$TASK_ID" \
  -H "X-Api-Key: $API_KEY" | python -m json.tool --no-ensure-ascii
```

任务完成后下载结果：

```bash
curl -L "$BASE_URL/tasks/$TASK_ID/download" \
  -H "X-Api-Key: $API_KEY" \
  -o final.mp4
```

## 安全提醒

- 不要把真实 `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_STS_TOKEN`、`API_KEYS` 写进文档、提交到 Git，或粘贴到公开聊天记录。
- 如果密钥已经泄露，应在阿里云控制台轮换密钥，并更新服务器上的 `/root/autodl-tmp/wangxi/videocut/env/videocut.env`。
- 建议给 OSS 账号使用最小权限，只允许访问当前服务需要的 bucket 和 prefix。
- env 文件建议只保留在服务器上，不随镜像、tar 包或源码一起分发。
