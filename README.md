# Aussie EcoGuard — FIT3184 A2

澳大利亚动物目击管理、物种与数量检索、照片／文字寻宠、邮件订阅和 Lex 助手。
四人分工中 A 负责核心业务和 AI 检索；[四人任务与交接计划](docs/product-and-implementation-plan.md)。

## 最新交付与运行状态

**核心实现已完成本轮交接；云端运行和访问已暂停，数据与配置保留。**
AWS 的定时任务、队列处理、七个 Lambda 和 API 已停用；媒体桶也拒绝包括旧签名链接在内的数据读写。
`infra/aws/deployment.auto.tfvars` 持久保存 `runtime_enabled=false`，普通 plan 不会默默恢复运行。
恢复服务需要新的授权，并继续遵守 **额外自费 A$0**。

[核心交付与停用记录](docs/core-completion-20261008.md)区分代码检查、真实本机模型和云验收。
本轮唯一自动测试 **259 项全部通过**：后端 130、前端 39、推理与鉴权 72、Oracle 门禁 8、停用检查器 10。
真实本机核心链路 **55/55**；120 秒视频按 0/10/…/110 秒取 12 帧，推理 **203.985 秒**，临时素材和模型进程均已清理。
这些本机检查使用真实缓存模型、内存存储和明确的空 Rekognition fixture，不代表真实双云链路通过。

模型 v3 的独立小样本验证：8 张未见图片的猫狗物种及特征 **8/8**；照片与照片加描述候选检索各 **7/8**；
独立改写文字 **4/4**，无关文字 **0/4 误报**。一个 bulldog 检索未命中仍保留在报告分母。
这是品种／物种语义候选基线，**不是宠物个体身份准确率或所有入侵物种精度验收**。
[模型报告](services/inference/evidence/model-quality-v3-20261008.json)保留原失败和后续验证的边界。

完整成品仍需接通现有 Oracle HTTPS 主机、AWS ingest 镜像工作器和正式应用 Lex，并以独立同宠／干扰宠物数据完成验收。
目前没有可用 Oracle 主机，正式 Lex Bot/Alias 未接线；不能把本轮核心交付描述为全作业完成。
此前[续验收失败记录](docs/acceptance-continuation-20261008.md)与[前轮云检查](docs/environment-acceptance-20261008.md)原样保留。

## 核心能力

- 权限、稳定分页、状态与错误契约；标签和数量条件使用 AND。
- checksum 去重、真实图片／视频校验、缩略图、10 秒抽帧、分类和模型写回。
- 浏览器上传签名 600 秒；临时查询从创建起共 900 秒，续签与重试不延长期限。
- 可恢复查询失败保留输入并提供 Retry；最多两次自动短重试。成功、永久失败、取消和过期会清理输入。
- 照片、描述和联合寻宠只比较同版特征；联合查询要求两种输入匹配同一个宠物裁剪，强照片不能盖过矛盾文字。
- 批量标签、人工物种标签抑制、重复删除回执、迟到结果失效、对象及索引一致性。

## 本地启动

```powershell
Set-Location D:\3184a2
.\scripts\bootstrap-local.ps1
.\scripts\dev.ps1 api
# 另一终端
.\scripts\dev.ps1 web
```

前端默认使用真实 Cognito。要进入可见开发模式，在 `apps/web/.env.local` 明确设置
`VITE_DEV_MODE=true`，仅允许 loopback。开发 API 使用内存数据，缺少真实服务会明确报错；
不会模拟 AI、Cognito、SNS 或 Lex 成功。暂停期间云端登录／API／S3 不可用于在线演示。

## 检查与真实本机复现

```powershell
.\scripts\verify.ps1
.\services\inference\.venv\Scripts\python.exe -m pytest services/inference/tests -q
.\.venv\Scripts\python.exe -m unittest discover -s infra/oracle/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s infra/aws/tests -v
.\.venv\Scripts\python.exe scripts/fit_pet_calibration.py --check
```

真实模型离线检查需要已验证的约 2.08 GiB 模型缓存，见[推理配置](services/inference/ENVIRONMENT.md)。
固定 COCO 双猫 PNG 来自[公开文档素材](https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/coco_sample.png)，
保存在忽略的 `tmp/model-smoke/coco_sample.png`；脚本验证其 SHA-256，不自动下载或更换样本。
复现输出目录必须全新，避免覆盖失败证据：

```powershell
.\services\inference\.venv\Scripts\python.exe scripts/check_core_local.py `
  --sample tmp/model-smoke/coco_sample.png --output-dir tmp/my-core-run
