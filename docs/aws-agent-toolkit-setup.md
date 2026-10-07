# Agent Toolkit for AWS 本机配置记录

2026-10-08（悉尼时间）已按 [AWS 官方安装说明](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md)
完成本机配置。个人账号使用独立 profile `ecoguard-personal`；课程账号的
`default` profile 保留，Learner Lab 继续保持结束状态。本次没有创建、启动
或修改项目云资源，没有升级账单计划。

| 项目 | 实际结果 |
|---|---|
| AWS CLI | 已有版本 2.37.10，满足安装器要求；目录已加入持久 User PATH |
| uv / uvx | 已有 uv 0.11.3，可启动官方代理 |
| 个人账号 | `011154879765`，CLI 浏览器授权成功；STS 身份与目标账号一致 |
| profile | `ecoguard-personal`；默认区域 `ap-southeast-2`（悉尼） |
| Toolkit 区域 | 技能目录和 AWS MCP 端点使用官方安装要求的 `us-east-1` |
| AWS 技能 | CLI 清单与实际文件均为 24 个；四个安装目录内容校验一致 |
| 技能目录查询 | `list-available-skills` 成功，当前返回 114 个可用技能 |
| Codex 配置 | `codex mcp get aws-mcp --json` 可解析，服务器已启用 |
| 实际 MCP 连接 | initialize 成功，协议 `2025-06-18`，代理版本 1.7.0 |
| 工具与执行 | tools/list 返回 8 个工具；AWS 文档查询成功，`isError=false` |
| 测试进程 | 验证代理已正常退出，无遗留测试服务 |

安装器为 Codex、Claude、Cursor、Gemini 和 OpenCode 生成了 `aws-mcp`
配置。每个配置仅追加个人 profile 的环境变量：

```text
AWS_MCP_PROXY_PROFILES=ecoguard-personal
```

保留安装器生成的 command、args、端点和其他设置。OpenCode 使用
`environment` 字段，其余四个工具使用 `env`。原有 Codex `node_repl` 配置
已与安装前备份比较并完整保留。OpenClaw 已安装技能；安装器按设计没有
为它自动配置 MCP，本次没有初始化新的 OpenClaw 工作区。

Codex、Gemini、OpenCode 共用本机 `.agents/skills`；Claude、Cursor 和
OpenClaw 使用各自技能目录。项目规则分别保存于 `AGENTS.md`、
`CLAUDE.md`、`GEMINI.md` 和 `.cursor/rules/aws.mdc`。四份文件完整包含
[官方 advanced AWS rules](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/rules/aws-agent-rules.md)，
包括 Secret Safety，使用固定 BEGIN/END 标记。规则之前保留了项目范围、
A$0 额外自费上限、账号分离和现有 Terraform 的约束。

新开一次 Codex 会话以加载新增 MCP 和技能配置。如果新会话仍未显示
`aws-mcp`，关闭并重新打开 Codex。新 PowerShell 窗口会读取更新后的 PATH。
可用下面的只读命令检查当前登录；临时会话失效后重新运行浏览器登录：

```powershell
$awsExe = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe"
& $awsExe sts get-caller-identity --profile ecoguard-personal --region ap-southeast-2
# 仅在会话过期时重新授权
& $awsExe login --profile ecoguard-personal --region ap-southeast-2
```

凭证与登录缓存保存在用户目录，没有写入仓库。安装前配置备份位于
`C:\Users\Jiarui Li\.aws\agent-toolkit-backups\20261007T132903Z`；安装后、
绑定 profile 前的备份位于同目录下的
`20261007T133403Z-before-profile-binding`。AWS profile 配置还有独立的
`config.before-agent-toolkit-20261007T132448Z` 备份。

本机详细验证记录保存于 Git 忽略的 `tmp/`：

- `aws-toolkit-skills-verified-20261008.json`
- `aws-toolkit-profile-binding-20261008.json`
- `aws-toolkit-existing-config-preserved-20261008.json`
- `aws-toolkit-catalog-20261008.json`
- `aws-toolkit-rules-installed-20261008.json`
- `aws-toolkit-mcp-smoke-20261008.json`

验证范围为 CLI 登录、技能安装、配置解析、MCP 连接和文档工具执行。
没有通过 MCP 测试个人账号的资源操作权限，没有部署 Lex，也没有完成
作业应用的云端验收。后续资源部署仍受原有预算与用户授权范围约束。
