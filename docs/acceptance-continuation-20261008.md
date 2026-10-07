# 核心功能续验收：2026 年 10 月 8 日

**本地功能与回归通过；模型检索质量、实际 Oracle 部署和完整作业验收仍未通过。**
本报告接续[前轮环境与功能记录](environment-acceptance-20261008.md)，保留前轮原文、
失败日志和已验证证据。旧报告中的测试数量、ZIP 和 Lab 结束状态属于前轮快照；
本轮最新云端部署复测与 Lab 收尾见下方“云端状态与收尾”。

## 本轮结果与计数

| 范围 | 结果 | 证据与边界 |
| --- | --- | --- |
| 后端 | 88/88 | 原业务/领域 35、Lex 部署 7、寻宠响应契约 5、日期 11、源对象恢复 11、离线质量评估器 19；包含明确 fixture |
| 前端 | 29/29、TypeScript/Vite build 通过 | 7 个测试文件；未将此前浏览器证据重复计算 |
| 推理与鉴权 | 59/59 | 原 53 项加 Cognito JWKS 缓存回归 6 项；已有 Starlette/httpx 弃用提示不影响通过 |
| Oracle 配置门禁 | 保留此前 8/8 | 校验器及测试未变，本轮没有重复运行，也未部署主机 |
| Terraform | AWS/GCP validate 通过 | 静态配置检查，不等于创建资源或实际部署完成 |
| 真实本机核心链路 | 52/52 功能检查 | 真模型、内存数据库/对象存储；不包含真实 AWS 入口模型和双云身份 |
| 8 张独立公开图片质量基线 | 失败 | 14 个 semantic-only 查询，`status=feature_failures`、退出码 1；未调阈值掩盖失败 |

唯一自动测试总数为 **184 = 后端 88 + 前端 29 + 推理 59 + Oracle 8**。
其中 **176 项本轮执行，8 项沿用此前未变更的 Oracle 门禁结果**。专项重跑、
52 个真实本地功能检查、模型调用、构建和云探测均不另加到 184 项中。
`scripts/verify.ps1` 与本轮完整 inference pytest 均退出 0。

## 真实本机核心链路

`local_core_e2e.py` 使用现有固定模型缓存，单实例顺序执行 Grounding DINO/SigLIP2。
真实字节上传后经过生产预处理、Outbox/worker、模型推理、归档和索引，未手写
READY 状态或伪造模型输出。52 个检查覆盖：

- checksum、同用户改名去重、重复事件不重复推理，以及公开/私有可见性。
- 数量和标签 AND 查询、分页、真实 JPEG 缩略图、缩略图反查原文件字节。
- 临时文件查询、图片/文字/组合寻宠、结果契约，以及查询不入库、不发通知。
- 人工加标签、无变化操作、移除/恢复标签与数量/索引同步。
- 查询取消、无效图片失败、库记录删除，以及原图、帧、缩略图和特征清理。

运行 130.969 秒，6 次 infer、4 次 embed；实际处理 6 帧、11 个 crop embedding。
模型子进程和请求临时目录已退出/清除；最终媒体记录、索引和对象为 0。内存中仍有
13 个任务/结果记录及 24 个 outbox 记录，不能写成所有表清空。通知事件仅保留供
检查，没有发送邮件。本轮没有重复此前 120 秒视频的重负载测试。

本次身份是显式本地 principal，持久层为 MemoryRepository/MemoryStorage，队列为
本地适配器；Rekognition 入口为明确的空结果 fixture。两个库素材来自同一 COCO
图片及其裁剪，属于近重复来源，查询还复用了这些字节。它证明代码与真实本机模型
能够连通，不能证明 AWS Rekognition 混合链路、Cognito/Oracle 鉴权、个体宠物准确率、
多物种精度或部署性能。

