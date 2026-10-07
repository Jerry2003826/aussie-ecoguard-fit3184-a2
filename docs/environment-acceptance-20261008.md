# 环境与功能实测记录：2026 年 10 月 8 日

本轮按“全部给我测试完”执行本地、浏览器、AWS、GCP 身份和 Oracle 检查。
**整体尚未通过完整云端验收。** 本地通过、真实云接口通过、业务受阻及模型质量
失败分别记录，不能把明确报错的测试通过当成该业务已经可用。

## 已验证的结果

| 范围 | 实际证据 | 结论 |
| --- | --- | --- |
| 后端 | 47 项 unittest，包括组合输入、版本拒绝、删除竞态和清理、Lex 部署及跨端契约 | 通过；包含受控适配器，不能替代云端模型 |
| 推理与鉴权 | 53 项 pytest | 通过；包括签名、身份、过期和进程取消的边界 |
| Oracle 配置门禁 | 8 项 unittest | 通过；并未部署 Oracle 主机 |
| 前端 | 29 项测试、正式 build，Edge 390px 布局与键盘操作；真实浏览器选择/上传 | 通过；实际 S3 3142 字节与 SHA-256 匹配，上传任务 QUEUED |
| 真模型 | 15 个缓存文件尺寸与 SHA-256、真实 DINO/SigLIP2 猫样本 | 检出两只猫及 768 维向量；不是完整准确率评估 |
| 真视频 | FFmpeg 9.0.2，4/24/120 秒视频，真实本地模型 | 最大 12 帧 `[0,10,...,110]`；120 秒样本推理 209.312 秒，聚合仍是 2；临时素材与对象清零 |
| 实际单 worker 背压 | 两个独立图片请求同时提交真实 ASGI/模型 worker | 一个 200、另一个可重试 429 MODEL_BUSY；重试 200，支持一个同时执行任务 |
| 真 AWS | 两个临时 Cognito 用户、签名上传真实字节及真实删除 Lambda | 权限、校验和、去重、排队、删除及失败边界通过 |
| 真实中断恢复 | 现有 cleanup Lambda、唯一测试 producer/ARTIFACTS 账本与 S3 对象，过期租约 | 19 项检查通过；Lambda 隔离中断任务并删除账本/对象，finally 核验记录/对象/版本清零 |
| Rekognition | 课程账号 `us-east-1` 的真实 DetectLabels | Cat 置信度 97.44%，两个 instance |
| Google WIF | 短期测试 Lambda 使用真实 LabRole 签名并交换令牌 | Google ID token 签名、audience、有效期及固定 SA 身份通过；错误 audience 被拒绝 |
| WIF 拒绝路径 | 课程账号的 voclabs 角色向实际 Google STS 交换 | `unauthorized_client`，由 attribute condition 拒绝 |
| 最终部署一致性 | 七个现有 Lambda 均 Active/Successful，代码 hash 与最终 ZIP 一致 | manifest 全部为 `ecoguard-models-2026-10-07-v2` |
| 云配置 | AWS 59 次只读检查、GCP 9 次检查、全部可访问 OCI 范围交叉核查 | AWS 基础设施及 WIF 正常；实际推理计算环境缺失 |
| 个人 Lex 正式 NLU | 初轮 7/8，修复后同会话追问与回答 2/2；累计八种场景有通过证据 | 10 次实际 RecognizeText；不代表应用 fulfillment 完成 |

最终 `scripts/verify.ps1` 退出 0，AWS/GCP Terraform 校验及前端构建通过。
唯一自动测试合计 **137 = 后端 47 + 前端 29 + 推理 53 + Oracle 门禁 8**；重复
运行、构建与云探测不重复计数。最终核心包部署后 AWS 两用户边界脚本 29 项检查全部通过，
Cognito 密码挑战/CORS 脚本 6 项检查全部通过。

AWS 两用户检查包含匿名/无效/ID token 401、合法 access token 200、允许 origin
预检成功、不允许 origin 无 CORS 授权、他人私有媒体/任务/编辑/删除/聊天 404、
签名 checksum 篡改被拒、实际 PUT 与存储 checksum 一致、匿名 S3 GET 403、
本人内容去重、上传完成进入 QUEUED、未完成模型时拒绝改标签、未验证邮箱拒绝
订阅、缺推理/Lex 返回准确 503，以及实际删除文件和记录。测试未植入假 READY
模型结果；上传进入 QUEUED 不代表自动识别完成。

