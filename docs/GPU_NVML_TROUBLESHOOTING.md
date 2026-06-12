# GPU/NVML 渲染失败排查

本文用于排查 VideoCut Wrapper 在服务器有 NVIDIA GPU 的情况下，渲染任务仍然在 FFmpeg 归一化阶段失败的问题。

## 现象

API 任务返回类似下面的错误：

```text
Render failed.
  normalize clip 1 (clip_1.mp4): FFmpeg exited with code 187.
  stderr tail:
  ...
  cu->cuInit(0) failed -> CUDA_ERROR_NO_DEVICE: no CUDA-capable device is detected
  Device creation failed: -542398533.
  No device available for decoder: device type cuda needed for codec h264.
  [vist#0:0/h264 ...] Hardware device setup failed for decoder
  [vost#0:0/h264_nvenc ...] Error initializing a simple filtergraph
```

容器检查命令：

```bash
sudo docker inspect videocut-wrapper --format '{{json .HostConfig.DeviceRequests}}'
sudo docker exec -it videocut-wrapper nvidia-smi
sudo docker exec -it videocut-wrapper bash -lc 'ls -l /dev/nvidia* || true'
```

典型异常状态：

```text
[{"Driver":"","Count":-1,"DeviceIDs":null,"Capabilities":[["gpu"]],"Options":{}}]
Failed to initialize NVML: Unknown Error
crw-rw-rw- 1 root root 195,   0 ... /dev/nvidia0
crw-rw-rw- 1 root root 195, 255 ... /dev/nvidiactl
crw-rw-rw- 1 root root 235,   0 ... /dev/nvidia-uvm
crw-rw-rw- 1 root root 235,   1 ... /dev/nvidia-uvm-tools
```

这说明 Docker 已经请求了 GPU，容器里也能看到 NVIDIA 设备节点，但当前运行中的容器已经无法正常使用 NVML。

## 这不是什么问题

这不是 OSS 上传问题。该错误发生在 `progress=25` 左右的 `normalize clip 1` 阶段，早于最终视频上传。

这也不能说明服务器没有 GPU。宿主机仍然可能正常显示 GPU：

```bash
nvidia-smi
```

宿主机 `nvidia-smi` 正常，只能证明宿主机能看到 GPU。真正关键的是容器内检查：

```bash
sudo docker exec -it videocut-wrapper nvidia-smi
```

## 原因

VideoCut 会从运行时环境变量读取 FFmpeg 配置。下面两个值会强制使用 GPU 解码和编码：

```text
FFMPEG_ENCODER=h264_nvenc
FFMPEG_HWACCEL=cuda
```

当容器丢失 GPU/NVML 访问能力时，FFmpeg 无法初始化 CUDA 或 NVENC，归一化阶段就会失败。

NVIDIA 官方文档把这类 Docker 问题描述为：容器运行中丢失 GPU 访问，并出现下面的错误：

```text
Failed to initialize NVML: Unknown Error
```

在使用 systemd cgroup 的系统上，`systemctl daemon-reload` 可能触发运行中的 GPU 容器丢失 GPU 访问。本次服务器事故里，journal 日志中出现了相关记录：

```text
systemd[1]: Reloading requested from client PID ... ('systemctl') (unit apt-daily-upgrade.service)...
systemd[1]: Reloading...
systemd[1]: Reloading finished ...
```

参考 NVIDIA 官方文档：<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/troubleshooting.html#containers-losing-access-to-gpus-with-error-failed-to-initialize-nvml-unknown-error>

## 立即恢复

删除并重新创建容器。镜像 tag 按当前实际版本填写，例如 `v5.12`：

```bash
sudo docker rm -f videocut-wrapper 2>/dev/null || true

sudo docker run -d \
  --name videocut-wrapper \
  --restart unless-stopped \
  -p 3000:3000 \
  --memory="28g" \
  --cpus="8" \
  --gpus all \
  --device /dev/nvidia0 \
  --device /dev/nvidiactl \
  --device /dev/nvidia-uvm \
  --device /dev/nvidia-uvm-tools \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
  --env-file /data/env/videocut.env \
  videocut-wrapper:v5.12
```

