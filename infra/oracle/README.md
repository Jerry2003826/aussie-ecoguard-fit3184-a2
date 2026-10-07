# 在既有 Oracle 服务器部署第二云推理

本目录是待审查的部署模板；没有连接服务器，也没有创建 VM、负载均衡、公网 IP、磁盘、证书或其他云资源。Oracle 承担 Grounding DINO / SigLIP2 实际推理，AWS 保留主业务、Rekognition、Cognito 与 S3。Google IAM Workload Identity Federation 只负责 AWS 工作负载换取短期 Google 身份令牌，不承载本方案计算。现有服务器的既有账单与出站流量仍按其原配置计费。

需要根任务先取得既有服务器 SSH 地址、Ubuntu/Debian 版本、现有域名和 HTTPS 反向代理信息。没有可用的既有 HTTPS 入口时，停在本机服务检查；不要把 18080 绑定到公网，也不要覆盖已有网站或替换其证书。

## 交付边界

| 文件 | 用途 |
| --- | --- |
| `preflight.py` | 只读检查 RAM、磁盘、CPU 架构、Docker/Compose/systemd、端口及现有代理；不安装软件、不调用云 API |
| `compose.yaml` | 单个非 root 推理容器；`127.0.0.1:18080 → 8080`；6 GiB 内存、4 CPU 上限、1 GiB 临时内存盘 |
| `validate-config.py` | 离线检查 Oracle 鉴权配置、精确 S3 范围、manifest/taxonomy 与全部模型缓存 SHA-256 |
| `ecoguard-inference.service` | 使用既有 Docker 的 systemd 启停包装；每次启动先验证配置和模型文件 |
| `*.env.example` | 无凭证模板；必须填实际值后才能通过验证 |
| `nginx/*.example` | 合入已有 HTTPS `server {}` 的独立子路径，不设置监听器或证书 |

**当前兼容边界：**仓库 `services/inference/requirements.lock.txt` 是按 Linux x86_64 / Python 3.12 生成的锁；本模板只把 `linux/amd64` 视为可部署基线。Oracle Ampere `aarch64/arm64` 会被预检阻断，必须先完成原生 ARM 依赖锁、镜像和真实模型 smoke 验证，不能通过架构模拟或改一个平台参数视为已验收。

## 1. 先做既有主机预检

由根任务上传本目录后，在目标服务器运行（读取 Docker 信息可能需要管理员权限）：

```bash
python3 /opt/ecoguard/oracle/preflight.py
```

要求 `MemAvailable` 至少 8 GiB（不把 swap 算进去），模型缓存所在盘、构建暂存盘与 Docker 数据盘各至少 8 GiB 可用空间。8 GiB 是最低门槛：当前模型文件约 2.08 GiB，上传归档、解包、镜像构建和旧镜像回滚可能还需额外空间。磁盘路径尚不存在时检查最近存在的父目录。预检不自动清理任何文件。

还需 Python 3.10+、可访问的现有 Docker 引擎、Compose 2.24+、systemd，以及空闲的 `127.0.0.1:18080`。缺少工具时先由根任务评估已有服务和安装方式，本模板不自动安装。80/443 已占用通常意味着存在网站，应复用其代理；不要停掉它。

6 GiB 容器限制是初始保护配置，不是模型峰值内存已通过的证明。CPU 推理的最长视频采样、冷启动和并发还必须实际测量；资源不足或耗时过长时，保留失败证据，不自动扩大资源。

## 2. 上传并准备构建上下文和缓存

上传由根任务完成，只包含审查过的源码、锁定配置和模型缓存。不要上传 `.env`、本机凭证、Google 服务账号密钥、AWS 临时凭证、用户媒体、`.venv` 或整个工作目录。

固定主机路径：

```text
/opt/ecoguard/source/                  # 根任务提供的仓库构建上下文
/opt/ecoguard/oracle/                  # 本目录
/opt/ecoguard/config/model.locked.json
/opt/ecoguard/config/taxonomy.json
/var/lib/ecoguard/models/<adapter>/<revision>/<file>
/etc/ecoguard/compose.env
/etc/ecoguard/inference.env
```

