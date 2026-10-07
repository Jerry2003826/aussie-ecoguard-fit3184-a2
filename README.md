# Aussie EcoGuard — FIT3184 A2

澳大利亚动物目击管理、物种与数量检索、照片／文字寻宠、邮件订阅和 Lex
助手。用户当前要求先完成云环境配置，再停止扩展开发。

## 当前环境

AWS us-east-1 已实际创建 Cognito、私有 S3、5 张 DynamoDB 表、4 个 SQS
工作队列及其 DLQ、ECR、7 个 Lambda 和受 JWT 保护的 HTTP API。

- API：`https://i4zicvj5s2.execute-api.us-east-1.amazonaws.com/api/v1`
- Cognito pool：`us-east-1_9nR2hwyVJ`
- Cognito client：`6io5inllom6a5l43038uhbluvi`
- 文件桶：`ecoguard3184-dev-360493183467-media`

实际检查已验证匿名 401、临时密码强制修改、access token 可访问、ID token
不可访问；测试用户已删除，未发送邮件。CORS preflight 修复已写入代码，
尚需在重新启动到期 Learner Lab 后应用并复验。课程会话到期后
`voc-cancel-cred` 禁止更新；STS 能返回身份不代表会话仍允许管理资源。

第二计算云已按用户最新选择改为**现有甲骨文服务器**。Oracle HTTPS
推理服务模板和完整服务令牌验签已准备；真实服务器地址与 SSH 访问尚未
确认，因此不能声称 Oracle 已部署或双云链路已验收。

Google 项目 `ecoguard-fit3184-jlii0474` 仅配置免费 IAM/WIF 身份；账单未绑定，
没有 Cloud Run、模型存储或镜像仓库。AWS LabRole 换取短期 Google ID token，
Oracle 验证其签名、issuer、audience 和固定服务账号，并对查询独立验证
Cognito access token。[IAM 官方价格](https://cloud.google.com/iam/pricing)。

**额外自费上限为 A$0**。不创建新的 Oracle VM/负载均衡，不启用 Google
计算服务。AWS 使用课程额度。Lex 在当前账号仍被拒绝；手动控制台也受
同一 IAM 约束，需已有可用账号或教学团队开放权限。

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

后端 24 项测试、推理/鉴权 53 项测试、前端 18 项测试与构建通过。
真实本机模型 smoke 在官方样本上检测出两只猫，生成真实 768 维单位向量。
此前失败样本证据已单独保留。这些结果不证明完整物种精度或真实寻宠准确率。

完整云上传链路、Oracle HTTPS/WIF、Rekognition、SNS 确认邮件及 Lex 对话
仍须实际部署后验证。12 帧冷启动可能超过当前 240 秒推理预算；照片和文字
同时输入的融合检索尚未验收。不能将已有本地测试视作作业全部完成。

[原产品与实施计划](docs/product-and-implementation-plan.md) ·
[部署流程](docs/deployment-runbook.md) ·
[云环境状态](docs/cloud-environment-status.md)
