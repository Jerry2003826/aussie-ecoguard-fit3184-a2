# Aussie EcoGuard — FIT3184 A2

澳大利亚动物目击管理、物种与数量检索、照片／文字寻宠、邮件订阅和 Lex
助手。项目已按四人分工安排后续接管、实施与验收，你负责核心业务与 AI
检索；[详细任务、交接与排期](docs/product-and-implementation-plan.md)。

## 当前环境

AWS us-east-1 已实际创建 Cognito、私有 S3、5 张 DynamoDB 表、4 个 SQS
工作队列及其 DLQ、ECR、7 个 Lambda 和受 JWT 保护的 HTTP API。

- API：`https://i4zicvj5s2.execute-api.us-east-1.amazonaws.com/api/v1`
- Cognito pool：`us-east-1_9nR2hwyVJ`
- Cognito client：`6io5inllom6a5l43038uhbluvi`
- 文件桶：`ecoguard3184-dev-360493183467-media`

最新结果见[10 月 8 日续验收记录](docs/acceptance-continuation-20261008.md)：
唯一自动测试 **184 项**通过（本轮执行 176，保留未变更的 Oracle 门禁 8）；
真实本机核心链路 52 项功能检查通过。新增 8 张独立 Oxford 图片的品种语义基线
仍失败，不能将测试通过视作真实寻宠质量或全作业通过。

本轮仅更新七个既有 Lambda 的代码，包 hash、v2 manifest、LabRole 和
Active/Successful 状态已核验；真实两用户检查 29/29、终态源对象恢复检查
29/29 通过，未发送邮件。上传仍停在 QUEUED，推理和正式 Lex 仍返回准确的
503，完整自动云处理未通过。测试数据已清除，本轮 Lab 在 Edge 确认
Terminated / 00:00；关闭前两个课程区域的 EC2 均无运行中或待启动实例。
[前轮记录](docs/environment-acceptance-20261008.md)原样保留。课程会话到期时的
`voc-cancel-cred` 限制也不能仅凭 STS 身份查询成功判断已恢复。

第二计算云已按用户最新选择改为**现有甲骨文服务器**。Oracle HTTPS
推理服务模板和完整服务令牌验签已准备；本轮再核查仍未发现 RUNNING/STOPPED
主机，因此不能声称 Oracle 已部署或双云链路已验收。Cognito JWKS 过期缓存
修复只通过代码与本地测试，尚未部署 Oracle 接收端。

Google 项目 `ecoguard-fit3184-jlii0474` 仅配置免费 IAM/WIF 身份；账单未绑定，
没有 Cloud Run、模型存储或镜像仓库。AWS LabRole 换取短期 Google ID token，
Oracle 验证其签名、issuer、audience 和固定服务账号，并对查询独立验证
Cognito access token。实际 LabRole→Google WIF 交换、令牌验签和错误角色拒绝
已通过；Oracle 接收端仍未部署。[IAM 官方价格](https://cloud.google.com/iam/pricing)。

**额外自费上限为 A$0**。不创建新的 Oracle VM/负载均衡，不启用 Google
计算服务。AWS 主要使用课程额度。课程账号的 Lex 仍受限；个人 AWS
账号已完成临时 Lex Bot 的创建、构建、发布及正式槽位补测，累计八种场景
均有通过证据，包含同会话物种追问；初轮 7/8、修复后 2/2，测试资源已清理。
免费计划及 US$100 赠金此前已核实。本轮个人 MCP 身份可用，Sydney/us-east-1
ListBots 均为空；正式应用 Bot/Alias 和查询接线仍未完成，见
[Lex 验证记录](docs/lex-resolution.md)。

## 手动 Lex

[完整 PowerShell 操作和接线](docs/manual-lex-setup.md)；
[真实 Lex V2 部署器](services/aws/tools/deploy_lex.py)。部署器默认离线计划，
显式 `--apply` 才创建意图、槽位、locale、version、alias 和限定 alias 的
Lambda invoke permission。不会修改课程 IAM。

## 本地启动

```powershell
Set-Location D:\3184a2
.\scripts\bootstrap-local.ps1
.\scripts\dev.ps1 api
# 另一终端
.\scripts\dev.ps1 web
```

前端默认使用真实 Cognito。要进入可见的开发模式，必须在
apps/web/.env.local 中明确设置 `VITE_DEV_MODE=true`，而且只能在 loopback
使用。内存适配器不模拟真实 AI、Cognito、SNS 或 Lex 成功。缺少服务时
会显示真实错误；开发模式不能作为云验收证据。

## 代码和配置

| 路径 | 内容 |
|---|---|
| apps/web | React / TypeScript 完整英文界面，官方 Cognito SDK |
| packages/domain | 权限、计数、标签、搜索等确定性规则 |
| services/aws | API、预处理、推理任务、删除、通知、outbox、清理、Lex |
| services/inference | Grounding DINO + SigLIP2、Cognito / Oracle 令牌验证 |
| infra/aws | AWS 资源及保护路由；课程角色权限限制见说明 |
| infra/gcp | 默认仅免费 IAM / WIF；计算部署默认关闭 |
| infra/oracle | 既有服务器部署模板，不创建付费基础设施 |
| config/model.locked.json | 固定模型版本、全部 artifact / taxonomy SHA-256 |
| docs | 原计划、部署边界、权限问题与操作说明 |

模型版本为 `ecoguard-models-2026-10-07-v2`。保持每个任务固定 manifest
版本；兼容模型更新由配置驱动。图片与文字特征仅在 featureVersion 一致
时比较。视频按每物种的帧内最大数量聚合，不能据此声称跨帧唯一动物数。

## 已有验证及限制

最新分项为后端 88、前端 29、推理/鉴权 59、保留 Oracle 门禁 8；前端构建和
AWS/GCP Terraform validate 通过。真实本机模型在此前公开样本上检测出两只猫，
生成真实 768 维单位向量；同源裁剪的本地链路检查不是独立个体准确率评估。
4/24/120 秒视频抽帧与模型已实测；最大 12 帧冷启动 209.312 秒，临时文件清零。
当前单 worker 并发请求返回可重试的 MODEL_BUSY，重试成功。
此前失败样本证据已单独保留。这些结果不证明完整物种精度或真实寻宠准确率。

SNS 确认、真实通知 Lambda、邮件实收及重复/权限/标签/取消负例已验证；使用
独立通知 fixture，不能替代自动入库链路。真实 Edge 文件选择及 S3 上传也通过。
完整自动云处理、Oracle HTTPS及正式应用 Lex 仍需验收。照片与描述
联合输入代码已修复，但正确文字仍被阈值过滤，无关 car 文字配同图仍返回候选。
新增 DINO-only 基线把 4 张猫全部判为 fox、未生成 pet features；图库不完整使
14 个语义查询保守记为失败。未调阈值掩盖失败，也未验收真实 Rekognition 混合
路径或宠物个体身份。浏览器上传签名 600/900 秒、query 15 分钟/3600 秒的
计划与实现差异已显式记录，仍待对齐。

[四人产品实施与验收计划](docs/product-and-implementation-plan.md) ·
[部署流程](docs/deployment-runbook.md) ·
[云环境状态](docs/cloud-environment-status.md) ·
[22 项验收矩阵](docs/acceptance-matrix.md)

个人 AWS 账号的 CLI 登录、24 个 AWS 技能和 AWS MCP 已在本机配置并验证。
[Agent Toolkit 配置记录](docs/aws-agent-toolkit-setup.md) 包含 profile、区域、
启动方式和实际验证范围。