先确认这些路径不是现有应用目录；若已经存在，应保留现有版本并审查后更新。模型与 taxonomy 必须和同一份锁定 manifest 对应。源 `models/` 的内容放入主机 `/var/lib/ecoguard/models/`，不要再多包一层 `models/`。缓存目录由容器 UID/GID `10001:10001` 读写，配置目录只读可遍历；修改 ownership 仅限本应用专用目录。

已有 `services/inference/Dockerfile` 从仓库根构建，镜像标签使用实际审查过的提交或构建 ID。以下是**待人工替换和审查的命令**，不由本模板自动执行：

```bash
cd /opt/ecoguard/source
docker build -f services/inference/Dockerfile -t ecoguard-inference:REVIEWED_BUILD_ID .
python3 /opt/ecoguard/oracle/preflight.py --image ecoguard-inference:REVIEWED_BUILD_ID
```

Compose 使用 `pull_policy: never`，不会悄悄从注册表拉取同名镜像。不要使用 `latest`。完整缓存通过哈希校验后，模型加载不需要 GCS 访问凭证；模型运行仍需向 Google/Cognito JWKS 和已允许的 S3 HTTPS 主机发出请求，不能禁用所有出站网络。

## 3. 填写身份与数据边界

复制两个 `.env.example` 到上述 `/etc/ecoguard/` 路径后，用编辑器填值；环境文件应属于 root 且权限为 `0600`。不要把环境文件当 shell 脚本 `source`。验证器仅支持不带引号或变量展开的字面量 `KEY=value`。

| 设置 | 必须对应的值 |
| --- | --- |
| `SERVICE_AUTH_MODE` | `oracle`；不能沿用 Cloud Run 跳过平台签名验证的模式 |
| `SERVICE_TOKEN_AUDIENCE` | 实際现有 HTTPS base URL，例如 `https://my-existing-domain.tld/ecoguard-inference`，无尾斜杠 |
| `SERVICE_TOKEN_EMAIL` | AWS WIF 被允许代用的**唯一** GCP invoker service-account email |
| `COGNITO_ISSUER` / `COGNITO_CLIENT_ID` | 该作业实际部署的 user pool issuer 与 app client |
| `SOURCE_S3_HOSTS` | 该作业媒体 bucket 的精确区域主机名，不能为通配符或整条 URL |
| `SOURCE_KEY_PREFIXES` | `derived/,queries/`，不接受原始媒体或任意其他 key |
| `MODEL_MANIFEST_SHA256` | 实际上传的 `model.locked.json` 文件 SHA-256；通过 `sha256sum` 计算 |

`MODEL_MANIFEST_PATH=/config/model.locked.json`、`TAXONOMY_PATH=/config/taxonomy.json`、`MODEL_CACHE_DIR=/models` 与 Compose 挂载一致。host 路径若变更，必须一并审查 Compose、unit 与验证命令，不要只改一个 env。

```bash
python3 /opt/ecoguard/oracle/validate-config.py \
  --env /etc/ecoguard/inference.env \
  --manifest /opt/ecoguard/config/model.locked.json \
  --taxonomy /opt/ecoguard/config/taxonomy.json \
  --cache /var/lib/ecoguard/models
docker compose --env-file /etc/ecoguard/compose.env \
  -f /opt/ecoguard/oracle/compose.yaml config --quiet
```

验证器不联网、不补下载模型、不会输出令牌。它验证全部缓存文件 SHA-256；只成功解析 Compose 不代表模型可运行。避免把完整的 `docker compose config` 输出保存或贴到聊天中，因为它会展开环境变量。

## 4. 启动本机服务并检查

确认环境与缓存检查通过后，审查并把 unit 安装为 `/etc/systemd/system/ecoguard-inference.service`。unit 使用固定 `/usr/bin/docker` 与 `/usr/bin/python3`，不支持未经调整的 rootless Docker。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ecoguard-inference.service
sudo systemctl status ecoguard-inference.service --no-pager
curl --fail --silent http://127.0.0.1:18080/healthz
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:18080/readyz
```

`healthz` / Docker healthy 只表示进程存活；首次模型 warmup 前 `readyz=503` 是预期状态。systemd 的 `active (exited)` 表示 Compose 包装执行完毕，不代表持续推理正常；同时看容器 health、OOM 状态和实际请求。缓存启动验证可能要几十秒。

## 5. 接入既有 HTTPS 反向代理

让根任务先读取并备份实际站点的相关配置。只有确认域名证书有效、没有同路径冲突、现有规则不会先跳转/拦截该路径之后，才合入 `nginx/ecoguard-inference.location.conf.example`。不要替换 `nginx.conf`、现有 `server {}`、默认站点、证书或 80/443 监听器；本目录不申请证书，也不开放防火墙端口。现有代理若为 Caddy/Apache，按同样的路由与头部规则修改其已有站点，不安装第二套代理。

模板把 `https://<domain>/ecoguard-inference/v1/*` 转到 `http://127.0.0.1:18080/v1/*`，仅转发 POST；外部 `healthz/readyz` 返回 404。专用既有子域名也可以使用根 base URL，但需同步调整 location/rewrite、AWS URL 与令牌 audience。先运行现有代理的配置检查（NGINX 为 `nginx -t`）成功后再 reload，不做 restart，也不停止已有业务。

