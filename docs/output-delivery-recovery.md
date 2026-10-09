# 输出交付与本地恢复

上传后使用 SDK HEAD 检查准确的 Bucket/Key 和 Content-Length。默认最多核验 3 次，每次间隔 1 秒，单次 OSS 请求超时 10 秒。核验失败交给原有上传重试；耗尽后任务失败，本地 MP4 保留。ossutil 上传后端保持不变。

上传证据持久化在任务 payload 的 `_upload_diagnostics`，查询 API 的 `uploadDiagnostics` 会返回该记录，包括 Bucket、Key、本地路径、文件大小、工具脱敏结果、尝试次数、ETag、OSS 请求编号和核验时间。重启后仍可读取。ETag 不作为分片对象的 MD5 使用。

`OUTPUT_LOCAL_RETENTION_HOURS` 默认 24，最小 1 小时。每 5 分钟扫描已完成任务，到期后重新 HEAD 并核对大小，成功才删除本地 MP4。失败任务和远端缺失/检查异常的任务继续保留；任务 TTL 清理不会删除仍有恢复路径的记录。字幕输出若位于临时目录内，保留整个工作目录，自动清理只删除登记的最终 MP4，不递归删除目录。需要监测磁盘用量；本策略不会为了释放空间提前删除尚处于保留期或未验证的恢复副本。

下载接口 `GET /tasks/{taskId}/download`：

- 对象存在且大小符合已知记录：302（本地 OSS 模式直接返回文件）。
- 对象缺失或大小变化：HTTP 410，`error_code=3007`，`details.code=TASK_OUTPUT_MISSING`。
- OSS 检查超时、权限等异常：HTTP 503，`error_code=3008`，`details.code=TASK_OUTPUT_CHECK_UNAVAILABLE`。
- 错误包含 taskId、服务请求编号 requestId，以及能够获取的 ossRequestId。

查询接口保留历史 `status=completed`，实时返回 `outputAvailable`（true/false/null）、`outputVerifiedAt`、`outputCheckError`。null 表示尚未完成或检查失败；不可用和未知状态不返回下载 URL。

`POST /tasks/{taskId}/retry-upload`（使用现有 X-Api-Key 鉴权）复用登记的本地 MP4，重新检查 AIGC 并上传，不调用生成模型或渲染 worker。失败或历史完成任务有本地副本时可以执行；无副本或任务已在处理时返回 HTTP 409 / 3009。服务重启也会优先恢复已登记的上传阶段；已登记文件若丢失则失败，不自动重跑渲染。

HEAD 与后续下载之间仍存在时间窗口，其他调用方可以删除对象。外部删除仍须通过 OSS 数据事件审计定位；此修复不恢复已经丢失的历史视频，也不会自动重做收费生成任务。

验收应分别验证生产 API、远端 HEAD、公网下载、ffprobe/AIGC、本地保留，以及重启或本地清理后的下载能力。

## v5.37 构建与部署

完整构建可以沿用主 Dockerfile 并指定 `--build-arg IMAGE_VERSION=v5.37 -t videocut-wrapper:v5.37`。已有 v5.36 镜像时，此补丁也提供只替换四个 Python 文件的构建入口：

```sh
docker build -f deploy/Dockerfile.output-delivery-fix -t videocut-wrapper:v5.37 .
VIDEOCUT_RUNTIME_ROOT=/data/videocut sh deploy/zero_downtime_deploy.sh videocut-wrapper:v5.37
```

部署前核对旧实例的 `/srv/videocut/data`、`/srv/videocut/temp`、`/app/output` 挂载，并在部署用 `.env` 中持久设置 `VIDEOCUT_RUNTIME_ROOT`。不能只验证健康检查：健康的空数据库实例也可能使用了错误的数据目录。切换后再次检查挂载以及已知任务的查询结果。

验收脚本为 `api-test/verify_output_delivery.py`，在新容器中运行，使用其现有环境配置和 API_KEYS，不在命令行传密钥。`--source` 传已有视频路径，`--evidence-dir` 传持久目录。脚本截取 3 秒，提交 bgm-concat 真实渲染并校验交付；不会调用上游生成模型。`--task-id` 只复核已有任务，不另行生成。