质量失败仍保留：正确猫描述最高 cosine 为 `0.108152`，低于当前 `0.2`；同图配正确
描述得分 `0.554076`，同图配无关 car 描述仍得 `0.471907` 并返回候选。图片和文字
确实都进入现有等权平均，但平均分不是身份概率。本轮未修改评分策略或阈值。

## 独立公开样本的失败基线

另选 8 张独立来源图片，来自 Abyssinian、Siamese、American Bulldog、Beagle 四个
预先固定品种，各取原始 train/test split 的一张。筛选规则在看到分数前固定，
不是将同一图片改字节当作独立样本。组成 14 个查询：4 个图片、6 个文字、4 个组合，
含 12 个品种语义正例和 2 个无匹配负例。这里的正例只表示品种语义相关，没有人工
个体身份标签，不能验收“找回同一只宠物”。

来源为 [Oxford-IIIT Pet 官方数据集](https://www.robots.ox.ac.uk/~vgg/data/pets/)，
下载来源及品种标签使用 [timm 镜像](https://huggingface.co/datasets/timm/oxford-iiit-pet)，
固定 revision `089695c834a7deb60505b7cc506672db1c31a6aa`。官方和镜像列明
CC-BY-SA 4.0；本地每个 asset 的 metadata 保存来源、许可、原始 image ID 和 SHA-256。
原始照片、完整向量和原始日志仅保留在忽略的 `tmp/`，本报告不复制这些文件。

实际入口为 **DINO-only 检测后接 SigLIP2**，未调用真实 AWS Rekognition：

| 已标注输入 | 本次模型结果 | 对评估的影响 |
| --- | --- | --- |
| 4 张猫 | 全部检为 fox，均无 pet crop/features | `NO_PET_FEATURES`，图库不完整 |
| 4 张狗 | 全部检为 dog，各 1 个 pet crop | 不能据此忽略失败的猫或声称全部检索通过 |

评估器调用当前生产 `Workers._query_result` 评分。图库特征缺失时，不悄悄丢掉失败
媒体后计算一个看似更好的排名；14 个 case 均保守记录失败，报告
`status=feature_failures`、退出码 1。失败保留在分母中；负例的“0 个假阳性”也不能
被理解为负例通过，因为这两个查询同样因图库失败而无法正常评分。
这不是实际 AWS Rekognition+DINO 混合模型评估，也没有确认个体宠物准确率。

已有离线评估器的 19 项回归通过，包括版本、维度、非有限/零向量、来源泄漏、
图库缺失、异常保留和查询模式检查。评估器测试通过不代表模型基线通过。后续需要
独立人工标注的正负样本、分离的校准/保留集，再评估检测和各模态排序；不能只把当前
猫样本调到通过。个体身份模式还要求独立来源及人工身份标注，当前 semantic-smoke
不满足这些条件。

## 本轮三项修复

| 修复 | 原问题 | 当前行为与验证 |
| --- | --- | --- |
| 日期与分页 | 整秒 `00Z` 与 `00.500000Z` 字符串比较导致漏查、越界及倒序错误 | 新写入统一 UTC 六位微秒；新旧记录按实际时间比较，边界包含相等时刻；相同瞬间按 mediaId 排序。11 项回归通过 |
| 上传源对象恢复 | 仍有效的签名 PUT 在删除/发布后重放，可能重建 incoming 对象；HEAD 404 还可能隐藏非当前版本 | 终态源对象可由迟到事件及定期清理恢复；删除精确 key 的全部版本，单项失败保留并继续其他项。11 项回归通过 |
| Cognito JWKS 缓存 | 已过期的 JWKS 缓存刷新失败，后续节流请求可能继续使用旧缓存 | 过期后刷新失败则拒绝认证；并发/节流重试也不能借旧缓存放行。有效 warm cache 与正常换 key 路径保留；新增 6 项本地回归通过 |

日期新游标带 `orderVersion=2`。旧游标的字符串顺序可能已经漏序，不能安全地按新
顺序续页，因此明确返回 `CURSOR_EXPIRED` / 409 并要求重新查询。旧媒体记录正常
参与新查询，无需数据迁移。倒置 from/to 返回空结果，没有新增 422 契约。

源对象清理只针对 READY/CANCELLED/DELETED 上传任务，保护有效 producer lease、
仍被媒体引用的旧原文件，以及供显式重试使用的 FAILED 输入。保留任务的 source
引用，每轮都能再次清理由同一有效签名 URL 重放的对象；不会将一次成功标成永久
无需再清理。`checked/cleaned/failed` 是操作处理计数，**不是对象数或删除字节数**；
幂等清扫一个已不存在的合格源对象仍可能计为成功操作。

Cognito 缓存修复属于推理服务代码和本地签名 fixture 测试；当前没有 Oracle 主机，
尚未部署到实际 Oracle 接收端。上述三项的原始失败及修后结果均保留。

## 计划与实现尚未对齐

| 项目 | 原计划目标 | 当前代码 | 处理状态 |
| --- | --- | --- | --- |
| 浏览器上传签名 URL | 600 秒 | 900 秒 | 尚未对齐；没有通过改写计划掩盖差异 |
| query 总期限 | 创建后 15 分钟 | `cleanupDeadline` / `resultExpiresAt` 为创建后 3600 秒 | 尚未对齐，待产品与实现共同决定 |

3600 秒是未完成/遗留查询的到期边界，不能写成所有查询素材固定保留一小时。
成功和永久失败立即尝试清理；取消马上进入异步清理状态，不等待一小时到期，
但活动 producer lease 结束后才由 worker/定时任务回收。可恢复失败保留输入供同任务
重试，定期清理及 TTL 只作后续兜底。
[原计划](product-and-implementation-plan.md)中的 600 秒与 15 分钟目标未改。

## 云端状态与收尾

本轮部署包 SHA-256 为
`fa8448c2a0fdff09637ed01fc575c22f0aa6861634d042c283f7968b86f7fdd2`。
已审查的 Terraform plan 为 **0 创建、7 更新、0 删除**，只涉及既有七个 Lambda 的
`source_code_hash` / `last_modified`，执行角色、权限和环境配置不变。

| 项目 | 本轮最终核验状态 |
| --- | --- |
| 七个 Lambda apply 与最终 CodeSha256/状态核验 | 0 创建/7 更新/0 删除成功；七个函数 Active/Successful，新 ZIP、v2 manifest、LabRole 一致 |
| 更新后真实课程账号两用户复测 | 新运行 29/29 通过，`emailsSent=0`、`cleanupErrors=[]`；QUEUED/推理 503/聊天 503 为正确边界，不是业务通过 |
| 真实上传源对象恢复 | 新运行 29/29 通过；既有 cleanup 两次调用，重复迟到写入清除、FAILED 保留、CANCELLED 活动租约保护与到期回收均通过 |
| 本轮临时数据清理与学生 Lab 最终结束 | 两用户测试与源对象探针的自有数据清零；Edge 实际确认 AWS Status: Terminated、Lab terminated、00:00 |
| 关闭前课程 EC2 再查 | us-east-1 / us-west-2 的 running、pending、stopping、stopped 均为 0；只读检查，未执行启停 |
| Oracle | 本轮再次只读核查未发现 RUNNING/STOPPED 主机；无实际 HTTPS 推理服务 |
| 正式应用 Lex | 个人 MCP 身份可用，Sydney/us-east-1 ListBots 均为空；此前临时 Bot 已删除，正式应用 Bot/Alias 尚未配置 |

源对象云探针先回收 DELETED 源，再用 SDK PUT 模拟迟到写入并再次回收；FAILED
源两轮保留，CANCELLED 源在有效 lease 下保留，仅把本探针自己的 lease fixture
改为过期后回收。终态任务记录保留以支持后续清扫，最后 finally 删除自己的
3 条记录、3 个 key 及其全部版本，`errors=[]`。桶版本控制前后均 Disabled，
未启用版本控制；此处 SDK PUT 不声称浏览器签名 URL 重放已经验证，真实迟到 S3
事件到 ingest 的路径仍未部署，仅有本地回归。
两次返回 `(checked, cleaned, failed)=(3,2,0)/(3,3,0)` 包含另一个既有终态任务，
这些是符合条件的操作计数，不能称作探针删除了 2/3 个文件或相应字节。

本轮没有启动前端，两个本地模型子进程均已退出、临时目录已清除。Lab 收尾
另存 `lab-ended-continued.json`：浏览器无障碍状态确认 Terminated / Lab terminated / 00:00；
`lab-ended-continued-final.png` 显示红色 AWS 指示与 00:00。没有新建资源、启动
服务器或发送邮件。此前临时开放的 Edge 扩展“允许访问文件 URL”仍待用户确认
已关闭；工具不能访问扩展设置页，不能将其恢复状态签为已验证。

前轮真实 Cognito、S3、Rekognition、通知组件和 LabRole→Google WIF 证据继续有效，
但不能补齐缺失的 Oracle 接收端、完整自动入库、正式 Lex fulfillment 或真实寻宠质量。
第二计算云仍为既有 Oracle，Google 只提供固定服务账号/WIF 身份。A$0 额外支出
限制不变，不新建主机、不启动停止的服务器、不升级账单计划。

## 复现入口与证据位置

以下是本地检查命令，不包含云部署或账号操作。已有环境不需要重新安装依赖：

```powershell
Set-Location D:\3184a2
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1
Push-Location services\inference
try { .\.venv\Scripts\python.exe -m pytest tests -q }
finally { Pop-Location }

# 只重放现有特征评分；不加载模型、不调用云。当前证据预期退出 1。
.\.venv\Scripts\python.exe scripts\evaluate_pet_quality.py `
  tmp\environment-acceptance-20261008\semantic-pet-baseline\manifest.json `
  --output tmp\environment-acceptance-20261008\semantic-pet-baseline\quality-report-replay.json
```

本机原始证据统一位于忽略目录 `tmp/environment-acceptance-20261008/`：

| 文件 | 用途 |
| --- | --- |
| `continued-final-verify.log/json`、`continued-inference-tests.log` | 最终合并结果、准确分项计数、源码 SHA-256 |
| `local-core-e2e.json/log`、`local_core_e2e.py` | 52 项真实本机功能链与明确适配器边界 |
| `local-core-real-features.json` | 现有真实向量缓存；不提交到 Git |
| `semantic-pet-baseline/sources.json`、`receipt.json` | 8 个来源/许可/摘要及逐图检测结果 |
| `semantic-pet-baseline/manifest.json`、`quality-report.json` | 固定输入及 14 个失败查询报告；原始与规范化版本分别保留 |
| `a02-a03-audit-repros.json`、`date-fix.json`、`date-*-before.log` | 原审计失败、日期修复与旧游标失败复现 |
| `upload-source-cleanup-tests.json`、`cognito-cache-fix.json` | 源对象与 JWKS 缓存回归范围 |
| `continued-plan-reviewed.json`、`continued-apply.log`、`aws-continued-packages.json` | 已审查变更范围、实际 apply 和七个函数包一致性 |
| `aws-two-user-continued.json`、`aws-upload-source-continued.json` | 本轮两用户 29 项及源对象恢复 29 项真实云检查与清理 |
| `aws-shutdown-preflight-continued.json`、`lab-ended-continued.json/png` | 关闭前 EC2 空清单、当前 Lab 结束记录；状态栏截图为 `lab-ended-continued-final.png` |

原图、向量、原始运行目录、私有 plan/state、密钥及登录缓存不复制进文档或版本库。
提交材料只引用脱敏结果、来源和可复现方法。仍需完成 Oracle 运行环境、自动处理
云链路、正式 Lex、独立模型质量评估及四人演示/报告，不能将本报告签为整份作业通过。