必须原样保留两个不同用途的头部：

- `X-Serverless-Authorization: Bearer <完整的 Google 签名 ID token>`：Oracle 服务验证 Google 签名、issuer、有效期、精确 audience、精确 service-account email；只有 email 字段或自制 JWT 不算验证通过。
- `Authorization: Bearer <Cognito access token>`：`/v1/query-authorizations` 在 Oracle 独立验证 pool、client、`token_use=access` 与用户身份。不要替换成 ID token 或由前端直接访问私有推理接口。

模板将请求体限制在 1 MiB，关闭访问日志、响应缓冲落盘与上游自动重试，代理读取超时 390 秒对应应用 330 秒预算。不要把 authorization 头、query bearer、presigned S3 URL 或完整请求体写入日志；已有代理日志/debug 配置也要核对。业务 JSON 应引用有短期签名的 S3 来源，用户媒体仅在容器 `/tmp` 临时内存盘处理。

## 6. 跨云接线与必须保留的验收

由根任务配置 AWS 推理 URL 为完全相同的 HTTPS base，WIF 的 `generateIdToken` audience 同样使用此 base，并包含 invoker email。WIF 服务账号不需要 Oracle 上的 Google JSON key，也不需要本方案启用 Cloud Run。公网 HTTPS 能访问并不意味着私有接口允许匿名推理。

最低真实检查顺序：

1. 本机 `healthz=200`，公网不能直接连接 18080；代理外部 health 路径为 404。
2. 对 warmup 发送无 token、伪造 token、错误 audience/服务账号 token，确认被拒绝；不要只测格式错误请求，因为 422 不证明身份验证。
3. 用 AWS WIF 产生的有效短期 Google ID token 调用 `/v1/warmup`。成功后本机 `readyz=200`，模型版本与锁定 manifest 一致。令牌在内存或权限受限的临时文件中处理，不放进截图/命令历史/进程参数；测试完成删除临时 token 文件。
4. 同一有效服务令牌下，Cognito access token 正确时 `query-authorizations` 返回真实 `sub`；ID token、错 client、过期 token 都被拒绝。两个用户各用自己的 token 验证。
5. AWS → Oracle 的一个真实图片推理和一个 embedding 请求通过；对非法 S3 host、越界 key、到期签名 URL、SHA/媒体损坏保留明确失败结果。检查模型结果与 manifestVersion，而非 mock 标签。
6. 检查 `/tmp` 请求目录清理、容器 `OOMKilled`、CPU 延迟、重启后的缓存完整性。最大视频帧数与并发未测过时单独标为待验收，不能把一次 warmup 视为完整作业通过。

本目录的本地单元测试只覆盖离线部署门禁，不替代以上云验证。

## 7. 停止与回滚

`sudo systemctl stop ecoguard-inference.service` 只停止本应用 Compose 容器，保留模型与配置。不要执行全局 Docker prune、删除共享卷、停止现有 nginx 或删除 VM。回滚镜像时恢复此前保留的镜像标签、配套 manifest/taxonomy/env 和必要的模型缓存，先重新校验后启动。仅回滚本应用新增的 proxy location，配置检查通过再 reload 既有代理。

容器使用 `restart: unless-stopped`，主机重启后 Docker 也可恢复容器；正常变更应通过 unit 执行，以保留启动前的完整校验。停止服务不会停止既有 VM 账单。

参考：[Docker Compose 服务配置](https://docs.docker.com/reference/compose-file/services/)、[NGINX HTTP 代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。
