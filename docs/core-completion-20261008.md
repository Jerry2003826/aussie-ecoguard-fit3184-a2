# A01–A06 核心交付与保留数据停用记录

本轮执行用户的“完成自己的核心部分、更新 GitHub、关闭云运行”要求。
用户明确选择保留数据与配置，接受少量存储额度消耗，额外自费 A$0 不变。
核心代码与本轮本机证据可交接；全作业签核仍受云接线与独立身份基准限制。

## A 的交付

| 任务 | 本轮结果 | 验收边界 |
|---|---|---|
| A01 契约与规则 | 权限、UTC 时间、稳定分页、AND／数量、状态、retryable 错误及查询截止时间统一 | 后端确定性检查通过；没有用本地身份替代真实 Cognito 验收 |
| A02 上传和处理 | PUT 600 秒、checksum／去重、续签先核查已上传对象、真实格式校验、缩略图、视频抽帧与模型写回 | 真实本机模型链通过；真实 AWS ingest 镜像和 Oracle 接收端仍缺，自动两云入库未通过 |
| A03 查询与素材 | 创建起 900 秒；续签／retry 不延期限；成功、永久失败、取消、超时回收；可恢复失败保留输入；评分发布前复核截止时间 | 真实本机链及期限／并发负例通过；Requests timeout 为连接／读取 inactivity 预算，硬模型期限由可终止 Worker 保证 |
| A04 寻宠 | 同版图片、文字、联合检索；仅公开且在日期范围；同 crop 双门槛；候选理由保留身份限制 | 小规模语义保留集通过预先冻结门槛；一个 bulldog 检索未命中；未完成同一宠物身份基准和所有入侵物种评估 |
| A05 管理 | 批量标签、人工物种抑制、幂等删除回执、generation 失效及补偿清理 | 确定性与真实本机数据链通过；云端既有存储和配置保留 |
| A06 发布交接 | 固定 v3 manifest／校准、真实离线检查器、可复算拟合器、当前 README、部署及停用说明 | 核心实现签核可交接；全作业验收不能勾满 |

还补齐了前端恢复：可恢复 FAILED 查询显示 Retry，调用原 job ID；永久失败、取消、清理、过期禁止重试。
GET 410 会移除过期查询缓存，恢复新建表单，不会让页面保持禁用。网络错误不会偷偷重新提交。

## 唯一测试计数

| 测试层 | 本轮通过 | 证据 |
|---|---:|---|
| 后端 | 130 | `tmp/core-completion-20261008/backend-final-guarded.log` |
| 前端 | 39 | `tmp/query-retry-20261008/frontend-final-tests-with-expiry.log`（实际路径亦在 verification.json） |
| 推理／鉴权 | 72 | `tmp/a04-model-quality-20261008/inference-final-tests.log`（服务证据有记录） |
| Oracle 部署门禁 | 8 | `tmp/core-completion-20261008/oracle-gates-final.log` |
| 停用检查器 | 10 | `tmp/core-completion-20261008/shutdown-checker-final.log` |
| 合计 | **259** | 不累计重复执行次数 |

TypeScript／Vite build、AWS 与 GCP Terraform validate 通过，改动的 Terraform 文件 fmt 检查通过。
既有未改动的 cors.tf 格式问题没有混入本轮功能变更。Oracle 门禁测试是离线配置测试，不是运行中的 Oracle 服务。
已发现的 same-crop 测试空通过也修正：补公共索引和正向对照，确认同一 crop 满足两种输入时能返回结果。
评分耗时越过截止时间的原失败证据另存；新增回归确认 FAILED／QUERY_EXPIRED、无 resultIds，并只清理查询源、保留库特征。

## 真实本机模型与质量

真实缓存模型运行的核心链路 **55/55**，105.891 秒：生产 API 分派／Service／Workers、校验与预处理、
真实 DINO／SigLIP2、缩略图反查、标签数量 AND、公共／私有边界、查询、标签管理、删除及素材清理。
最终素材对象 0、模型子进程已退出、所有请求临时目录清空。内存 operation/outbox 回执不是外部持久化数据。

该检查明确采用两名本地 Principal、MemoryRepository／MemoryStorage 和本地队列，入口 Rekognition 是空 fixture；
没有假写 READY／识别结果，也没有 AWS、Cognito、Rekognition、SNS 或双云成功声明。
图库与查询复用 COCO 双猫图及同源裁剪，**不是独立个体质量集**。
可复现命令见 README 与 `scripts/check_core_local.py`；输出目录必须全新。

另一次实际 v3 视频回归：120.0 秒，取 0/10/…/110 秒共 12 帧；推理 203.985 秒，整链 205.938 秒；
每帧与聚合均 cat=2，24 个真实 pet features，无截断，查询自动清理，子进程和目录退出。
这是静态双猫画面生成的视频，用来验证最长允许时长和框架预算；不证明复杂镜头、100 MB 文件或所有物种计数精度。
`scripts/check_video_local.py` 不调用云、不开 HTTP 服务、仅使用已验证模型缓存。

v3 修正独立 DINO prompt 的跨提示物种分数覆盖：保留候选框，SigLIP2 在同一裁剪上统一分类。
单一 animal prompt 曾漏掉第二只猫，失败方案没有发布；保留多提示定位后原双猫计数仍通过。
SigLIP2 显式 lowercase／64 token；真实同句大小写向量最大差 0。