验证：

```bash
sudo docker exec -it videocut-wrapper nvidia-smi
sudo docker exec -it videocut-wrapper bash -lc 'env | grep -E "NVIDIA|CUDA|FFMPEG"'
curl -s http://127.0.0.1:3000/health
```

预期结果：

```text
NVIDIA_VISIBLE_DEVICES=all
NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
FFMPEG_ENCODER=h264_nvenc
FFMPEG_HWACCEL=cuda
{"ok":true,...}
```

然后重新跑同一条渲染测试：

```bash
cd /data/VideoCut-Wrapper
python api-test/render_trim_2_5_oss_5clips.py
```

## 长期修复

把 Docker 容器的 cgroup driver 改为 `cgroupfs`。

先查看当前状态：

```bash
sudo cat /etc/docker/daemon.json
sudo docker info | grep -i 'Cgroup Driver'
```

写入配置：

```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak.$(date +%F-%H%M%S) 2>/dev/null || true

sudo python3 - <<'PY'
import json
from pathlib import Path

p = Path("/etc/docker/daemon.json")
cfg = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}

opts = cfg.get("exec-opts", [])
if "native.cgroupdriver=cgroupfs" not in opts:
    opts.append("native.cgroupdriver=cgroupfs")
cfg["exec-opts"] = opts

runtimes = cfg.setdefault("runtimes", {})
runtimes.setdefault("nvidia", {
    "path": "nvidia-container-runtime",
    "runtimeArgs": []
})

p.write_text(json.dumps(cfg, indent=2) + "\n")
PY

sudo systemctl restart docker
```

Docker 重启后，按“立即恢复”里的 `docker run` 命令重新创建 `videocut-wrapper` 容器。

验证：

```bash
sudo docker info | grep -i 'Cgroup Driver'
sudo docker exec -it videocut-wrapper nvidia-smi
```

预期：

```text
Cgroup Driver: cgroupfs
```

## 可选防护

如果渲染服务器不依赖 apt 自动升级，可以禁用 apt daily 定时器，减少自动触发 systemd reload 的机会：

```bash
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
```

这是额外防护。主要修复仍然是 Docker 使用 `cgroupfs`，并在启动容器时显式添加 NVIDIA 设备参数。

## 后续复发时采集证据

在改应用代码前，先跑下面这些命令：

```bash
nvidia-smi

sudo docker ps --filter "name=videocut-wrapper"
sudo docker inspect videocut-wrapper --format '{{json .HostConfig.DeviceRequests}}'
sudo docker exec -it videocut-wrapper nvidia-smi
sudo docker exec -it videocut-wrapper bash -lc 'ls -l /dev/nvidia* || true'
sudo docker exec -it videocut-wrapper bash -lc 'env | grep -E "NVIDIA|CUDA|FFMPEG"'

sudo docker info | grep -i 'Cgroup Driver'
sudo journalctl --since "today" | grep -Ei 'daemon-reload|apt-daily|docker|containerd|nvidia'
```

判断方式：

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| 宿主机 `nvidia-smi` 失败 | 宿主机 GPU 或驱动异常 | 先修宿主机 NVIDIA 驱动 |
| 宿主机正常，容器 `nvidia-smi` 报 NVML unknown error | 容器丢失 GPU/NVML 访问 | 重建容器，并应用 `cgroupfs` 修复 |
| 容器 `nvidia-smi` 正常，但 FFmpeg 仍失败 | 需要检查 FFmpeg/NVENC 和运行时环境变量 | 检查 `FFMPEG_ENCODER`、`FFMPEG_HWACCEL`、`ffmpeg -encoders` |
| `/health` 正常但 render 在 normalize 阶段失败 | API 可达，但 GPU 渲染链路异常 | 继续按 GPU/NVML 路径排查 |
| render 到 95% 后失败 | 这是另一层问题：上传或完成阶段 | 使用上传诊断，不要按本文处理 |

