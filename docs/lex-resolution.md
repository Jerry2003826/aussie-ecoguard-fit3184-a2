# Lex resolution status — verified 2026-10-08

Amazon Lex is usable in the personal AWS account. A real temporary bot was
created, built, published and invoked successfully; its bot, alias and dedicated
role were then deleted and verified absent. This resolves the personal-account
service feasibility question. The assignment's authenticated animal-search
fulfillment and application integration are still pending.

| Check | Actual result |
|---|---|
| Profile/account | ecoguard-personal / 011154879765 |
| Region | ap-southeast-2 (Sydney) |
| Read access | ListBots succeeded in Sydney and us-east-1 |
| Funding state before probe | FREE / ACTIVE, US$100 remaining credit; plan expires 2027-04-07 |
| Test resources | Unique temporary bot ecoguard-lex-probe-20261008 and isolated tagged role |
| Create/build | Bot, en_US locale, CheckConnection intent; locale reached Built |
| Publish | Version and alias reached Available |
| Runtime | Exactly one RecognizeText call: `check my connection` |
| Real response | Intent CheckConnection, state Fulfilled, `EcoGuard Lex connection verified.` |
| Cleanup | Test bot/alias and role removed; absence verified |
| Scope | No Lambda hook, Bedrock, audio, chatbot designer or application deployment |

The account plan was not upgraded. The single text request has an approximate
US$0.00075 cost using the published pricing example; this is an estimate, not a
measured bill. AWS documents Free Tier credits as eligible for Amazon Lex.
[Official Lex pricing](https://aws.amazon.com/lex/pricing/)

Local, sanitized step-by-step evidence is retained in the Git-ignored files
`tmp/lex-personal-readonly-check-20261008.json` and
`tmp/lex-personal-live-probe-20261008.json`. They record actual service responses,
resource ownership checks and cleanup. No reusable bot/alias is left deployed.

The four-person plan assigns formal Lex work to D, with C responsible for
runtime identity/region/permissions and A supplying the existing query and ACL
logic. D must implement species/count/color/date slots, missing-information
prompts, real searches and authenticated session isolation; B owns the chat UI.

Do not simply put personal bot IDs into the course API and assume it can call
them. The course runtime identity, personal account, region, fulfillment Lambda
and invocation permissions need a deliberate, verified integration path within
the A$0 extra-spend constraint. This small connectivity bot does not demonstrate
the required SearchAnimals/FindPet behavior or complete LEX 01–02 acceptance.

The following earlier Learner Lab failure evidence remains valid for that
account; using the personal account has not changed course-managed policies.

Verified on 2026-10-07 in the AWS Academy Learner Lab:

- A real CreateBot request was denied for lex:CreateBot.
- ListBots was denied in us-east-1 and us-west-2.
- The supplied LabRole did not trust lexv2.amazonaws.com.
- The role inventory contained no existing Lex role.
- IAM simulation denied the course caller CreateBot, CreateRole and PutRolePolicy,
  and denied LabRole CreateBot/RecognizeText. Simulation is diagnostic, not a
  substitute for a real service request.

Reinstalling CLI or changing local configuration cannot grant those course
permissions. The teaching-team question remains a prepared draft in
lex-permission-request.md; nothing has been sent. If the team needs a course-only
integration, it must obtain a suitable teaching environment or verified course
permissions. The student Lab remains ended; this personal probe did not restart it.

References: [AWS Lex IAM troubleshooting](https://docs.aws.amazon.com/lexv2/latest/dg/security_iam_troubleshoot.html),
[Lex V2 service roles](https://docs.aws.amazon.com/lexv2/latest/dg/migrating-to-service-roles.html).