检索校准只用 4 个图库＋4 个 calibration 查询及预先固定文字配对，共 40 对；
图片／文字分开拟合单调 sigmoid，各模态固定门槛 0.5，组合需双通过再取几何均值。
外层过滤仍 0.2；它不能绕过各模态 0.5。模型学得的图文 logits 未被当作图图评分或身份概率。
校准 source hash／拟合规则／参数在 manifest 与 `config/calibration/`；
`python scripts/fit_pet_calibration.py --check` 从 40 对数据复算，和 manifest 完全相同。

首次冻结后的独立图片 holdout：8/8 正确猫狗类别，8/8 可用 pet features；照片及联合各 Recall@1/@5=7/8。
一个 american_bulldog_54 被检索门槛过滤，仍留在分母与原结果，未按 holdout 改阈值。
原四条正向文字复用了校准模板，因此只算重放；另行冻结新表述后，补充文字 4/4，
未训练的无关文字 0/4 误报，新文字加原八图组合仍 7/8，不能将它说成新增八个独立图片。
这些是品种／物种语义标签，不是同一动物；不能推断身份、全部入侵物种或真实 Rekognition 混合链的准确率。

可提交的脱敏模型报告：`services/inference/evidence/model-quality-v3-20261008.json`。
原始图片、768 维向量、首轮失败和所有中间日志继续仅在忽略的 `tmp/a04-model-quality-20261008/`。
前轮 v2 猫判 fox／正确文字被过滤／无关文字组合问题的原证据没有覆盖。

当前 manifest `ecoguard-models-2026-10-08-v3` SHA-256：
`639595e1e9ffe18cb11ead1c7480ffe7a31bf767f6cbaf6b8375829227a569ae`。
旧 manifest 原字节归档，异版 features 不比较；生产 v3 不接 v2 任务。后续恢复需审查旧任务处理及旧图库重建。

## 云停用：已执行的范围

课程账号 `360493183467`，`default`，us-east-1。首先刷新课程临时凭证并用真实管理 API 核验；
只凭 STS 成功并不能排除 `voc-cancel-cred`。临时凭证直接写本机配置，不出现在聊天／Git。

停用计划为 **0 创建、14 原地更新、0 删除**；原 tfstate 私下备份，保留所有队列、表、桶、用户池、client、
Lambda、targets 和权限配置。没有 terraform destroy 或修改其他项目。
之后的最终代码更新计划只更新七个既有 Lambda 的 ZIP 和 v3 manifest 环境值，仍保留并发 0。

真实管理检查通过：

- outbox／cleanup 两条规则 DISABLED；infer／delete／notify 三个 SQS mapping Disabled。
- 七个 Lambda reserved concurrency=0；没有 provisioned concurrency、VPC 或 EFS 挂载。
- HTTP 默认 execute-api endpoint 禁用，真实没有 custom-domain mapping 绕过入口。
- 专用媒体桶增加数据层 Deny；GetBucketPolicy／PutBucketPolicy 保留恢复能力；没有 S3 通知目标。
- Cognito 禁止公共自注册；当前用户数 0，因此没有禁用／删除任何用户。
- 两个可用课程区域 EC2 全状态实例数 0。

旧签名链接核验只读取／写入本轮自己创建的 59 B probe，GET／PUT 均返回 403 AccessDenied，
未读取用户媒体，未覆盖原图。该小 probe 随保留数据一起留下，私有记录提供清理 key，不写入 Git。
初轮 PUT 被自动 Content-Type 导致 SignatureDoesNotMatch，不能算策略证明；按原签名空 Content-Type 修正后才获得 AccessDenied。
HTTP API 实际返回 404，结合 DisableExecuteApiEndpoint=true 及 mapping=0 确认入口关闭，不虚写 403。

Google 仅既有 IAM/WIF、账单未绑定；Oracle 无可用应用主机。没有新增或启动任何计算资源。
个人账号 MCP 的只读 Lex 清单为空，本轮没有创建正式 bot 或发邮件。
本轮最后结束 Learner Lab 并核验 Terminated／00:00；具体结束证据及最终代码包检查见本地汇总 JSON。

## 费用、恢复和成品剩余工作

保留数据的停用**不保证账单零**。当前 DDB 为 PAY_PER_REQUEST，ECR 0 镜像，日志计量 storedBytes=0、保留 7 天；
这些值和队列计数都有延迟。ingest 约有 2 条旧消息，未 purge，仍按原保留期到期。
S3 数据层已关闭，未继续 ListObjects；日统计缺失不能当作 0 B，存储／版本／日志等仍可能消耗额度。
默认配置持久保存 runtime_enabled=false；只读检查器和恢复说明见 `infra/aws/SHUTDOWN.md`。

B：接管其余界面、可用性和演示体验。
C：提供可用的既有 Oracle HTTPS 主机、ingest 镜像和云部署接线；按新授权和 A$0 约束恢复。
D：正式 Lex、邮件与验收协调；独立同宠／干扰宠物标注、至少 50 个唯一保留查询来源、所有必选物种和完整云链验收、演示及报告。
A 的核心实现本轮已交接，后续在这些真实依赖就绪后配合最终模型／链路验收。
