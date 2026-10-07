# AWS runtime wiring

The deployment context is the repository root. Build `services/aws/Dockerfile`, then use
the same image with a different Lambda image command per handler. Production handlers
reject `ECO_MODE=local`. The Python import roots for local development are
`packages/domain` and `services/aws`.

## Handlers

| Function | Image command | Trigger |
| --- | --- | --- |
| API | `ecoguard_aws.handlers.api` | HTTP API `/api/v1/{proxy+}`, JWT authorizer |
| Preprocess | `ecoguard_aws.handlers.ingest` | ingest Standard SQS, batch 1, partial failures |
| Inference | `ecoguard_aws.handlers.infer` | inference SQS, batch 1, partial failures |
| Delete | `ecoguard_aws.handlers.delete` | deletion SQS, batch 1, partial failures |
| Notification | `ecoguard_aws.handlers.notify` | notification SQS, partial failures |
| Outbox | `ecoguard_aws.handlers.outbox` | scheduled each minute or filtered DynamoDB stream wakeup |
| Cleanup | `ecoguard_aws.handlers.cleanup` | scheduled each 5 minutes |
| Lex fulfillment | `ecoguard_aws.handlers.lex_fulfillment` | Lex alias code hook; resource policy only that alias |

## Environment (all configuration is read by `ecoguard_aws.config.Config`)

| Name | Meaning |
| --- | --- |
| `ECO_MODE` | `aws` for deployment; `local` only on a loopback development server |
| `ECO_REGION` | AWS region; falls back to `AWS_REGION`, then `us-east-1` |
| `ECO_MEDIA_TABLE` | String partition key `mediaId` |
| `ECO_OPERATIONS_TABLE` | String partition key `operationId`; TTL `expiresAt` |
| `ECO_INDEX_TABLE` | String partition `scopeSpecies`, string sort `observedAtMediaId` |
| `ECO_SUBSCRIPTIONS_TABLE` | String partition `ownerSub`, string sort `subscriptionId` |
| `ECO_OUTBOX_TABLE` | String partition `eventId`; TTL `expiresAt`; optional NEW_IMAGE stream |
| `ECO_MEDIA_BUCKET` | Private S3 bucket; incoming/library/, incoming/query/, derived/, library/ |
| `ECO_INGEST_QUEUE_URL` | Outbox recovery messages and S3 incoming events |
| `ECO_INFERENCE_QUEUE_URL` | Preprocessed task references, without signed URLs or JWTs |
| `ECO_DELETE_QUEUE_URL` | Deletion task references |
| `ECO_NOTIFICATION_QUEUE_URL` | Media record-version events |
| `ECO_INFERENCE_URL` | Protected Cloud Run base HTTPS URL, also Google ID-token audience |
| `ECO_GOOGLE_CREDENTIALS_FILE` | Packaged external-account WIF JSON path; no secret keys |
| `ECO_GOOGLE_INVOKER_EMAIL` | GCP target service account with Cloud Run invoker |
| `ECO_COGNITO_CLIENT_ID` | Browser app client ID |
| `ECO_COGNITO_USER_POOL_ID` | User pool ID for deployment/diagnostics |
| `ECO_WEB_URL` | Exact frontend origin/base URL for stable media links |
| `ECO_API_URL` | Exact public API base URL, without `/api/v1` |
| `ECO_MODEL_MANIFEST_VERSION` | Immutable inference manifest version; fixed per task |
| `ECO_LEX_BOT_ID` / `ECO_LEX_ALIAS_ID` | Deployed Lex V2 bot and alias |
| `ECO_LEX_LOCALE` | `en_US` unless the deployed bot uses another locale |
| `ECO_RESOURCE_PREFIX` | SNS user-topic name prefix, default `ecoguard` |
| `ECO_FFMPEG` / `ECO_FFPROBE` | Executables; image installs them on PATH |
| `ECO_PET_SCORE_THRESHOLD` | Initial 0.2; freeze only after real benchmark evaluation |

AWS credentials must come from the Lambda execution role. Do not set credentials in
the image, Terraform variables, or Lambda environment. GCP WIF must allow the exact
AWS account/role to impersonate the invoker SA using `roles/iam.workloadIdentityUser`.
The runtime exchanges its AWS identity for an STS token, then `generateIdToken`.

## Access and delivery requirements