临时密码强制改密的真实 Cognito 检查也已通过。用户指定自己的 QQ 测试邮箱后，
实际 SignUp 邮件已收到并完成 ConfirmSignUp，email_verified=true。应用订阅
POST 返回 201 / PENDING_CONFIRMATION，用户点击确认后变为 ACTIVE。真实通知
Lambda 已向唯一授权邮箱发送一封 matching sighting 通知，用户确认实收。
同一事件重复、他人私有素材、不匹配标签，以及应用 API 取消后均未产生新的
发送账本。成功发送账本状态 SENT/messageId 不变，六项组件检查通过。

通知使用明确标注的独立 READY 测试 fixture；没有伪造自动入库结果，也不据此
验收“上传自动识别→通知”的完整链路。该链路仍依赖实际推理部署。

用户临时开启 Edge 扩展的 file URL 权限后，真实 file chooser 选取专用图片，
UI 提交实际课程 API/S3，图库出现私有记录和 QUEUED 状态。直接核验 S3 内容
与源文件完全一致、尺寸及 SHA-256 一致。这通过了浏览器选择与上传步骤；
自动识别仍未部署，不能将 Processing/QUEUED 标为业务完成。已提醒用户关闭
临时权限；浏览器工具禁止操作扩展设置，恢复权限需要用户手动完成。

## 修复并复测

1. 浏览器预检被 JWT 路由拦截：部署独立 `OPTIONS /api/v1/{proxy+}`，业务路由
   继续要求 JWT；实际浏览器还发现遗漏 Idempotency-Key 请求头，已补齐 API CORS
   并对前端真实 POST 的三项请求头复测。
2. 照片和文字同时输入时文字被忽略：在同一候选 crop 上联合图片和文字 cosine，
   分数为两项平均值；纯图、纯文语义保持，使用同一个处理 deadline。
3. 文本嵌入返回的 manifest 未被验证：严格匹配任务 manifest，组合输入还要求
   featureVersion 一致；不兼容时失败并清理临时素材。
4. 可选描述全为空格时误发空 embedding 请求：验证后 strip，按纯图片处理。
5. 前一次查询完成、后一次请求失败后输入被永久禁用：清除已完成的旧 query 状态，
   回归先复现失败、修复后通过；输入保留并允许重试。
6. 真实 Lex CreateSlotType 拒绝 `TOP_RESOLUTION`：改为官方 API 的 `TopResolution`；
   与 SDK 实际枚举核对的回归通过，原始失败与清理证据保留。
7. 真实 Lex CreateIntent 拒绝保留名称 `HelpIntent`：自定义帮助意图改为
   `EcoGuardHelp`，帮助 utterances 和业务功能保持；默认 fallback 使用既有意图。
8. 真实 Lex 拒绝 built-in fallback 的空 sampleUtterances/slotPriorities：省略
   这些字段；Build 要求 optional 槽位也列入优先级，四个槽位配置 1–4，只有
   species 必填。初轮“show me”落入 fallback，添加无槽位入口后真实连续追问通过。
9. SDK 会话存在但用户属性读取失败时，前端停在残留登录状态：清空本地认证状态
   并 sign out，允许重新登录；两项回归通过。
10. 推理在存储复制后、数据库发布前与删除发生竞态，未登记的归档对象可能遗留：
    产物按 generation/attempt 隔离，先登记持久清理账本，成功发布同时移除账本，
    失败只清理本次未被引用的产物；清理可在进程中断后继续，不删除新 attempt。
    11 项受控竞态/恢复回归通过；单项清理失败保留账本并继续后续任务，明确返回
    failed 计数。ingest/delete 租约 150 秒覆盖 120 秒 Lambda，infer 600 覆盖 480。
11. 批量删除部分失败时成功任务进度丢失、重试再次提交已删除中的文件：保留所有
    已接受任务，并仅重试可识别的失败子集；重试断网也保留状态。
