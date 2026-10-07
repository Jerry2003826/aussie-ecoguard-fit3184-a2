# Teaching-team clarification draft

Subject: FIT3184 A2 required Amazon Lex unavailable in AWS Academy Learner Lab

Our A2 application uses Amazon Cognito, S3, DynamoDB, SQS and Rekognition in the
AWS Academy Learner Lab. Amazon Lex is explicitly required by the assignment.
On 7 October 2026, both `lexv2-models list-bots` and a real
`lexv2-models create-bot` request returned AccessDeniedException, including denial
of `lex:CreateBot`. The supplied LabRole also does not trust
`lexv2.amazonaws.com`, and `AWSServiceRoleForLexV2Bots` is absent.

Additional checks found that ListBots is also denied in us-west-2, and the full
IAM role inventory has no role trusted/named for Lex. IAM policy simulation for
the current voclabs principal returned implicitDeny for CreateBot, CreateRole
and PutRolePolicy; simulation for LabRole returned implicitDeny for CreateBot
and RecognizeText. Switching region or using LabRole therefore does not provide
a confirmed permitted path. No course-managed policy has been modified.

Could you confirm the supported A2 environment for Lex, or enable the required
Lex model-building/runtime permissions and a role that Lex can assume? We need
to create/build a bot and locale, intents/slots, a bot version and alias, attach
the Lambda code hook, and use RecognizeText. We cannot replace required Lex
with a local simulated chatbot and claim it satisfies the rubric.

An additional AWS Academy Machine Learning course is available to the student;
its lab availability and effective account permissions have not yet been checked.
Please confirm whether its lab is appropriate for this assignment.

This is a prepared draft only. No message has been sent.

Reference: [AWS Lex IAM troubleshooting](https://docs.aws.amazon.com/lexv2/latest/dg/security_iam_troubleshoot.html).