Grant only necessary table operations (GetItem, Query, Scan, TransactWriteItems) on the
listed tables; S3 read/write/copy/delete plus ListBucket/ListBucketVersions and
DeleteObjectVersion on the dedicated bucket; queue send/receive/delete/change-visibility
on named queues; Rekognition DetectLabels to preprocess; Lex RecognizeText on only the
alias; Cognito GetUser for verified notification email; SNS create/subscribe/list/publish
restricted to `${ECO_RESOURCE_PREFIX}-user-*`. Refine per-function roles when permitted.

This first implementation scans owner gallery/maintenance/subscriptions and uses explicit
species partitions for search. It targets the assignment workload, not an unbounded media
catalog. Provision indexes and bounded maintenance batches before scaling materially.

Upload URLs: 900 seconds, signed SHA-256 and content length. End-user reads: 60 seconds.
Worker frame reads: 900 seconds, created immediately before inference. Temporary-role
credential expiry can shorten all presigned URLs. Never place signed URLs into queue state.

Use S3 `ObjectCreated:*` notifications only on `incoming/`; accept duplicate events. All
SQS handlers return `batchItemFailures`, which requires `ReportBatchItemFailures` in event
source mappings. Preprocess timeout 120s/visibility >=720s, inference 480s/>=2880s;
set batch size 1 and maximum concurrency 2. Configure DLQ redrive and alarms separately.

## Local development

Set `ECO_MODE=local`, then run `python -m ecoguard_aws.local`. Use the explicit header
`X-Ecoguard-Dev-User: alice` or `bob`. The server binds only to 127.0.0.1, stores data in
memory, and announces `development-only`. It does not simulate Cognito, inference,
Rekognition, SNS or Lex success. Missing services fail visibly. Test doubles live only
in tests and must never be used to claim cloud acceptance.

## Manual Lex deployment

`tools/deploy_lex.py` defaults to an offline plan. It accepts an **existing** role
whose trust includes `lexv2.amazonaws.com`, plus the deployed fulfillment Lambda ARN.
It never creates IAM roles or changes trust/policies. The selected caller needs
approved Lex V2 model operations, `iam:GetRole`, `iam:PassRole`, Lambda GetFunction,
GetPolicy and AddPermission. A teaching-account deny must be resolved by its
administrator; this script does not bypass it.

From the repository root in PowerShell, replace both example ARNs:

```powershell
$lexRoleArn = 'arn:aws:iam::123456789012:role/ApprovedLexRole'
$lexFulfillmentArn = 'arn:aws:lambda:us-east-1:123456789012:function:ecoguard-lex'
$lexArguments = @(
  'services/aws/tools/deploy_lex.py',
  '--region', 'us-east-1',
  '--role-arn', $lexRoleArn,
  '--fulfillment-arn', $lexFulfillmentArn,
  '--bot-name', 'ecoguard-assistant',
  '--alias-name', 'live'
)
.\.venv\Scripts\python.exe @lexArguments
# Run only after reviewing the plan and selecting the approved AWS credentials:
.\.venv\Scripts\python.exe @lexArguments --apply --output tmp/lex-deployment.json
```

Pass `--profile approved-profile` when using a named AWS profile. The tool creates
or reconciles only its `ManagedBy=AussieEcoGuard` bot, builds the `en_US` locale,
publishes a version, connects the alias to Lambda, and adds a Lambda resource-policy
permission scoped to that exact bot alias and account. It returns `ECO_LEX_BOT_ID`,
`ECO_LEX_ALIAS_ID` and `ECO_LEX_LOCALE` for the infrastructure owner to configure;
it does not overwrite any Lambda environment.

Intent names are `SearchAnimals`, `FindPet`, `EcoGuardHelp`, `FallbackIntent`; slots
are exactly `species` (required), `count`, `color`, `date` (optional). Dialog hooks
are disabled; fulfillment hooks use `ecoguard_aws.handlers.lex_fulfillment` and
server-bound authenticated chat sessions. A successful deployment does not yet
prove real `RecognizeText` behavior; test through an authenticated app chat session.

All four slots have priorities 1–4 even when optional. `show me` and `search for
animals` enter SearchAnimals without a species so Lex can elicit it. The default
fallback omits empty utterance/priority fields, and custom slot resolution uses
the SDK value `TopResolution`. These constraints were verified against real Lex.

Official API references: [alias Lambda attachment](https://docs.aws.amazon.com/lexv2/latest/dg/lambda-attach-api.html),
[immutable versions](https://docs.aws.amazon.com/lexv2/latest/APIReference/API_CreateBotVersion.html),
[slot elicitation](https://docs.aws.amazon.com/lexv2/latest/APIReference/API_SlotValueElicitationSetting.html).
