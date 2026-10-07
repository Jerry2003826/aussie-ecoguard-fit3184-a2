# 手动创建真实 Amazon Lex V2

当前课程账号的 CreateBot 被拒绝，控制台和 CLI 使用相同 IAM 权限。
以下代码适用于教学团队开放权限后的账号。若换 AWS 账号，先在新账号
部署本项目 AWS 环境，替换下面 fulfillment ARN；不能直接假设跨账号接线可用。

## 1. 在 PowerShell 中核对当前账号

```powershell
Set-Location D:\3184a2
$awsCmd = 'C:\Users\Jiarui Li\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe'
& $awsCmd sts get-caller-identity
& $awsCmd lexv2-models list-bots --region us-east-1
```

如果第二条命令仍为 AccessDenied，先让教学团队开放 Lex 权限。安装 CLI
或换控制台操作不会改变账号权限。

## 2. 准备一个 Lex 可以承担的角色

优先使用教学团队提供的角色 ARN。此项目现有 LabRole 没有 Lex trust，
不要填它。若你已拥有创建角色的权限，可执行：

```powershell
& $awsCmd iam create-role `
  --role-name EcoGuardLexRole `
  --assume-role-policy-document file://infra/lex/bot-trust-policy.json

$lexRoleArn = (& $awsCmd iam get-role --role-name EcoGuardLexRole `
  --query 'Role.Arn' --output text).Trim()
```

bot-trust-policy.json 只允许 lexv2.amazonaws.com 承担此角色。文本聊天的
Lambda code hook 通过后续 alias 限定的 Lambda resource policy 接线。
部署者仍需要 Lex 建模权限、对该角色的 iam:PassRole 和 Lambda
AddPermission/GetPolicy。当前 Learner Lab 缺少这些权限中的必要部分。

## 3. 一次性创建意图、槽位、版本和别名

下面脚本接受已有角色，不会修改教学账号 IAM。先打印计划：

```powershell
$lexRoleArn = '替换为已有且信任 lexv2.amazonaws.com 的角色 ARN'
$fulfillmentArn = 'arn:aws:lambda:us-east-1:360493183467:function:ecoguard3184-dev-lex_fulfillment'

.\.venv\Scripts\python.exe services/aws/tools/deploy_lex.py `
  --region us-east-1 `
  --role-arn $lexRoleArn `
  --fulfillment-arn $fulfillmentArn
```

实际部署（会创建或更新真实 Lex 资源）：

```powershell
.\.venv\Scripts\python.exe services/aws/tools/deploy_lex.py `
  --region us-east-1 `
  --role-arn $lexRoleArn `
  --fulfillment-arn $fulfillmentArn `
  --apply `
  --output tmp/lex-deployment.json
```

部署器创建 en_US locale、species/color 自定义槽位、SearchAnimals、FindPet、
EcoGuardHelp、FallbackIntent，构建 locale、发布版本与 live alias，添加只允许
这个 bot alias 调用 fulfillment Lambda 的权限。可测试语句：

- show me（应追问物种，再回答 dogs）
- show me two foxes
- show me cats from yesterday
- find my ginger cat
- help

真实 Lex API 要求 species/count/color/date 四个槽位都列入优先级，即使后三个
是 optional；只有 species 必填。自定义名称 `HelpIntent` 是 AWS 保留名称，
本项目使用 `EcoGuardHelp`。默认 FallbackIntent 不提交空 sampleUtterances 或
slotPriorities，species/color 的解析策略使用 SDK 枚举 `TopResolution`。

## 4. 将 bot 接入已部署的 API

将脚本输出的 bot/alias ID 写进 infra/aws/deployment.auto.tfvars：

```hcl
lex_bot_id = "你的 bot ID"
lex_alias_id = "你的 alias ID"
```

然后部署 Lambda 环境变量更新：

```powershell
Set-Location D:\3184a2\infra\aws
$terraformCmd = 'C:\Users\Jiarui Li\AppData\Local\Programs\Terraform\terraform.exe'
& $terraformCmd plan '-out=lex.tfplan'
& $terraformCmd apply lex.tfplan
```

通过网站登录后新建聊天会话，再发消息。直接在 Lex 控制台测试器中发消息
没有应用会话绑定，fulfillment 会提示新建会话；这是防止绕过用户权限的
设计。只有真实登录后的应用对话及结果链接通过，才能记为 Lex 验收完成。

官方权限说明：[AWS Lex IAM troubleshooting](https://docs.aws.amazon.com/lexv2/latest/dg/security_iam_troubleshoot.html)。
