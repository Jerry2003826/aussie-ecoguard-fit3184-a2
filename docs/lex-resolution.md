# Lex resolution status

Current account: AWS Academy Learner Lab, region us-east-1.

Verified on 2026-10-07:

- The actual CreateBot request was denied for lex:CreateBot.
- ListBots is denied in both us-east-1 and us-west-2.
- The supplied LabRole does not trust lexv2.amazonaws.com.
- The account role inventory contains no existing Lex role.
- IAM simulation denies the current caller CreateBot, CreateRole and PutRolePolicy;
  it also denies LabRole CreateBot and RecognizeText. Simulation is diagnostic,
  not a substitute for a real successful service request.

The current credentials cannot grant the missing permission themselves. Reinstalling
the CLI, selecting another region or changing local config does not change that.
We have not changed any course-managed IAM policy.

Two supported paths remain:

1. Inspect the student's machine-learning course 188413 for a suitable lab and
   verify its actual identity and Lex rights. This requires an accessible Edge
   session or the student starting that lab. Browser control currently times out.
2. Have the teaching team provide a Lex-capable environment or update the course
   permissions and supply a Lex-trusted runtime role. The concrete question is
   prepared in lex-permission-request.md. Sending it requires the user's explicit
   instruction; nothing has been sent.

The application API, session isolation and fulfillment implementation continue
locally. Once rights are available, deploy the real bot, build its en_US locale,
publish a version/alias, attach the alias-limited Lambda permission and configure
the API's bot/alias IDs. Only then test and mark actual Lex acceptance complete.

References: [AWS Lex IAM troubleshooting](https://docs.aws.amazon.com/lexv2/latest/dg/security_iam_troubleshoot.html),
[Lex V2 service role migration](https://docs.aws.amazon.com/lexv2/latest/dg/migrating-to-service-roles.html).