.\services\inference\.venv\Scripts\python.exe scripts/check_video_local.py `
  --sample tmp/model-smoke/coco_sample.png --output-dir tmp/my-video-run
```

视频检查使用本机已安装的 Scoop FFmpeg/FFprobe。两只本地身份、内存队列及空入口识别 fixture
均在报告显式标注；没有邮件发送、云操作或真实宠物身份结论。
`scripts/evaluate_pet_quality.py` 可验证来源哈希、划分和真实预计算特征，使用生产 Worker 评分，
不会加载模型或把合成向量当作 AI 证据。真实身份基准仍需人工标签和至少 50 个独立保留查询来源。

## 模型与评分冻结

当前 manifest 为 `ecoguard-models-2026-10-08-v3`，SHA-256：
`639595e1e9ffe18cb11ead1c7480ffe7a31bf767f6cbaf6b8375829227a569ae`。
Grounding DINO 提供候选框，SigLIP2 在同一裁剪上统一比较物种，避免跨提示分数覆盖标签；
文字显式小写并按 64 token 处理。图片和文字分别使用仅校准集拟合的单调评分，
各自门槛固定 0.5，联合分数为两者几何均值。分数是候选排序依据，不是身份概率。
40 对公开语义校准数据及拟合器可复算；holdout 未参与拟合或阈值选择。

旧 v2 manifest 按原字节归档在 `config/manifests/`。任务固定 manifest，v3 不接 v2 任务，
异版向量不比较；旧媒体不能自动视为新索引，恢复时需审查重建方案。
视频计数是每物种的帧内最大数量，不是跨帧唯一动物数。

## 云环境与费用

| 云 | 当前项目状态 |
|---|---|
| AWS 课程账号 `360493183467` / `default` / us-east-1 | Cognito、私有 S3、5 张按请求计费 DynamoDB 表、4 个 SQS 工作队列及 DLQ、ECR、7 个 Lambda、HTTP API 保留；运行和访问暂停 |
| AWS 个人账号 `011154879765` / `ecoguard-personal` | Toolkit/MCP 已验证；Sydney/us-east-1 无 Lex Bot；当前项目无新部署 |
| Google `ecoguard-fit3184-jlii0474` | IAM/WIF 身份接线，账单未绑定，无计算部署 |
| Oracle | 既有服务器部署模板已准备；当前未找到可用主机，未部署应用 |

课程 API `https://i4zicvj5s2.execute-api.us-east-1.amazonaws.com/api/v1` 当前关闭。
Cognito pool 为 `us-east-1_9nR2hwyVJ`，client 为 `6io5inllom6a5l43038uhbluvi`；
这些是公开配置，不是凭证。AWS LabRole→Google WIF 和令牌验签曾实际通过；
Oracle 接收端、完整自动入库和正式 Lex 仍待验收。SNS 确认及唯一授权测试邮件已实际实收，本轮没有再发邮件。

用户选择保留云端数据和配置，并接受少量存储额度消耗。**暂停不保证账单为零**：
S3、DynamoDB、日志等保留数据仍可能占用额度；队列消息、TTL 和日志保留期仍继续。
不创建新 VM、负载均衡或 Google 计算，不启动已停止服务器，不处理其他项目。
[停用、检查及恢复说明](infra/aws/SHUTDOWN.md)。密钥、登录缓存、`.env`、原图／向量、Terraform state 均不入 Git。

## 代码与队友接管

| 路径 | 内容 |
|---|---|
| `apps/web` | React / TypeScript 英文界面、Cognito SDK、查询重试与过期恢复 |
| `packages/domain` | 权限、数量、标签、AND 与时间规则 |
| `services/aws` | API、预处理、推理任务、删除、通知、outbox、清理、Lex |
| `services/inference` | DINO / SigLIP2、可终止模型 Worker、服务令牌与 Cognito 验签 |
| `infra/aws` / `infra/gcp` / `infra/oracle` | 原 Terraform 基础设施、IAM/WIF、既有 Oracle 主机模板 |
| `config` / `scripts` | 固定模型与校准、可复算评估、本地和停用检查器 |

B 接管界面与用户体验；C 接管既有 Oracle、ingest 镜像和云部署；D 接管正式 Lex、邮件、独立数据、完整验收、演示与报告。
A 的核心交接详见[最新记录](docs/core-completion-20261008.md)，完整验收与贡献边界仍按[四人计划](docs/product-and-implementation-plan.md)。

[部署流程](docs/deployment-runbook.md) · [22 项验收矩阵](docs/acceptance-matrix.md) ·
[手动 Lex](docs/manual-lex-setup.md) · [Toolkit 配置](docs/aws-agent-toolkit-setup.md)