12. 后端真实返回 matchScore/matchReason，但前端只读取 score/reasons：兼容真实
    字段，保留旧别名；理由区分图片/文字/联合，显示小数相似度而非身份概率。
    回归直接读取 Python job_view 的实际响应后渲染，未重新捏造前端响应样例。

七个现有 Lambda 的包与 v2 manifest 已更新，没有更改 IAM 角色、拓宽跨云
信任或创建新的常驻计算服务器。最后一次 Terraform plan/apply 是 0 新增、
7 更新、0 删除；逐项审核只改变 source_code_hash/last_modified，包含上述
删除竞态及跨端寻宠契约修复。实际七个函数均 Active/Successful、LabRole 不变，
CodeSha256 与本地 ZIP 一致。最终 ZIP SHA-256：
`c21e1e6e1ad1a680b40ecc86919d1600d037ded791a4798ee7e0abba8ef26d0b`。

部署后另外用唯一、独立的测试 producer、ARTIFACTS 清理账本与 S3 对象验证
实际恢复：过期租约被确认，现有 cleanup Lambda 将中断 producer 标为 FAILED，
清除账本和对象，返回有效计数且 failed=0。19 项检查通过，finally 记录/对象/
版本均为空；没有创建新 Lambda、发送邮件或伪造 READY 入库结果。这补充了
真实存储/数据库/清理 worker 证据，但不替代完整云模型与删除竞态的端到端验收。

## 尚未通过的项目

| 项目 | 真实结果 | 完成所需条件 |
| --- | --- | --- |
| Oracle 实际推理 | IAM 已确认当前就是学生 `jlii0474` 账号；三个配置指向同一 tenancy，Melbourne 无 RUNNING/STOPPED VM，OKE 节点池为 0。历史 SSH 超时，旧 LB 无后端且无 HTTPS | 提供可用现有服务器，或明确另行授权预算内新主机；不得用付费 OKE 顶替 |
| 自动云上传/模型写回 | ECR 没有 worker image，ingest Lambda 不存在；infer mapping Disabled，inference URL 和 invoker 配置为空；真实上传停在 QUEUED | 在预算内获得实际主机及 HTTPS，构建/部署 worker，再验证事件到模型、索引及检索 |
| Oracle HTTPS 身份鉴权 | Google 令牌生成和拒绝路径已真实验证；没有 Oracle 端点可接收 | 在实际主机验证允许及拒绝的服务和用户身份，不以本地 JWT 测试替代 |
| 文字寻宠质量 | 正确猫描述最高 cosine `0.11217`，低于当前 `0.2` 门槛；当前样本会被过滤 | 使用独立正负样本进行模态阈值/排序校准；不能用该单一样本调到“通过” |
| 组合寻宠质量 | 正确描述影响分数，但同一照片配无关 car 描述仍可达 `0.47455` | 进一步评估联合输入阈值与假阳性；平均分不等于身份概率 |
| 正式应用 Lex | 课程 ListBots 仍 AccessDenied；课程 API 的 Bot/Alias IDs 为空，聊天真实返回 LEX_UNAVAILABLE | 明确个人 Lex 的 runtime 调用身份、区域、fulfillment 和数据路径，完成应用对话 |
| 自动识别后触发邮件 | Cognito/SNS 确认、真实通知 Lambda 发送及用户实收、重复/ACL/标签/取消负例已通过；输入为受控通知 fixture | 实际自动识别完成事件与通知联调，不能用组件通过替代入库链路 |
| 完整样本精度及云端性能 | 当前少量猫样本/重复帧；120 秒视频及单 worker 背压实测通过，缺实际目标云和全部物种精度集 | 独立标注样本及真实目标环境，测精度、冷暖启动和云端并发 |

正式 NLU 测试经过真实 create/build/version/alias，覆盖物种、数量、颜色、日期、
同义词、帮助、fallback 和缺物种追问。保留初轮 7/8 失败，修复后只重跑缺物种
同会话两步 2/2，不能声称最终 bot 重新运行全部八例。所有临时 bot/alias/角色
均已清理；使用静态 closing response，不将 NLU 当作数据库查询 fulfillment。
团队材料、四人演示和各人的独立报告属于交付验收，尚未完成。

## 原始证据与复跑

