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

10 月 8 日的完整环境检查已验证真实 Cognito、两用户权限、S3 字节与签名、
校验和、去重及删除。CORS OPTIONS 和前端 POST 所需 Idempotency-Key 已部署
并复验，七个 Lambda 已统一更新为最终 v2 包。测试结果和仍失败的项目见
[本轮验收记录](docs/environment-acceptance-20261008.md)。课程会话到期后
`voc-cancel-cred` 禁止更新；STS 能返回身份不代表会话仍允许管理资源。
本轮结束时已关闭测试前端并结束学生 Lab，实际状态 Terminated / 00:00；
测试数据已清除。完整双云和寻宠质量尚未通过，核心任务仍按验收记录逐项签核。

第二计算云已按用户最新选择改为**现有甲骨文服务器**。Oracle HTTPS
推理服务模板和完整服务令牌验签已准备；真实服务器地址与 SSH 访问尚未
确认，因此不能声称 Oracle 已部署或双云链路已验收。

Google 项目 `ecoguard-fit3184-jlii0474` 仅配置免费 IAM/WIF 身份；账单未绑定，
没有 Cloud Run、模型存储或镜像仓库。AWS LabRole 换取短期 Google ID token，
Oracle 验证其签名、issuer、audience 和固定服务账号，并对查询独立验证
Cognito access token。实际 LabRole→Google WIF 交换、令牌验签和错误角色拒绝
已通过；Oracle 接收端仍未部署。[IAM 官方价格](https://cloud.google.com/iam/pricing)。

**额外自费上限为 A$0**。不创建新的 Oracle VM/负载均衡，不启用 Google
计算服务。AWS 主要使用课程额度。课程账号的 Lex 仍受限；个人 AWS
账号已完成临时 Lex Bot 的创建、构建、发布及正式槽位补测，累计八种场景
均有通过证据，包含同会话物种追问；初轮 7/8、修复后 2/2，测试资源已清理。
免费计划及 US$100 赠金已核实。正式查询接线尚未验收，见
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

本轮后端、推理/鉴权、Oracle 配置门禁及前端测试与构建通过，具体数量见验收记录。
真实本机模型在公开样本上检测出两只猫，生成真实 768 维单位向量。
4/24/120 秒视频抽帧与模型已实测；最大 12 帧冷启动 209.312 秒，临时文件清零。
当前单 worker 并发请求返回可重试的 MODEL_BUSY，重试成功。
此前失败样本证据已单独保留。这些结果不证明完整物种精度或真实寻宠准确率。

SNS 确认、真实通知 Lambda、邮件实收及重复/权限/标签/取消负例已验证；使用
独立通知 fixture，不能替代自动入库链路。真实 Edge 文件选择及 S3 上传也通过。
完整自动云处理、Oracle HTTPS及正式应用 Lex 仍需验收。照片与描述
联合排序已修复，但文字猫样本被当前阈值过滤，寻宠质量仍失败；少量样本不能
建立全部物种精度或真实目标云性能。不能将已有测试视作作业全部完成。

[四人产品实施与验收计划](docs/product-and-implementation-plan.md) ·
[部署流程](docs/deployment-runbook.md) ·
[云环境状态](docs/cloud-environment-status.md) ·
[22 项验收矩阵](docs/acceptance-matrix.md)

个人 AWS 账号的 CLI 登录、24 个 AWS 技能和 AWS MCP 已在本机配置并验证。
[Agent Toolkit 配置记录](docs/aws-agent-toolkit-setup.md) 包含 profile、区域、
启动方式和实际验证范围。
