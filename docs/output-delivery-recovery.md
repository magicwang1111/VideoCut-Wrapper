# 输出交付与本地清理

上传后使用 SDK HEAD 检查准确的 Bucket/Key 和 Content-Length。默认最多核验 3 次，每次间隔 1 秒，单次 OSS 请求超时 10 秒。核验失败交给原有上传重试；耗尽后任务失败，本地 MP4 保留。ossutil 上传后端保持不变。

上传证据持久化在任务 payload 的 `_upload_diagnostics`，查询 API 的 `uploadDiagnostics` 会返回该记录，包括 Bucket、Key、本地路径、文件大小、工具脱敏结果、尝试次数、ETag、OSS 请求编号和核验时间。重启后仍可读取。ETag 不作为分片对象的 MD5 使用。

交付流程为：渲染 → 上传并校验 → 标记 `completed`、返回 `outputUrl` → 立即清理本地成品及该任务的临时工作目录。下游负责下载、保存和删除 OSS 中转文件。上传后的校验在标记完成之前执行；成功交付后的本地清理不再依赖 OSS 文件是否存在。清理失败只记录警告，不撤销任务成功状态。

不再设置 `OUTPUT_LOCAL_RETENTION_HOURS`，也不启动定时保留检查线程。历史任务仍按 `TASK_TTL_DAYS` 清理，不会被旧的本地保留字段阻塞。旧版本已经保留的本地文件不会在升级时批量删除；部署时应先根据任务记录确认交付成功，再单独清理这些旧副本。数据库过期清理本身不删除磁盘文件。

下载接口 `GET /tasks/{taskId}/download`：

- 已完成且有交付地址：直接返回 302，不再发起 OSS HEAD。本地 OSS 模式直接返回文件，文件已移除时返回 HTTP 404 / 3001。
- 下游已删除 OSS 文件时，跟随跳转会收到 OSS 的 404；再次下载应使用下游保存的副本。
- 未完成、任务不存在或没有交付地址时仍返回 HTTP 404 / 3001；公网 URL 配置校验保持不变。

查询接口中的 `status=completed` 表示渲染及上传校验成功，`outputUrl` 是当时的交付地址，不保证对象持续存在。下游删除对象不会改变任务状态或清空地址。移除顶层 `outputAvailable`、`outputVerifiedAt`、`outputCheckError` 及相关检查请求字段。`uploadDiagnostics` 中的校验时间、`outputAvailable` 等属于上传当时的历史证据，不是实时可用性。

移除 `POST /tasks/{taskId}/retry-upload` 和 3007–3009 检查/补传错误码。保留上传失败重试，以及服务重启时对未完成任务的上传阶段恢复：本地文件存在时续传，已登记文件丢失时任务失败，不自动重跑渲染。已经完成的任务不会因为下游消费而重新上传。

上传校验、STS 临时凭证支持、超时处理和日志凭证脱敏继续保留。OSS 文件不作为长期归档；任务记录和上传证据用于确认交付结果。

验收使用不被其他业务自动消费的独立任务，检查 API、上传校验、公网下载、ffprobe/AIGC 和本地成品清理。回归测试另行模拟下游立即删除 OSS 文件，确认成功状态、交付地址及本地清理不受影响。

## v5.38 构建与部署

完整构建可以沿用主 Dockerfile 并指定 `--build-arg IMAGE_VERSION=v5.38 -t videocut-wrapper:v5.38`。已有 v5.36 镜像时，此补丁也提供只替换四个 Python 文件的构建入口：

先在本地电脑的仓库目录构建（Windows PowerShell 或 WSL 均可）：

```sh
docker build -f deploy/Dockerfile.output-delivery-fix -t videocut-wrapper:v5.38 .
docker save -o videocut-wrapper_v5.38.tar videocut-wrapper:v5.38
```

然后将 tar 压缩为 `videocut-wrapper_v5.38.tar.gz`（例如在 WSL 仓库目录执行 `gzip -k videocut-wrapper_v5.38.tar`），由操作者手动上传到服务器的 `/data/images/`。导出的镜像包含基础层，服务器无需重新构建。

上传完成后，才在服务器执行：

```sh
docker load -i /data/images/videocut-wrapper_v5.38.tar.gz
cd /data/VideoCut-Wrapper
VIDEOCUT_ENV_FILE=/data/env/videocut.env \
VIDEOCUT_RUNTIME_ROOT=/data/videocut \
sh deploy/zero_downtime_deploy.sh videocut-wrapper:v5.38
```

`/data/env/videocut.env` 是服务器运行容器时注入的环境配置，不参与本地构建，也不放进镜像或上传包。已有配置正常时继续使用；路径配置应填写容器内 Linux 路径。服务器部署脚本、Compose、挂载的 `pipelines` 和 `fonts` 也应与此版本配套。

部署前核对旧实例的 `/srv/videocut/data`、`/srv/videocut/temp`、`/app/output` 挂载，并在部署用 `.env` 中持久设置 `VIDEOCUT_RUNTIME_ROOT`。不能只验证健康检查：健康的空数据库实例也可能使用了错误的数据目录。切换后再次检查挂载以及已知任务的查询结果。

验收脚本为 `api-test/verify_output_delivery.py`，在新容器中运行，使用其现有环境配置和 API_KEYS，不在命令行传密钥。`--source` 传已有视频路径，`--evidence-dir` 传持久目录。脚本截取 3 秒，提交 bgm-concat 真实渲染并校验交付；不会调用上游生成模型。`--task-id` 只复核已有任务，不另行生成。