脱敏 JSON、日志和截图保存在 `tmp/environment-acceptance-20261008/`，该目录
不提交 Git。主要文件：`local-backend-inference.json`、`frontend.json`、
`aws-baseline.json`、`aws-two-user.json`、`aws-final-packages.json`、`rekognition.json`、
`wif.json`、`wif-rejected-role.json`、`oracle.json`、`aws-inventory.json`、
`gcp-inventory.json`、`local-model-extended.json`、`mail.json`、`final-verify.json`、
`lex-formal-nlu-summary.json`、`aws-two-user-final.json`、`aws-cognito-final.json`、
`final-core-verify.json`、`a05-delete-race-fix.json`、`pet-result-contract-fix.json`、
`browser-upload-final.json`、`aws-core-final-packages.json`、
`final-core-plan-review.json`、`aws-two-user-core-final.json`、
`artifacts-cloud-probe.json`。
先前失败/修复前日志保留，避免覆盖原始失败事实。

首次两用户脚本把删除后预期误写成 media tombstone / 原任务 CANCELLED；实际
契约是 media 记录移除、原任务 DELETED。已按明确的数据删除契约更正断言，
保留 `aws-two-user-first-run.json`，复测通过；这不是产品缺陷修复。

复跑真实 AWS 边界测试会创建并清理两个专用测试用户及其数据，禁止用真实团队
账号替换这些保留域名测试身份。先恢复课程会话、确认课程账号与额度，再运行：

```powershell
$env:PYTHONPATH = 'packages/domain;services/aws'
.venv/Scripts/python.exe scripts/check-aws-environment.py `
  --profile default --account 360493183467 --region us-east-1 `
  --api-url https://i4zicvj5s2.execute-api.us-east-1.amazonaws.com `
  --pool-id us-east-1_9nR2hwyVJ --client-id 6io5inllom6a5l43038uhbluvi `
  --sample tmp/model-smoke/coco_sample.png `
  --output tmp/environment-acceptance-20261008/aws-two-user.json
```

本轮没有升级账单计划，也没有启动付费 Oracle 节点或 Google 计算服务。
个人 FREE/ACTIVE 计划检查显示 US$100 赠金，课程页面显示已用 $0 / $50；这些
余额有刷新延迟，不能据此声称实际用量为零。Google billingEnabled=false。
短期 WIF Lambda、其日志组及 ZIP 均已删除，七个产品 Lambda 的最终包已核验。

本轮收尾：邮件测试账户已退出并删除，唯一通知 fixture、发送账本、SNS topic/
订阅及临时密码文件均清除；浏览器上传和两用户 API 测试数据也已清除。
独立 ARTIFACTS 恢复测试的 producer、ledger、对象及版本 finally 核验均为空。
测试前端进程已停止，5173 无监听，专用应用标签页已关闭。最后通过 Edge 结束
课程 Lab，并实际读取到 **AWS Status: Terminated / Lab terminated / 00:00**。
`lab-ended-final.png` 保留结束证明。没有为了测试启动常驻云服务器，也没有
操作个人 AWS 控制台。临时 file URL 权限已提醒关闭，但尚未由用户确认关闭，
不能将扩展权限恢复写为已验证。

## 你的核心功能接管顺序

用户后续要求测试无问题后完成 A 的部分。A01 契约、A02 上传/处理、A03 查询和
临时素材、A04 寻宠评估、A05 管理一致性、A06 发布签核均有现有实现，本轮已
直接补齐确认的契约、联合输入及删除缺陷。仍不能把 A 全部签为完成：A02/A03
需要实际 Oracle HTTPS 主机与自动 worker 部署，A04 需要独立正负样本校准。

后续 A04 应建立有来源/许可、SHA-256、个体标识、人工描述和正例 ID 的评估
manifest，按个体及拍摄序列拆校准与保留集，排除本轮反复使用的 COCO 猫与近重复。
分别评估图片、文字和联合输入，记录 Recall@1/@5、无匹配假阳性及所有失败分母。
只在校准集选择模态阈值/融合政策，固定版本后评估保留集；当前没有这种独立集，
不能通过降低 0.2 让现有猫样本过关来关闭质量失败。

[22 项评分签核矩阵](acceptance-matrix.md) · [实施计划](product-and-implementation-plan.md)
